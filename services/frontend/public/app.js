// Operator UI: video from the vision service, state polled from it, "Xong" sent to it (all through nginx /vision/).
const VISION = "/vision";
const $ = (id) => document.getElementById(id);

function listItem(left, right, cls) {
  const li = document.createElement("li");
  if (cls) li.className = cls;
  const a = document.createElement("span");
  a.textContent = left; // textContent: barcode text comes from the camera, never parsed as HTML
  li.append(a);
  if (right !== undefined) {
    const b = document.createElement("span");
    b.textContent = right;
    li.append(b);
  }
  return li;
}

function render(s) {
  const idle = s.stage === "idle";
  $("box").textContent = idle ? "Đưa hộp vào bàn" : `HỘP: ${s.box_id || "chưa đọc được mã"}`;
  $("stage").textContent = idle ? "" : s.stage === "sealing" ? "OK → dán seal rồi bấm Xong" : "Đang xếp hàng";
  $("stage").className = s.stage === "sealing" ? "warn" : "muted";

  const status = $("checklist-status");
  status.textContent = "";
  if (s.box_id && s.loading) { status.textContent = "Đang tải checklist…"; status.className = "muted"; }
  else if (s.box_id && !s.has_checklist) { status.textContent = "Không có đơn cho hộp này"; status.className = "bad"; }

  $("checklist").replaceChildren(...(s.checklist || []).map((e) => {
    const cls = e.scanned === e.quantity ? "ok" : e.scanned > e.quantity ? "bad" : "";
    return listItem(`${e.scanned === e.quantity ? "✓" : "•"} ${e.item_id}`, `${e.scanned}/${e.quantity}`, cls);
  }));
  $("not-in-order").replaceChildren(...Object.entries(s.not_in_order || {})
    .map(([code, n]) => listItem(`✗ Không có trong đơn: ${code}`, `x${n}`)));

  $("counts").textContent = idle ? "" : `Thấy ${s.visual_count} item · quét được ${s.scanned_count}`;
  $("unread").textContent = s.unread_hint ? `${s.unread_hint} item chưa đọc được mã → xoay mã` : "";

  $("ng").hidden = !s.ng;
  if (s.ng) {
    const reasons = [];
    for (const [label, part] of [["Thiếu", s.ng.missing], ["Thừa", s.ng.extra], ["Sai", s.ng.wrong]]) {
      for (const [item, n] of Object.entries(part)) reasons.push(listItem(`${label}: ${item}`, `x${n}`));
    }
    if (s.ng.unread) reasons.push(listItem(`Chưa quét được: ${s.ng.unread} item`));
    if (s.ng.no_checklist) reasons.push(listItem("Không có checklist từ BE"));
    $("ng-reasons").replaceChildren(...reasons);
  }
  $("message").textContent = s.message || "";
  $("status").textContent = `${s.fps ?? 0} FPS`;
}

