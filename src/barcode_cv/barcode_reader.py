"""Barcode reading tuned for a laptop webcam.

On 135 real 1280x720 webcam frames (EAN-13 ~2 px per bar, slightly blurred), zxing on the whole frame read 1;
OpenCV's detector alone 23; OpenCV locate -> rotate upright, upscale x2, sharpen -> zxing read 45 (of 76 frames
where a barcode was found at all; the rest were motion-blurred). OCR of the printed digits, the fallback, recovered
5 of the 35 still-unread barcodes with no wrong code, but costs ~1.1 s, so it runs in a background thread.
"""
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import zxingcpp

_detector = cv2.barcode.BarcodeDetector()
# Only what the pipeline uses: EAN-13 on items, Code128 on shipping labels (box_id). On real frames every EAN-8 /
# UPC-A that OpenCV returned (18 of 56 reads) was a misread of printed text or patterns.
_FORMATS = (zxingcpp.BarcodeFormat.EAN13, zxingcpp.BarcodeFormat.Code128)


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


_ocr = None
_ocr_pool = ThreadPoolExecutor(max_workers=1)  # one OCR at a time; the camera loop never waits for it


def ocr_ean13(img, quad):
    """Fallback: OCR the digits printed under the bars; only a checksum-valid EAN-13 is returned, else ""."""
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR  # heavy import, only when the fallback is first needed

        _ocr = RapidOCR()
    x, y, w, h = cv2.boundingRect(quad.astype(np.int32))
    crop = img[max(y - h // 2, 0):y + h + h // 2, max(x - w // 4, 0):x + w + w // 4]  # bars + digits below
    if not crop.size:
        return ""
    digits = "".join(c for c in "".join(_ocr(crop).txts or []) if c.isdigit())
    return next((digits[i:i + 13] for i in range(len(digits) - 12) if ean13_ok(digits[i:i + 13])), "")


def ocr_ean13_async(img, quad):
    return _ocr_pool.submit(ocr_ean13, img.copy(), quad.copy())


if __name__ == "__main__":  # self-check: a small, rotated, blurred EAN-13 like the webcam sees
    import io

    import barcode
    from barcode.writer import ImageWriter

    assert ean13_ok("8935106211321") and not ean13_ok("8935106211322") and not ean13_ok("79902113")
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
    print("ok")
