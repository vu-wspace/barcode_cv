"""Vision service: camera loop in a background thread + HTTP API for the web UI.

    GET  /state       operator state (JSON), polled by the UI
    GET  /stream.mjpg annotated camera video (MJPEG)
    POST /xong        the operator's "Xong" button
    GET  /config      {"browser_camera": bool}: should the UI send its own camera?
    WS   /camera      camera frames (binary JPEG) from the UI, when CAMERA_SOURCE=browser
    GET  /health

    python -m vision            (env: CAMERA_SOURCE, BE_URL, VISION_PORT; see README)
"""
import os
import threading
import traceback

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from vision import pipeline

shared = pipeline.Shared()
app = FastAPI(title="Vision service")
MAX_FRAME_BYTES = 5 * 1024 * 1024  # a 1080p JPEG is ~0.3 MB
_sender = None  # the one WebSocket allowed to send frames (latest UI tab wins)


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


@app.get("/config")
def config():
    return {"browser_camera": pipeline.CAMERA_SOURCE == "browser"}


@app.websocket("/camera")
async def camera(ws: WebSocket):
    """Frames from the UI's camera. One sender at a time: frames from two tabs (two cameras, or the same one twice)
    interleaved would scramble tracking, so a new tab takes over and the previous one is closed."""
    global _sender
    await ws.accept()
    if pipeline.CAMERA_SOURCE != "browser":
        await ws.close(code=1008, reason="CAMERA_SOURCE is not browser")
        return
    previous, _sender = _sender, ws
    if previous is not None:
        try:
            await previous.close(code=4000, reason="another tab took over the camera")
        except RuntimeError:  # already closed
            pass
    try:
        while True:
            data = await ws.receive_bytes()
            if ws is not _sender:
                break
            if len(data) <= MAX_FRAME_BYTES and data[:2] == b"\xff\xd8":  # JPEG only; decoded in the camera loop
                shared.put_browser_frame(data)
    except WebSocketDisconnect:
        pass
    finally:
        if _sender is ws:
            _sender = None


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
