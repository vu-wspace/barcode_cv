"""BE for the packing inspection: checklists in, inspection results out, stored in PostgreSQL.

    uv run uvicorn server:app --app-dir src/barcode_cv --host 0.0.0.0 --port 8000
    uv run python src/barcode_cv/server.py seed [mock_checklist.json]   # load checklists from a JSON file
    uv run python src/barcode_cv/server.py check                         # self-check against DATABASE_URL

Needs DATABASE_URL in .env, e.g. postgresql://postgres:<password>@localhost:5432/barcode_cv
"""
import base64
import binascii
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, field_validator

load_dotenv()
SCHEMA = Path(__file__).with_name("schema.sql")
MAX_IMAGE_BYTES = 10 * 1024 * 1024

Code = Annotated[str, Field(min_length=1, max_length=64)]
Counts = dict[Code, Annotated[int, Field(ge=0)]]


class ChecklistItem(BaseModel):
    item_id: Code
    barcodes: list[Code] = Field(default_factory=list, max_length=50)
    quantity: int = Field(ge=1, le=100_000)


class Inspection(BaseModel):
    """What be_client.send_result posts. Unknown fields (e.g. the client's local image path) are ignored."""
    inspection_id: UUID
    box_id: Code
    result: Literal["OK", "NG"]
    seal_status: Literal["sealed", "not_sealed", "n/a"]
    qty: int = Field(ge=0)
    items: Counts = {}
    missing: Counts = {}
    extra: Counts = {}
    wrong: Counts = {}
    unread: int = Field(0, ge=0)
    visual_count: int = Field(0, ge=0)
    time: datetime
    image_jpeg_b64: str | None = Field(None, max_length=MAX_IMAGE_BYTES * 4 // 3 + 4)

    @field_validator("time")
    @classmethod
    def _aware(cls, v):  # a naive time would be read in the DB server's timezone
        if v.tzinfo is None:
            raise ValueError("time must include a UTC offset, e.g. 2026-11-10T09:30:00+07:00")
        return v


pool = ConnectionPool(os.getenv("DATABASE_URL", ""), open=False, min_size=1, max_size=10)


@asynccontextmanager
async def lifespan(_):
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is not set (add it to .env)")
    pool.open(wait=True, timeout=10)
    with pool.connection() as conn:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
    yield
    pool.close()


app = FastAPI(title="Packing inspection BE", lifespan=lifespan)


@app.get("/boxes/{box_id}/checklist")
def get_checklist(box_id: str) -> list[ChecklistItem]:
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM boxes WHERE box_id = %s", (box_id,)).fetchone() is None:
            raise HTTPException(404, "unknown box")
        rows = conn.execute("SELECT item_id, barcodes, quantity FROM checklist_items WHERE box_id = %s "
                            "ORDER BY item_id", (box_id,)).fetchall()
    return [ChecklistItem(item_id=i, barcodes=b, quantity=q) for i, b, q in rows]


@app.put("/boxes/{box_id}/checklist", status_code=204)
def put_checklist(box_id: Code, items: list[ChecklistItem]):
    """Create or replace the whole checklist of a box (one transaction)."""
    if len({i.item_id for i in items}) != len(items):
        raise HTTPException(422, "duplicate item_id")
    with pool.connection() as conn, conn.transaction():
        upsert_checklist(conn, box_id, items)


def upsert_checklist(conn, box_id, items):
    conn.execute("INSERT INTO boxes (box_id) VALUES (%s) ON CONFLICT DO NOTHING", (box_id,))
    conn.execute("DELETE FROM checklist_items WHERE box_id = %s", (box_id,))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO checklist_items (box_id, item_id, barcodes, quantity) VALUES (%s, %s, %s, %s)",
                        [(box_id, i.item_id, i.barcodes, i.quantity) for i in items])


@app.post("/inspections", status_code=201)
def post_inspection(r: Inspection):
    image = None
    if r.image_jpeg_b64:
        try:
            image = base64.b64decode(r.image_jpeg_b64, validate=True)
        except binascii.Error:
            raise HTTPException(422, "image_jpeg_b64 is not valid base64")
        if not image.startswith(b"\xff\xd8"):
            raise HTTPException(422, "image is not a JPEG")
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO inspections (id, box_id, result, seal_status, qty, items, missing, extra, wrong, unread,"
            " visual_count, inspected_at, image) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING RETURNING id",
            (r.inspection_id, r.box_id, r.result, r.seal_status, r.qty, Jsonb(r.items), Jsonb(r.missing),
             Jsonb(r.extra), Jsonb(r.wrong), r.unread, r.visual_count, r.time, image)).fetchone()
    return {"id": str(r.inspection_id), "duplicate": row is None}  # duplicate = a retry of a stored result


