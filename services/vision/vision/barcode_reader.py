"""Barcode reading tuned for a laptop webcam.

On 135 real 1280x720 webcam frames (EAN-13 ~2 px per bar, slightly blurred), zxing on the whole frame read 1;
OpenCV's detector alone 23; OpenCV locate -> rotate upright, upscale x2, sharpen -> zxing read 45 (of 76 frames
where a barcode was found at all; the rest were motion-blurred). The fallback, Tesseract OCR of the digits printed
under the bars, runs in a background thread (see ocr_ean13).
"""
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import zxingcpp

_detector = cv2.barcode.BarcodeDetector()
# Only what the pipeline uses: EAN-13 on items, Code128 on shipping labels (box_id). On real frames every EAN-8 /
# UPC-A that OpenCV returned (18 of 56 reads) was a misread of printed text or patterns.
_FORMATS = (zxingcpp.BarcodeFormat.EAN13, zxingcpp.BarcodeFormat.Code128)

# Tesseract is a separate program: winget install -e --id UB-Mannheim.TesseractOCR (or set TESSERACT_CMD in .env)
TESSERACT_CMD = (os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
                 or r"C:\Program Files\Tesseract-OCR\tesseract.exe")
# Tesseract's OpenMP threads would fight the YOLO models for every core and freeze the camera window (measured with
# the previous OCR: 0.1-0.2 s -> 7.5 s per frame); one thread is enough for a small crop.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
OCR_SCALE = 3  # digits are ~10 px tall on the webcam; Tesseract wants ~30 px


def ean13_ok(s):
    """13 digits with a valid check digit: rejects partial misreads before they reach the checklist."""
    if len(s) != 13 or not s.isdigit():
        return False
    return (10 - sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(s[:12])) % 10) % 10 == int(s[12])


