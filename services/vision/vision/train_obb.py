"""Build a "box" OBB dataset from the webcam, then fine-tune YOLO26-OBB on it.

    uv run python -m vision.train_obb collect   # SPACE = save frame (only when green boxes are right), q = quit
    uv run python -m vision.train_obb train
"""
import sys
from pathlib import Path

import cv2
from ultralytics import YOLO, YOLOE

from vision.inspection import obb_from_mask

DATA = Path("datasets/box_obb")


def save_sample(img, obbs, idx):
    """Write one image + YOLO OBB label (class x1 y1 ... x4 y4, normalized). Every 5th sample goes to val."""
    split = "val" if idx % 5 == 4 else "train"
    for d in ("images", "labels"):
        (DATA / d / split).mkdir(parents=True, exist_ok=True)
    h, w = img.shape[:2]
    cv2.imwrite(str(DATA / "images" / split / f"{idx:05d}.jpg"), img)
    lines = ["0 " + " ".join(f"{min(max(x / w, 0), 1):.6f} {min(max(y / h, 0), 1):.6f}" for x, y in obb) for obb in obbs]
    (DATA / "labels" / split / f"{idx:05d}.txt").write_text("\n".join(lines))


def collect():
    # Pseudo-labels from the zero-shot box detector; you are the reviewer: only save frames where every box is right.
    # Same model, settings and 1280x720 capture as yolo_model.py, so training images look like the real input.
    # (Not imported from yolo_model: that would also load the item and seal models.)
    model = YOLOE("yoloe-26s-seg.pt")
    model.set_classes(["box"], model.get_text_pe(["box"]))
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)  # driver clamps to the camera's max
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    idx = len(list(DATA.glob("images/*/*.jpg")))  # resume numbering across sessions
    while True:
        ok, img = cap.read()
        if not ok:
            break
        r = model.predict(img, conf=0.12, imgsz=640, verbose=False)[0]  # = BOX_CONF, BOX_IMGSZ in yolo_model.py
        obbs = [obb_from_mask(p) for p in (r.masks.xy if r.masks is not None else []) if len(p) >= 3]
        frame = img.copy()
        cv2.polylines(frame, obbs, True, (0, 255, 0), 2)
        cv2.putText(frame, f"saved {idx} | SPACE save, q quit", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        cv2.imshow("collect", frame)
        key = cv2.waitKey(1)
        if key == ord(" "):
            save_sample(img, obbs, idx)  # empty obbs = background image, teaches "no box here"
            idx += 1
        elif key == ord("q"):
            break
    cap.release()


def train(epochs=50):
    n_val = len(list(DATA.glob("images/val/*.jpg")))
    assert n_val, f"no val images in {DATA}; run collect first (need ≥5 saved frames)"
    yaml = DATA / "data.yaml"
    yaml.write_text(f"path: {DATA.resolve().as_posix()}\ntrain: images/train\nval: images/val\nnames:\n  0: box\n")
    # n model: this laptop trains on CPU; best weights land in runs/obb/<name>/weights/best.pt
    YOLO("yolo26n-obb.pt").train(data=str(yaml), epochs=epochs, imgsz=640, batch=8)


if __name__ == "__main__":
    {"collect": collect, "train": train}[sys.argv[1] if len(sys.argv) > 1 else "collect"]()
