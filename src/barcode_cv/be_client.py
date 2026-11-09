"""API client to the BE (server.py).

    GET  {BE_URL}/boxes/{box_id}/checklist -> [{"item_id", "barcodes": [...], "quantity"}], 404 = unknown box
    POST {BE_URL}/inspections               <- result JSON with "inspection_id" (+ "image_jpeg_b64")
Without BE_URL in .env, the checklist comes from mock_checklist.json and results are only stored locally.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
BE_URL = os.getenv("BE_URL", "").rstrip("/")
MOCK = Path("mock_checklist.json")
OUTBOX = Path("results")
_pool = ThreadPoolExecutor(max_workers=2)  # network never blocks the camera loop


def fetch_checklist(box_id):
    """Future -> checklist list, or None when the BE does not know this box."""
    return _pool.submit(_fetch, box_id)


def _fetch(box_id):
    if not BE_URL:
        return json.loads(MOCK.read_text(encoding="utf-8")).get(box_id) if MOCK.exists() else None
    url = f"{BE_URL}/boxes/{urllib.parse.quote(box_id, safe='')}/checklist"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def send_result(result, jpeg):
    """Store locally first (never lose a result), then POST in the background with retries."""
    OUTBOX.mkdir(exist_ok=True)
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{result['box_id']}"
    (OUTBOX / f"{stem}.jpg").write_bytes(jpeg)
    # inspection_id: the BE stores a retried POST only once
    record = {**result, "inspection_id": str(uuid.uuid4()), "image": str(OUTBOX / f"{stem}.jpg")}
    with open(OUTBOX / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if BE_URL:
        _pool.submit(_post, {**record, "image_jpeg_b64": base64.b64encode(jpeg).decode()})
    return record


def _post(body, tries=3):
    data = json.dumps(body).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{BE_URL}/inspections", data=data,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).close()
            return
        except urllib.error.HTTPError as e:
            if e.code < 500:  # rejected (bad data): retrying cannot help
                print(f"[be] POST rejected {e.code}: {e.read().decode(errors='replace')[:300]}")
                break
            print(f"[be] POST failed ({i + 1}/{tries}): {e}")
            time.sleep(2 ** i)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[be] POST failed ({i + 1}/{tries}): {e}")
            time.sleep(2 ** i)
    print(f"[be] giving up; result kept in {OUTBOX / 'results.jsonl'}")
