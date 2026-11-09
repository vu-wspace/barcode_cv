"""Vision inspection at the packing table (pipeline_kiem_tra_dong_goi.png).

Camera -> Box (OBB) + Track ID -> box barcode -> BE checklist (Expected)
       -> Items (segmentation inside the box) + Track ID -> item barcode -> Scanned; Visual count
       -> Xong: Compare -> NG (back to packing) | OK -> Seal -> Xong -> result to BE
"""
import time
from collections import Counter, deque
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLOE

import ui
from barcode_reader import ean13_ok, ocr_ean13_async, read_barcodes
from be_client import fetch_checklist, send_result
from inspection import BoxSession

# Box: detect + OBB only. YOLOE gives a mask internally; only its outline is used, to get the rotation.
# shortcut: zero-shot is less accurate than a trained model, swap in a trained OBB model (train_obb.py) once it scores well.
model = YOLOE("yoloe-26s-seg.pt")
model.set_classes(["box"], model.get_text_pe(["box"]))

# Items: segment what is inside boxes; prompt-free = ~4.5k built-in classes, no prompts needed.
inner = YOLOE("yoloe-26s-seg-pf.pt")
# Classes that are about writing, not objects; excluded so only physical objects are detected.
TEXT_LABELS = {"alphabet", "font", "handwriting", "inscription", "label", "letter", "letter logo", "lettering",
               "logo", "car logo", "manuscript", "number", "number icon", "print", "printed page", "qr code",
               "quote", "signature", "text", "text message", "writing", "sticker", "sign", "signage"}
# Outside the boxes is blacked out before segmenting; without this the black area itself is segmented as "darkness".
BLACKOUT_LABELS = {"black", "dark", "darkness", "night", "midnight", "shadow"}
OBJECT_IDS = [i for i, n in inner.names.items() if n not in TEXT_LABELS | BLACKOUT_LABELS]
MAX_ITEM_FRAC = 0.6  # an item mask covering more of its box than this is the box itself (e.g. "storage box")

# Seal: zero-shot presence of tape / seal on the box, OBB from its mask.
# shortcut: presence only, no intact/torn classification; train a seal OBB + cls model once there are labeled photos.
seal_model = YOLOE("yoloe-26s-seg.pt")
SEAL_PROMPTS = ["packing tape", "seal sticker"]
seal_model.set_classes(SEAL_PROMPTS, seal_model.get_text_pe(SEAL_PROMPTS))

# Box, on 100 real webcam frames with a box (+ 79 with a hand-held medicine pack, 180 empty scenes), "s" model:
#   imgsz 480 conf .15 -> 53/100, 55 ms | 640 .12 -> 74/100, 0 false boxes, 90 ms | 480 .05 -> 80/100 but 4 packs as box.
#   Larger models: m 48/100 (125 ms), l 78/100 (151 ms); 5 prompts instead of "box": no gain.
# Items: 131 ms @640 -> 79 ms @480, run every 3rd frame.
BOX_IMGSZ, BOX_CONF, ITEM_IMGSZ, ITEM_EVERY = 640, 0.12, 480, 3
BOX_TRACKER = str(Path(__file__).with_name("box_tracker.yaml"))
BOX_VOTES = 2  # reads of the same box barcode (within BOX_WINDOW_S) before it is trusted as box_id
BOX_WINDOW_S = 3.0
# The zero-shot box detector misses the box in ~half the frames; a box stays "live" this long after its last
# detection, so items / barcodes / seal on it are not lost on the frames in between.
HOLD_S = 1.0
ROTATE_HINT_S = 1.5  # an item unread this long gets the "rotate the code" hint
SEAL_WINDOW = 15  # recent frames voting on seal presence


def obb_from_mask(polygon):
    """Oriented box (4 corner points, int) = smallest rotated rectangle around a mask outline."""
    return cv2.boxPoints(cv2.minAreaRect(polygon.astype(np.float32))).astype(np.int32)


def inside(polygon, pt):
    return cv2.pointPolygonTest(np.asarray(polygon, np.float32), pt, False) >= 0


def ids(r):  # tracker IDs; None until a track is confirmed
    return r.boxes.id.int().tolist() if r.boxes.id is not None else [None] * len(r.boxes)


def crop_box(img, obb):
    x, y, w, h = cv2.boundingRect(obb)
    x, y = max(x, 0), max(y, 0)  # OBB corners can fall outside the frame
    return img[y:y + h, x:x + w], np.array([x, y])