@app.get("/inspections")
def list_inspections(box_id: str | None = None, limit: int = Query(50, ge=1, le=500)):
    """Latest results first, without the image (fetch it from /inspections/{id}/image)."""
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, box_id, result, seal_status, qty, items, missing, extra, wrong, unread, visual_count,"
            " inspected_at, received_at, image IS NOT NULL AS has_image FROM inspections"
            " WHERE %(box)s::text IS NULL OR box_id = %(box)s ORDER BY inspected_at DESC LIMIT %(n)s",
            {"box": box_id, "n": limit})
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


@app.get("/inspections/{inspection_id}/image")
def get_image(inspection_id: UUID):
    with pool.connection() as conn:
        row = conn.execute("SELECT image FROM inspections WHERE id = %s", (inspection_id,)).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(404, "no image")
    return Response(bytes(row[0]), media_type="image/jpeg")


def seed(path):
    """Load {box_id: [{item_id, barcodes, quantity}]} (same format as mock_checklist.json) into the DB."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pool.open(wait=True, timeout=10)
    with pool.connection() as conn, conn.transaction():
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        for box_id, items in data.items():
            upsert_checklist(conn, box_id, [ChecklistItem(**i) for i in items])
    print(f"seeded {len(data)} box(es) from {path}")


def check():
    """End-to-end self-check through the HTTP API against DATABASE_URL; removes its own rows."""
    import uuid

    import cv2
    import numpy as np
    from fastapi.testclient import TestClient

    box = f"CHECK-{uuid.uuid4().hex[:8]}"
    jpeg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    body = {"inspection_id": str(uuid.uuid4()), "box_id": box, "result": "NG", "seal_status": "n/a", "qty": 1,
            "items": {"A": 1}, "missing": {"A": 1}, "time": "2026-11-10T09:30:00+07:00",
            "image_jpeg_b64": base64.b64encode(jpeg).decode(), "image": "results/x.jpg"}
    with TestClient(app) as c:
        try:
            assert c.get(f"/boxes/{box}/checklist").status_code == 404
            items = [{"item_id": "A", "barcodes": ["8935106211321", "8935106211322"], "quantity": 2}]
            assert c.put(f"/boxes/{box}/checklist", json=items).status_code == 204
            assert c.get(f"/boxes/{box}/checklist").json() == items
            assert c.put(f"/boxes/{box}/checklist", json=[{"item_id": "B", "quantity": 1}]).status_code == 204
            assert c.get(f"/boxes/{box}/checklist").json() == [{"item_id": "B", "barcodes": [], "quantity": 1}]
            assert c.put(f"/boxes/{box}/checklist", json=items * 2).status_code == 422  # duplicate item_id
            assert c.put(f"/boxes/{box}/checklist", json=[{"item_id": "A", "quantity": 0}]).status_code == 422

            r = c.post("/inspections", json=body)
            assert r.status_code == 201 and r.json()["duplicate"] is False, r.text
            assert c.post("/inspections", json=body).json()["duplicate"] is True  # client retry: stored once
            assert c.post("/inspections", json={**body, "time": "2026-11-10T09:30:00"}).status_code == 422
            assert c.post("/inspections", json={**body, "result": "MAYBE"}).status_code == 422
            assert c.post("/inspections", json={**body, "image_jpeg_b64": "@@@"}).status_code == 422
            rows = c.get("/inspections", params={"box_id": box}).json()
            assert len(rows) == 1 and rows[0]["missing"] == {"A": 1} and rows[0]["has_image"], rows
            assert c.get(f"/inspections/{body['inspection_id']}/image").content == jpeg
            assert c.get(f"/inspections/{uuid.uuid4()}/image").status_code == 404
        finally:
            with pool.connection() as conn:
                conn.execute("DELETE FROM inspections WHERE box_id = %s", (box,))
                conn.execute("DELETE FROM boxes WHERE box_id = %s", (box,))
    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        seed(sys.argv[2] if len(sys.argv) > 2 else "mock_checklist.json")
    elif cmd == "check":
        check()
    else:
        print(__doc__)
