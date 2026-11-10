# barcode-cv — Kiểm tra đóng gói bằng camera

Hệ thống kiểm tra đóng gói tại bàn, đặt trước bước seal. Camera nhận diện **hộp** và đọc **mã hộp**, rồi lấy checklist đơn hàng từ backend. Hệ thống segment các **item trong hộp**, đọc **barcode từng item** và so sánh với checklist khi nhân viên bấm **Xong**. Nếu OK, hệ thống kiểm tra **seal**, sau đó gửi kết quả về backend.

## Kiến trúc

```
                    ┌──────────── frontend (nginx :8080) ────────────┐
 trình duyệt ──────►│  /          web UI nhân viên (HTML + JS)        │
                    │  /vision/*  ──► vision  :8090                   │
                    │  /api/*     ──► backend :8000                   │
                    └─────────────────────────────────────────────────┘
 camera ──► vision (Python: YOLO, barcode, OCR) ──HTTP──► backend (FastAPI) ──► db (PostgreSQL)
```

| Service | Thư mục | Việc |
|---|---|---|
| **frontend** | `services/frontend` | Web UI nhân viên: video trực tiếp có khung hộp và mask item, checklist realtime, cảnh báo, nút Xong. nginx phục vụ trang và làm reverse proxy tới hai service còn lại. |
| **vision** | `services/vision` | Đọc camera, chạy YOLO, đọc barcode và OCR, so sánh, kiểm tra seal. Cung cấp `GET /stream.mjpg`, `GET /state`, `POST /xong`. Lấy checklist và gửi kết quả qua API của backend. |
| **backend** | `services/backend` | FastAPI + PostgreSQL: lưu checklist của hộp và kết quả kiểm tra (kèm ảnh). |
| **db** | (image `postgres:18`) | Dữ liệu nằm trong volume `pgdata`. |

## Chạy bằng Docker

1. Tạo `.env` từ `.env.example` và đặt `POSTGRES_PASSWORD`.
2. Chọn cách chạy vision:

   **a) Vision trong Docker** (Linux, hoặc camera IP):
   ```bash
   # .env: VISION_URL=http://vision:8090, CAMERA_SOURCE_DOCKER=rtsp://... (hoặc 0 + bỏ comment devices trong docker-compose.yml)
   docker compose --profile vision up -d --build
   ```

   **b) Vision chạy trực tiếp trên máy** (Windows/macOS với webcam laptop). Docker Desktop không chuyển được webcam vào container.
   ```bash
   # .env: VISION_URL=http://host.docker.internal:8090, CAMERA_SOURCE=0, BE_URL=http://localhost:8080/api
   docker compose up -d --build
   cd services/vision && uv sync && uv run python -m vision
   ```
3. Nạp checklist mẫu rồi tự kiểm tra backend:
   ```bash
   docker compose exec backend python -m app.server seed
   docker compose exec backend python -m app.server check
   ```
4. Mở **http://localhost:8080**. Bấm **Enter**, **Space** hoặc nút **XONG**:
   - lần 1: so sánh với checklist. NG thì xếp lại rồi bấm lại. OK thì dán seal,
   - lần 2: kiểm tra seal và gửi kết quả.

Lần chạy đầu, vision tải trọng số model (khoảng 300 MB, gồm `yoloe-26s-seg.pt`, `yoloe-26s-seg-pf.pt`, `mobileclip2_b.ts`). Trong Docker, chúng được lưu vào volume `vision-data` nên chỉ tải một lần.

## Pipeline (vision)

```
Camera ─► Box (detect + OBB) ─► Track ID box
            │                     └─► barcode hộp ─► API (1 lần / box_id) ─► backend ─► Expected
            └─ vùng hộp ─► Items (segmentation) ─► Track ID item ─► barcode item ─► Scanned (+1 mỗi track)
                              └─► Visual count
Xong ─► Compare: Expected vs Scanned · Scanned vs Visual count
          ├─ NG: thiếu / thừa / sai / chưa quét ─► quay lại xếp hàng (kết quả NG vẫn được gửi)
          └─ OK ─► dán seal ─► Xong ─► Seal ─► Kết quả {box_id, items, qty, result, seal_status, ảnh} ─► backend
```

| Bước | Cách làm |
|---|---|
| Box | YOLOE-26 zero-shot với prompt `"box"`. OBB là hình chữ nhật xoay nhỏ nhất bao quanh mask. Tracking dùng ByteTrack (`box_tracker.yaml`). Box được giữ thêm 1 s khi detector bỏ lỡ một frame. |
| Items | YOLOE-26 prompt-free (khoảng 4.5k lớp), chạy trên cả khung hình với phần ngoài hộp tô đen. Bỏ các lớp chữ/nhãn và mask lớn bằng cả hộp. Chạy mỗi 3 frame. Khi Compare, chỉ tính item còn thấy trong hộp trong 3 s gần nhất. |
| Barcode | OpenCV tìm và đọc barcode trên cả khung hình. Nếu chưa đọc được: cắt theo góc xoay, phóng to x2, làm nét rồi đưa cho zxing. Còn thất bại thì **Tesseract** OCR dãy số in dưới vạch (chỉ cho item, chạy nền). Chỉ nhận **EAN-13 đúng checksum** (item) và **Code128** (nhãn vận đơn của hộp). Mã được chọn theo số lần đọc nhiều nhất qua các frame của mỗi track. Mã do OCR đọc chỉ được chấp nhận khi có trong checklist. |
| box_id | Mã hộp phải đọc được ít nhất 2 lần trong 3 s gần nhất. Mỗi lúc chỉ có một hộp trên bàn. Khi đã có box_id, chỉ mã nằm trên hộp đang thấy mới đổi được hộp. |
| Seal | YOLOE zero-shot (`"packing tape"`, `"seal sticker"`). Lấy mốc diện tích seal lúc compare ra OK, vì thùng thường có sẵn băng keo. Chỉ tính `sealed` khi diện tích seal tăng thêm ≥ 2% diện tích hộp. |