def _upright(gray, quad, scale=2, pad=0.15):
    """Cut the barcode out along its own rotation, long side horizontal, upscaled for thin bars."""
    (cx, cy), (w, h), a = cv2.minAreaRect(quad.astype(np.float32))
    if w < h:
        w, h, a = h, w, a + 90
    w, h = w * (1 + 2 * pad), h * (1 + 2 * pad)  # keep the quiet zone around the bars
    m = cv2.getRotationMatrix2D((cx, cy), a, scale)
    m[:, 2] += (w * scale / 2 - cx, h * scale / 2 - cy)
    return cv2.warpAffine(gray, m, (int(w * scale), int(h * scale)), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def read_barcodes(img, undecoded=None):
    """[(text, (cx, cy))] for every barcode decoded in a BGR image, centers in img coordinates.
    Pass a list as `undecoded` to collect the quads of barcodes that were found but not decoded (for OCR)."""
    _, texts, _, quads = _detector.detectAndDecodeWithType(img)
    if quads is None:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    out = []
    for text, quad in zip(texts, quads):
        if not ean13_ok(text):  # OpenCV failed or misread: retry on a clean upright crop with zxing
            crop = _upright(gray, quad)
            crop = cv2.addWeighted(crop, 2.0, cv2.GaussianBlur(crop, (0, 0), 3), -1.0, 0)  # unsharp mask
            text = next((b.text for b in zxingcpp.read_barcodes(crop, formats=_FORMATS)
                         if b.format == zxingcpp.BarcodeFormat.Code128 or ean13_ok(b.text)), "")
        if text:
            out.append((text, tuple(map(float, quad.mean(0)))))
        elif undecoded is not None:
            undecoded.append(quad)
    return out


def ean13_from_text_boxes(boxes, texts):
    """The printed EAN-13 among OCR text boxes, or "".

    Only a run of digit-only boxes on one text line counts (the digits are printed as "8 935106 211321", often split
    into several boxes); the run must be exactly 13 digits with a valid check digit. Digits are never joined across
    lines or out of a longer number: on real frames that turned a shipping label ("X55277605433" over
    "VN5D6W5C4U2R") into the checksum-valid "5527760543356".
    """
    lines = []  # [y center, height, [texts left to right]]
    for box, text in sorted(zip(boxes, texts), key=lambda bt: np.asarray(bt[0])[:, 0].min()):
        box = np.asarray(box)
        yc, height = box[:, 1].mean(), np.ptp(box[:, 1])
        line = next((ln for ln in lines if abs(ln[0] - yc) < max(ln[1], height) / 2), None)
        if line is None:
            lines.append([yc, height, [text]])
        else:
            line[2].append(text)
    for _, _, line_texts in lines:
        run = ""
        for text in line_texts + [None]:  # None closes the last run
            s = None if text is None else text.replace(" ", "").replace("-", "")
            if s and s.isdigit():
                run += s
                continue
            if len(run) == 13 and ean13_ok(run):
                return run
            run = ""
    return ""


def ocr_crop(img, quad):
    """The bars plus the digits printed under them, grey and upscaled for OCR."""
    x, y, w, h = cv2.boundingRect(quad.astype(np.int32))
    crop = img[max(y - h // 2, 0):y + h + h // 2, max(x - w // 4, 0):x + w + w // 4]
    if not crop.size:
        return crop
    return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), None, fx=OCR_SCALE, fy=OCR_SCALE,
                      interpolation=cv2.INTER_CUBIC)


def ocr_ean13(img, quad, psm=11):
    """Fallback: Tesseract OCR of the digits printed under the bars; a checksum-valid EAN-13, else ""."""
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    crop = ocr_crop(img, quad)
    if not crop.size:
        return ""
    # No digit whitelist: letters must stay letters, so a label line like "VN5D6W5C4U2R" breaks a digit run
    # instead of being forced into digits.
    d = pytesseract.image_to_data(crop, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    words = [(i, t.strip()) for i, t in enumerate(d["text"]) if t.strip()]
    boxes = [[[d["left"][i], d["top"][i]], [d["left"][i] + d["width"][i], d["top"][i]],
              [d["left"][i] + d["width"][i], d["top"][i] + d["height"][i]],
              [d["left"][i], d["top"][i] + d["height"][i]]] for i, _ in words]
    return ean13_from_text_boxes(boxes, [t for _, t in words])


_ocr_pool = ThreadPoolExecutor(max_workers=1)  # one OCR at a time; the camera loop never waits for it


def ocr_ean13_async(img, quad):
    return _ocr_pool.submit(ocr_ean13, img.copy(), quad.copy())


if __name__ == "__main__":  # self-check: a small, rotated, blurred EAN-13 like the webcam sees
    import io

    import barcode
    from barcode.writer import ImageWriter

    assert ean13_ok("8935106211321") and not ean13_ok("8935106211322") and not ean13_ok("79902113")

    def box(x, y, w=40, h=20):  # OCR text box corners
        return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
    # real OCR outputs from webcam frames (box positions simplified)
    assert ean13_from_text_boxes([box(0, 100, 10), box(15, 100, 120)], ["8", "935106211321"]) == "8935106211321"
    assert ean13_from_text_boxes([box(0, 0), box(0, 100, 10), box(15, 100, 120)],
                                 ["Ngày", "8", "935106-211321"]) == "8935106211321"
    assert ean13_from_text_boxes([box(15, 100, 120), box(0, 100, 10)], ["935106211321", "8"]) == "8935106211321"
    assert ean13_from_text_boxes([box(0, 0, 120), box(0, 40), box(0, 45, 120)],
                                 ["X55277605433", "Mā vân don", "VN5D6W5C4U2R"]) == ""  # label: was a false read
    assert ean13_from_text_boxes([box(0, 0, 10), box(0, 40, 120)], ["8", "935106211321"]) == ""  # different lines
    assert ean13_from_text_boxes([box(0, 0, 200)], ["189351062113210"]) == ""  # 13 valid digits inside a longer number
    assert ean13_from_text_boxes([box(0, 0, 120)], ["8935106211322"]) == ""  # bad check digit
    assert ean13_from_text_boxes([], []) == ""

    buf = io.BytesIO()
    barcode.get("ean13", "893510621132", writer=ImageWriter()).write(buf)
    code = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    code = cv2.resize(code, None, fx=0.45, fy=0.45, interpolation=cv2.INTER_AREA)  # ~2 px per bar
    frame = np.full((720, 1280, 3), 230, np.uint8)
    y, x = 300, 500
    frame[y:y + code.shape[0], x:x + code.shape[1]] = code
    frame = cv2.warpAffine(frame, cv2.getRotationMatrix2D((640, 360), 25, 1), (1280, 720), borderValue=(230,) * 3)
    frame = cv2.GaussianBlur(frame, (0, 0), 0.8)
    found = read_barcodes(frame)
    assert [t for t, _ in found] == ["8935106211321"], found
    # Code128 shipping-label id (box_id) must still read; it has no EAN checksum
    buf = io.BytesIO()
    barcode.get("code128", "X55277605433", writer=ImageWriter()).write(buf)
    label = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    label = cv2.resize(label, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)
    frame = np.full((720, 1280, 3), 230, np.uint8)
    frame[200:200 + label.shape[0], 300:300 + label.shape[1]] = label
    found = read_barcodes(frame)
    assert [t for t, _ in found] == ["X55277605433"], found
    # Tesseract: the digits under a clean, upright barcode (only if Tesseract is installed)
    if os.path.exists(TESSERACT_CMD):
        frame = np.full((720, 1280, 3), 255, np.uint8)
        buf = io.BytesIO()
        barcode.get("ean13", "893510621132", writer=ImageWriter()).write(buf)
        code = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
        code = cv2.resize(code, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        frame[200:200 + code.shape[0], 300:300 + code.shape[1]] = code
        _, _, _, quads = _detector.detectAndDecodeWithType(frame)
        assert quads is not None and ocr_ean13(frame, quads[0]) == "8935106211321", "tesseract OCR"
    else:
        print(f"skipped Tesseract check: {TESSERACT_CMD} not found")
    print("ok")