def segment_items(img, boxes):
    """Item track id + mask outline for items inside boxes; tracked on the full frame so IDs stay stable."""
    in_boxes = np.zeros(img.shape[:2], np.uint8)
    cv2.fillPoly(in_boxes, [obb for _, obb in boxes], 255)
    ri = inner.track(cv2.bitwise_and(img, img, mask=in_boxes), classes=OBJECT_IDS, imgsz=ITEM_IMGSZ,
                     persist=True, verbose=False)[0]
    keep, items = [], []
    for i, (tid, p) in enumerate(zip(ids(ri), ri.masks.xy if ri.masks is not None else [])):
        if len(p) < 3:
            continue
        obb = next((o for _, o in boxes if inside(o, tuple(map(float, p.mean(0))))), None)
        if obb is None or cv2.contourArea(p.astype(np.float32)) > MAX_ITEM_FRAC * cv2.contourArea(obb):
            continue  # not in any box, or it is the box itself
        keep.append(i)
        if tid is not None:
            items.append((tid, p))
    return ri[keep], items


def detect_seals(img, obb):
    crop, off = crop_box(img, obb)
    r = seal_model.predict(crop, imgsz=480, verbose=False)[0]
    seals = [obb_from_mask(p + off) for p in (r.masks.xy if r.masks is not None else []) if len(p) >= 3]
    return [s for s in seals if inside(obb, tuple(map(float, s.mean(0))))]


def assign(session, box_votes, items, live, now, text, c):
    """Where a barcode read on the full frame belongs (one box at a time on the packing table):
    on an item's mask -> that item's code (valid EAN-13 only); a non-EAN-13 (shipping-label Code128) anywhere,
    or any code on a live box but not on an item -> box barcode vote. A loose EAN-13 outside boxes is ignored."""
    item_tid = next((t for t, p in items if inside(p, c)), None)
    if item_tid is not None:
        if session is None or not ean13_ok(text):
            return False
        if not session.items[item_tid][text]:  # print each code once per item, not every frame
            print(f"[scan] box {session.box_id or '?'}  item #{item_tid}: {text}")
        session.read_item(item_tid, text)
        return True
    if ean13_ok(text) and not any(inside(o, c) for _, o in live):
        return False  # e.g. an item held outside the box
    if not any(code == text for _, code in box_votes):
        print(f"[scan] box barcode: {text}")
    box_votes.append((now, text))
    return True


def box_code(box_votes, now, done_ids):
    """Box barcode with >= BOX_VOTES reads in the last BOX_WINDOW_S; recent-only so a new box can take over."""
    while box_votes and now - box_votes[0][0] > BOX_WINDOW_S:
        box_votes.popleft()
    code, n = (Counter(c for _, c in box_votes if c not in done_ids).most_common(1) or [(None, 0)])[0]
    return code if n >= BOX_VOTES else None


def report(r):
    print(f"\n=== BOX {r['box_id']}: {r['result']}  seal: {r['seal_status']} ===")
    for item, n in r["items"].items():
        print(f"    {item}  x{n}")
    for label in ("missing", "extra", "wrong"):
        for item, n in r[label].items():
            print(f"    {label}: {item} x{n}")
    print(f"    visual count: {r['visual_count']}, unread: {r['unread']}  -> {r['image']}\n")


