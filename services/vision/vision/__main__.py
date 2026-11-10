"""Vision service: camera loop in a background thread + HTTP API for the web UI.

    GET  /state       operator state (JSON), polled by the UI
    GET  /stream.mjpg annotated camera video (MJPEG)
    POST /xong        the operator's "Xong" button
    GET  /health

    python -m vision            (env: CAMERA_SOURCE, BE_URL, VISION_PORT; see README)
"""
import os
import threading
import traceback

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from vision import pipeline

shared = pipeline.Shared()
app = FastAPI(title="Vision service")


@app.get("/health")
def health():
    return {"ok": shared.jpeg is not None}  # False until the first camera frame


@app.get("/state")
def state():
    return shared.state


@app.post("/xong", status_code=202)
def xong():
    shared.xong.put(True)  # handled by the camera loop on its next frame
    return {"queued": True}


@app.get("/stream.mjpg")
def stream():
    def frames():
        last = None
        while True:
            with shared.frame_ready:
                shared.frame_ready.wait_for(lambda: shared.jpeg is not last, timeout=5)
                last = shared.jpeg
            if last is not None:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + last + b"\r\n"

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


def camera_loop():
    try:
        pipeline.run(shared)
    except BaseException:
        traceback.print_exc()
    os._exit(1)  # a dead camera loop must not leave the API serving a frozen frame; Docker restarts the service


if __name__ == "__main__":
    threading.Thread(target=camera_loop, daemon=True, name="camera").start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("VISION_PORT", "8090")))