### Cấu hình vision (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `CAMERA_SOURCE` | `0` | Số thứ tự webcam, hoặc URL luồng camera (`rtsp://…`, `http://…`). Mất tín hiệu thì tự kết nối lại. |
| `BE_URL` | (trống) | URL backend. Để trống thì checklist lấy từ `MOCK_CHECKLIST` và kết quả chỉ lưu trên máy. |
| `VISION_PORT` | `8090` | Cổng HTTP của vision. |
| `RESULTS_DIR` | `results` | Nơi lưu bản sao mọi kết quả (`results.jsonl` + ảnh) trước khi gửi backend. |
| `MOCK_CHECKLIST` | `mock_checklist.json` | Checklist giả khi chưa có backend. |
| `TESSERACT_CMD` | tự tìm | Đường dẫn `tesseract.exe` khi chạy trực tiếp trên Windows. Cài bằng `winget install -e --id UB-Mannheim.TesseractOCR`. |

Kết quả luôn được lưu trên máy trước, sau đó mới POST lên backend. Lỗi mạng hoặc 5xx được thử lại 3 lần, còn 4xx thì không. Mỗi kết quả có `inspection_id`, nên gửi lại không tạo bản ghi trùng.

## Backend API

| Gọi (qua nginx: thêm tiền tố `/api`) | Dữ liệu |
|---|---|
| `GET /boxes/{box_id}/checklist` | `[{"item_id": "...", "barcodes": ["893..."], "quantity": 1}]`. Trả 404 nếu hộp không có đơn. |
| `PUT /boxes/{box_id}/checklist` | Tạo mới hoặc thay toàn bộ checklist của hộp. Trả 422 nếu trùng `item_id` hoặc `quantity < 1`. |
| `POST /inspections` | Kết quả do vision gửi, kèm `image_jpeg_b64`. |
| `GET /inspections?box_id=&limit=` | Danh sách kết quả, mới nhất trước. |
| `GET /inspections/{id}/image` | Ảnh JPEG lúc bấm Xong. |

Tài liệu API tương tác: `http://localhost:8080/api/docs`. Schema: `services/backend/app/schema.sql`, các bảng được tạo tự động khi backend khởi động.

**Chưa có xác thực:** chỉ mở cổng 8080 trong mạng nội bộ.

## Phát triển không dùng Docker

Mỗi service là một project uv riêng, có `pyproject.toml` và `uv.lock` riêng.

```bash
cd services/backend && uv sync && uv run uvicorn app.server:app --port 8001   # cần DATABASE_URL
cd services/vision  && uv sync && uv run python -m vision                      # cần CAMERA_SOURCE, BE_URL
```

Vision dùng **PyTorch bản CPU** (khai báo trong `services/vision/pyproject.toml`) để image Docker không kéo theo vài GB thư viện CUDA.

Tự kiểm tra (in ra `ok`):

```bash
cd services/vision  && uv run python -m vision.inspection && uv run python -m vision.barcode_reader
cd services/backend && uv run python -m app.server check
```

### Train model OBB cho hộp (tuỳ chọn)

Box detector zero-shot hiện bắt được hộp ở khoảng 74% frame (đo trên 100 frame webcam thật). Để cải thiện:

```bash
cd services/vision
uv run python -m vision.train_obb collect   # SPACE: lưu frame khi khung xanh đúng, q: thoát
uv run python -m vision.train_obb train     # fine-tune yolo26n-obb.pt -> runs/obb/train/weights/best.pt
```

Nên thu khoảng 150–300 ảnh, đủ nhiều góc, khoảng cách và ánh sáng, có cả frame không có hộp.

## Cấu trúc

```
docker-compose.yml, .env.example
services/
  frontend/  Dockerfile, default.conf.template (nginx), public/ (index.html, app.js, style.css)
  backend/   Dockerfile, pyproject.toml, uv.lock, app/ (server.py, schema.sql)
  vision/    Dockerfile, pyproject.toml, uv.lock, mock_checklist.json,
             vision/ (__main__.py: HTTP API · pipeline.py: vòng lặp camera · inspection.py: so sánh, gán mã ·
                      barcode_reader.py: OpenCV → zxing → Tesseract · be_client.py · box_tracker.yaml · train_obb.py)
```

## Giới hạn đã biết

- **Box zero-shot còn yếu:** bắt được hộp ở khoảng 74% frame. Muốn ổn định cần train model OBB.
- **Seal:** dựa vào diện tích seal tăng thêm, chưa phân loại seal còn nguyên hay rách, và chưa được kiểm chứng với seal thật.
- **Barcode trên webcam 1280×720:** mỗi vạch chỉ khoảng 2 px. Frame bị nhòe do chuyển động sẽ không đọc được, nên cầm yên khoảng nửa giây.
- **Đếm trùng:** nếu tracker đổi ID của cùng một item, item đó bị đếm 2 lần.
- **Webcam trong Docker:** chỉ dùng được trên Linux. Trên Windows/macOS, chạy vision trực tiếp hoặc dùng camera IP.
- **Tốc độ:** khoảng 7–9 FPS trên CPU khi có hộp trong khung hình.
