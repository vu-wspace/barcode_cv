"""Operator UI: camera view + side panel with the live checklist, warnings and the Xong button."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

WINDOW = "Kiem tra dong goi"
PANEL_W = 460
GREEN, ORANGE, RED, GREY, WHITE = (60, 200, 90), (255, 165, 0), (235, 70, 70), (150, 150, 150), (240, 240, 240)
try:  # OpenCV fonts have no Vietnamese diacritics; Arial does
    FONT, BIG = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20), ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 26)
except OSError:
    FONT = BIG = ImageFont.load_default()


def render(frame, session, now, message, rotate_after):
    """Returns (camera view + panel as one BGR image, Xong button rect (x1, y1, x2, y2) in that image)."""
    h = frame.shape[0]
    panel = Image.new("RGB", (PANEL_W, h), (28, 28, 32))
    d = ImageDraw.Draw(panel)
    y = 16

    def line(text, color=WHITE, font=FONT, gap=30):
        nonlocal y
        d.text((20, y), text, fill=color, font=font)
        y += gap

    if session is None:
        line("Đưa hộp vào bàn", GREY, BIG, 40)
    else:
        line(f"HỘP: {session.box_id or 'chưa đọc được mã'}", WHITE, BIG, 40)
        line({"packing": "Đang xếp hàng", "sealing": "OK → dán seal rồi bấm Xong"}[session.state],
             ORANGE if session.state == "sealing" else GREY)
        y += 8
        got = session.scanned(now)  # items still in the box
        if session.box_id and session.loading:
            line("Đang tải checklist…", GREY)
        elif session.box_id and session.expected is None:
            line("Không có đơn cho hộp này", RED)
        for e in session.expected or []:
            n, q = got[e["item_id"]], e["quantity"]
            line(f"{'√' if n == q else '•'} {e['item_id']}", GREEN if n == q else (RED if n > q else WHITE), gap=0)
            d.text((PANEL_W - 90, y), f"{n}/{q}", fill=GREEN if n == q else (RED if n > q else ORANGE), font=FONT)
            y += 30
        known = {e["item_id"] for e in session.expected or []}
        for code, n in got.items():
            if code not in known and session.expected is not None:
                line(f"× Không có trong đơn: {code} x{n}", RED)
        y += 8
        line(f"Thấy {len(session.present(now))} item · quét được {sum(got.values())}", GREY)
        unread = session.unread(now, rotate_after)
        if unread:  # one line, however many: a long list would run over the Xong button
            line(f"{len(unread)} item chưa đọc được mã → xoay mã", ORANGE)
        c = session.last_compare
        if c and c["result"] == "NG" and session.state == "packing":
            y += 8
            line("NG → quay lại xếp hàng", RED, BIG, 36)
            for label, part in (("Thiếu", c["missing"]), ("Thừa", c["extra"]), ("Sai", c["wrong"])):
                for item, n in part.items():
                    line(f"{label}: {item} x{n}", RED)
            if c["unread"]:
                line(f"Chưa quét được: {c['unread']} item", RED)
            if c["no_checklist"]:
                line("Không có checklist từ BE", RED)
    if message:
        d.text((20, h - 140), message, fill=WHITE, font=FONT)

    bx1, by1, bx2, by2 = 20, h - 100, PANEL_W - 20, h - 24
    d.rounded_rectangle((bx1, by1, bx2, by2), 12, fill=(40, 120, 220))
    d.text(((bx1 + bx2) / 2, (by1 + by2) / 2), "XONG  (Enter)", fill=WHITE, font=BIG, anchor="mm")

    view = np.hstack([frame, cv2.cvtColor(np.asarray(panel), cv2.COLOR_RGB2BGR)])
    w = frame.shape[1]
    return view, (w + bx1, by1, w + bx2, by2)


def hit(rect, pt):
    x1, y1, x2, y2 = rect
    return x1 <= pt[0] <= x2 and y1 <= pt[1] <= y2