def finish(session, cmp, seal_status, frame):
    """Kết quả: box_id, items, qty, result, seal_status, image -> stored locally + sent to BE."""
    result = {"box_id": session.box_id, "items": cmp["scanned"], "qty": sum(cmp["scanned"].values()),
              "result": cmp["result"], "missing": cmp["missing"], "extra": cmp["extra"], "wrong": cmp["wrong"],
              "unread": cmp["unread"], "visual_count": cmp["visual_count"], "seal_status": seal_status,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    report(send_result(result, cv2.imencode(".jpg", frame)[1].tobytes()))


if __name__ == "__main__":  # guarded so train_obb.py can import model / obb_from_mask
    session, done_ids = None, set()  # one box at a time on the packing table
    box_votes = deque()  # (time, box barcode) reads; see box_code
    held = {}  # box track id -> (OBB, time last detected)
    checklist_job = ocr_job = None  # (future, ...) running in the background
    seal_votes = deque(maxlen=SEAL_WINDOW)
    ri, items, k, message, clicks = None, [], 0, "", []

    # Own capture instead of source=0: Ultralytics opens the webcam at 640x480, too coarse to decode barcodes.
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)  # driver clamps to the camera's max (1280x720 on this laptop)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    cv2.namedWindow(ui.WINDOW)
    cv2.setMouseCallback(ui.WINDOW, lambda ev, x, y, *_: clicks.append((x, y)) if ev == cv2.EVENT_LBUTTONDOWN else None)
    while True:  # press q to stop
        ok, img = cap.read()
        if not ok:
            break
        now, k = time.time(), k + 1
        r = model.track(img, persist=True, conf=BOX_CONF, imgsz=BOX_IMGSZ, tracker=BOX_TRACKER, verbose=False)[0]
        detected = {tid: obb_from_mask(p) for tid, p in zip(ids(r), r.masks.xy if r.masks is not None else [])
                    if tid is not None and len(p) >= 3}  # minAreaRect needs a real outline
        held.update({tid: (obb, now) for tid, obb in detected.items()})
        held = {tid: v for tid, v in held.items() if now - v[1] <= HOLD_S}
        boxes = [(tid, obb) for tid, (obb, _) in held.items()]  # detected now or within HOLD_S
        if boxes and session is None:
            session = BoxSession()

        # Items + Visual count (only while packing; the model is skipped on 2 of 3 frames for speed)
        if not boxes or session is None or session.state != "packing":
            ri, items = None, []
        elif k % ITEM_EVERY == 0:
            ri, items = segment_items(img, boxes)
            for item_tid, _ in items:
                session.see_item(item_tid, now)

        # Barcodes on the full frame (~12 ms), not only inside detected boxes: the detector misses the box in
        # ~half the frames, which threw away 49 of 59 decoded barcodes. Unreadable ones go to the OCR fallback.
        reads, missed = [], []
        for text, c in read_barcodes(img, missed):
            if assign(session, box_votes, items, boxes, now, text, c):
                reads.append((text, c))
        if ocr_job and ocr_job[0].done():
            fut, c = ocr_job
            ocr_job = None
            if fut.result():
                assign(session, box_votes, items, boxes, now, fut.result(), c)
        if ocr_job is None:
            for quad in missed:
                c = tuple(map(float, quad.mean(0)))
                item_tid = next((t for t, p in items if inside(p, c)), None)
                # Items only: box labels are Code128 with letters, which a digits-only OCR can never read right
                # (on real frames it turned "X55277605433" into the checksum-valid "1552776054330").
                if session is not None and item_tid is not None and not session.items[item_tid]:
                    ocr_job = (ocr_ean13_async(img, quad), c)
                    break

        # Box barcode -> box_id -> checklist from BE (once per box)
        code = box_code(box_votes, now, done_ids)
        if session is not None and code is not None and code != session.box_id:
            if session.box_id is not None:
                print(f"[warn] box {session.box_id} left without Xong; now inspecting {code}")
                session = BoxSession()
            session.box_id, session.loading = code, True
            checklist_job = (fetch_checklist(code), code)
        if checklist_job and checklist_job[0].done():
            fut, code = checklist_job
            checklist_job = None
            if session is not None and session.box_id == code:
                session.loading = False
                try:
                    session.expected = fut.result()
                except Exception as e:  # BE down: the operator sees it, Compare stays NG
                    message = f"Lỗi tải checklist: {e}"

        # Seal (after OK)
        seals = []
        if session is not None and session.state == "sealing" and boxes:
            for _, obb in boxes:
                seals += detect_seals(img, obb)
            seal_votes.append(bool(seals))

        # Draw
        frame = img.copy()
        if ri is not None:  # item masks only (no boxes, labels, conf); already filtered to inside boxes
            frame = ri.plot(img=frame, boxes=False, labels=False, conf=False)
        for tid, obb in boxes:  # green = detected this frame, grey = held from a recent frame
            color = (0, 255, 0) if tid in detected else (160, 160, 160)
            cv2.polylines(frame, [obb], True, color, 2)
            cv2.putText(frame, (session and session.box_id) or f"#{tid}", tuple(map(int, obb[0])),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        for s in seals:
            cv2.polylines(frame, [s], True, (255, 128, 0), 2)
        for text, (cx, cy) in reads:  # decoded codes, written where the barcode is
            cv2.putText(frame, text, (int(cx) - 80, int(cy)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        view, button = ui.render(frame, session, now, message, ROTATE_HINT_S)
        cv2.imshow(ui.WINDOW, view)
        key = cv2.waitKey(1)
        if key == ord("q"):
            break

        # Xong (Enter / Space / click): Compare -> NG | OK -> seal -> Xong -> result
        if key in (13, 32) or any(ui.hit(button, p) for p in clicks):
            if session is None or session.box_id is None:
                message = "Chưa đọc được mã hộp"
            elif session.state == "packing":
                cmp = session.last_compare = session.compare()
                if cmp["result"] == "NG":
                    finish(session, cmp, "n/a", frame)
                    message = "NG → quay lại xếp hàng, rồi bấm Xong"
                else:
                    session.state = "sealing"
                    seal_votes.clear()
                    message = "OK → dán seal rồi bấm Xong"
            else:
                seal = "sealed" if seal_votes and sum(seal_votes) * 2 >= len(seal_votes) else "not_sealed"
                finish(session, session.last_compare, seal, frame)  # items are hidden under the lid now
                done_ids.add(session.box_id)
                box_votes.clear()
                message = f"Đã gửi {session.box_id}: OK, seal {seal}"
                session = None
        clicks.clear()

    cap.release()
    if session is not None and session.box_id:
        print(f"[warn] box {session.box_id} not finished (Xong not pressed); no result sent")