async function poll() {
  try {
    const r = await fetch(`${VISION}/state`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    render(await r.json());
  } catch (e) {
    $("status").textContent = `Mất kết nối vision service (${e.message})`;
  }
  setTimeout(poll, 300);
}

async function xong() {
  const btn = $("xong");
  btn.disabled = true;
  try {
    const r = await fetch(`${VISION}/xong`, { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch (e) {
    $("message").textContent = `Không gửi được Xong: ${e.message}`;
  } finally {
    setTimeout(() => { btn.disabled = false; }, 500); // no double press
  }
}

// MJPEG stream; reconnect when the vision service restarts
function connectStream() {
  const img = $("stream");
  img.onload = () => { $("video-error").hidden = true; };
  img.onerror = () => { $("video-error").hidden = false; setTimeout(connectStream, 2000); };
  img.src = `${VISION}/stream.mjpg?t=${Date.now()}`;
}

// --- Browser camera (vision runs with CAMERA_SOURCE=browser) -----------------------------------------------
// The page asks for camera permission (getUserMedia) and sends ~10 JPEG frames/s to the vision service over a
// WebSocket. getUserMedia only works on a secure page: http://localhost or HTTPS.
const SEND_EVERY_MS = 100;
const JPEG_QUALITY = 0.92; // measured: no loss in barcode reads vs the original frames
const cam = { stream: null, ws: null, timer: null, sending: false };

function camStatus(text, cls = "muted") {
  $("cam-status").textContent = text;
  $("cam-status").className = cls;
}

const PERMISSION_HELP = {
  NotAllowedError: "Quyền camera đang bị chặn. Bấm biểu tượng camera / ổ khoá trên thanh địa chỉ → Cho phép, rồi bấm \"Bật camera\".",
  SecurityError: "Quyền camera đang bị chặn bởi trình duyệt hoặc chính sách của máy.",
  NotFoundError: "Không tìm thấy camera nào. Cắm camera rồi bấm \"Bật camera\".",
  NotReadableError: "Camera đang được ứng dụng khác dùng (Zoom, Teams, vision chạy trực tiếp…). Tắt ứng dụng đó rồi thử lại.",
  OverconstrainedError: "Camera đã chọn không còn. Chọn camera khác.",
};

function stopCamera() {
  clearInterval(cam.timer);
  cam.timer = null;
  if (cam.ws) { cam.ws.onclose = null; cam.ws.close(); cam.ws = null; }
  if (cam.stream) { cam.stream.getTracks().forEach((t) => t.stop()); cam.stream = null; }
}

async function listCameras(currentId) {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const select = $("cam-select");
  select.replaceChildren(...devices.map((d, i) => {
    const o = document.createElement("option");
    o.value = d.deviceId;
    o.textContent = d.label || `Camera ${i + 1}`; // labels are only visible after permission is granted
    o.selected = d.deviceId === currentId;
    return o;
  }));
  select.hidden = devices.length < 2;
}

function openSocket() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${VISION}/camera`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => camStatus("Camera đang gửi hình cho vision.", "ok");
  ws.onclose = (e) => {
    cam.ws = null;
    if (e.code === 4000) { // another tab now sends the camera
      stopCamera();
      $("cam-btn").textContent = "Bật camera";
      camStatus("Một tab khác đang dùng camera. Bấm \"Bật camera\" để dùng tab này.", "warn");
      return;
    }
    if (cam.stream) {
      camStatus("Mất kết nối tới vision, đang kết nối lại…", "warn");
      setTimeout(() => { if (cam.stream && !cam.ws) cam.ws = openSocket(); }, 2000);
    }
  };
  return ws;
}

function sendFrame(video, canvas) {
  const ws = cam.ws;
  // skip while the previous frame is still on its way: vision always uses the newest frame anyway
  if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > 0 || cam.sending || !video.videoWidth) return;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  cam.sending = true;
  canvas.toBlob((blob) => {
    cam.sending = false;
    if (blob && ws.readyState === WebSocket.OPEN) ws.send(blob);
  }, "image/jpeg", JPEG_QUALITY);
}

async function startCamera(deviceId) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    camStatus("Trình duyệt chỉ cho dùng camera trên trang an toàn: mở qua http://localhost:8080 hoặc HTTPS.", "bad");
    return;
  }
  stopCamera();
  camStatus("Đang xin quyền camera… (bấm \"Cho phép\" trên hộp thoại của trình duyệt)");
  try {
    cam.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      // ask for the highest resolution: barcode bars are ~2 px wide at 720p already
      video: { deviceId: deviceId ? { exact: deviceId } : undefined, width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
  } catch (e) {
    camStatus(PERMISSION_HELP[e.name] || `Không mở được camera: ${e.name} ${e.message}`, "bad");
    $("cam-btn").textContent = "Thử lại";
    return;
  }
  const track = cam.stream.getVideoTracks()[0];
  const id = track.getSettings().deviceId;
  try { localStorage.setItem("cameraId", id); } catch { /* private window: just not remembered */ }
  track.onended = () => { // unplugged, or permission revoked while running
    stopCamera();
    $("cam-btn").textContent = "Bật camera";
    camStatus("Camera đã dừng (bị rút ra hoặc bị thu hồi quyền).", "bad");
  };
  await listCameras(id);

  const video = $("local");
  video.srcObject = cam.stream;
  await video.play();
  const canvas = document.createElement("canvas");
  cam.ws = openSocket();
  cam.timer = setInterval(() => sendFrame(video, canvas), SEND_EVERY_MS);
  const s = track.getSettings();
  $("cam-btn").textContent = "Tắt camera";
  camStatus(`Camera ${s.width}×${s.height}, đang kết nối tới vision…`);
}

async function setupBrowserCamera() {
  try {
    const r = await fetch(`${VISION}/config`, { cache: "no-store" });
    if (!r.ok || !(await r.json()).browser_camera) return; // vision reads its own camera
  } catch {
    setTimeout(setupBrowserCamera, 3000); // vision not up yet
    return;
  }
  $("camera-ctl").hidden = false;
  $("cam-btn").addEventListener("click", () => {
    if (cam.stream) { stopCamera(); $("cam-btn").textContent = "Bật camera"; camStatus("Camera đã tắt."); }
    else startCamera($("cam-select").value || undefined);
  });
  $("cam-select").addEventListener("change", () => startCamera($("cam-select").value));
  let saved = null;
  try { saved = localStorage.getItem("cameraId"); } catch { /* storage blocked */ }
  // start right away when permission was granted before; otherwise wait for the button (the browser's prompt
  // should follow a click, and a denied prompt cannot be shown again without one)
  const perm = await navigator.permissions?.query({ name: "camera" }).catch(() => null);
  if (perm?.state === "granted") startCamera(saved || undefined);
  else camStatus("Bấm \"Bật camera\" và cho phép trình duyệt dùng camera.");
  if (perm) perm.onchange = () => { if (perm.state === "granted" && !cam.stream) startCamera(saved || undefined); };
}

$("xong").addEventListener("click", xong);
document.addEventListener("keydown", (e) => {
  const inControl = ["xong", "cam-btn", "cam-select"].includes(document.activeElement?.id);
  if ((e.key === "Enter" || e.key === " ") && !e.repeat && !inControl) {
    e.preventDefault();
    xong();
  }
});
connectStream();
poll();
setupBrowserCamera();
