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

$("xong").addEventListener("click", xong);
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && !e.repeat && document.activeElement !== $("xong")) {
    e.preventDefault();
    xong();
  }
});
connectStream();
poll();
