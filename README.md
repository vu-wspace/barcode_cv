# barcode-cv — Kiểm tra đóng gói bằng camera

Hệ thống kiểm tra đóng gói tại bàn, đặt trước bước seal. Camera nhận diện **hộp** và đọc **mã hộp**, rồi lấy checklist đơn hàng từ BE. Hệ thống segment các **item trong hộp**, đọc **barcode từng item** và so sánh với checklist khi nhân viên bấm **Xong**. Nếu OK, hệ thống kiểm tra **seal**, sau đó gửi kết quả về BE.

Repo gồm 2 phần: **camera client** (`yolo_model.py`) chạy tại bàn đóng gói và **BE** (`server.py`, FastAPI + PostgreSQL) lưu checklist và kết quả. Xem [BE](#be-fastapi--postgresql).

## Pipeline

```
Camera ─► Box (detect + OBB) ─► Track ID box
            │                     └─► barcode hộp ─► API client (1 lần / box_id) ─► BE ─► Expected [cache]
            └─ vùng hộp ─► Items (segmentation) ─► Track ID item ─► barcode item ─► Scanned (+1 mỗi track)
                              └─► Visual count
Xong ─► Compare: Expected vs Scanned · Scanned vs Visual count
          ├─ NG: thiếu / thừa / sai / chưa quét ─► quay lại xếp hàng (kết quả NG vẫn được gửi)
          └─ OK ─► dán seal ─► Xong ─► Seal ─► Kết quả {box_id, items, qty, result, seal_status, ảnh} ─► BE
```

| Bước | Cách làm |
|---|---|
| Box | YOLOE-26 zero-shot với prompt `"box"`. OBB là hình chữ nhật xoay nhỏ nhất bao quanh mask. Tracking dùng ByteTrack (`box_tracker.yaml`). Box được giữ thêm 1 s khi detector bỏ lỡ một frame. |
| Items | YOLOE-26 prompt-free (khoảng 4.5k lớp), chạy trên cả khung hình với phần ngoài hộp tô đen. Bỏ các lớp chữ/nhãn và mask lớn bằng cả hộp. Chạy mỗi 3 frame. Khi Compare, chỉ tính item còn thấy trong hộp trong 3 s gần nhất: item đã lấy ra, hoặc tay thò vào rồi rút ra, không còn được tính. |
| Barcode | OpenCV tìm và đọc barcode trên cả khung hình. Nếu chưa đọc được: cắt theo góc xoay, phóng to x2, làm nét rồi đưa cho zxing. Còn thất bại thì OCR số in dưới vạch (chỉ cho item, chạy nền). Chỉ nhận **EAN-13 đúng checksum** (item) và **Code128** (nhãn vận đơn của hộp). Mã được chọn theo số lần đọc nhiều nhất qua các frame của mỗi track. |
| box_id | Mã hộp phải đọc được ít nhất 2 lần trong 3 s gần nhất. Mỗi lúc chỉ có một hộp trên bàn. Khi đã có box_id, chỉ mã nằm trên hộp đang thấy mới đổi được hộp, nên thùng khác phía sau không thay được thùng đang làm. |
| Seal | YOLOE zero-shot (`"packing tape"`, `"seal sticker"`). Lấy mốc diện tích seal lúc compare ra OK, vì thùng thường có sẵn băng keo. Chỉ tính `sealed` khi diện tích seal tăng thêm ≥ 2% diện tích hộp. |

## Cài đặt

Yêu cầu Python ≥ 3.13 và webcam. Trên Windows, các model chạy bằng CPU.

```bash
uv sync
```

Nếu không dùng uv:

```bash
pip install --no-deps -r requirements.txt
```

Bắt buộc dùng `--no-deps`. Nếu thiếu, `rapidocr` sẽ kéo theo `opencv-python-headless`, bản này ghi đè `cv2` và làm cửa sổ camera không mở được (`cv2.namedWindow: The function is not implemented`). Với uv, việc này đã được chặn trong `pyproject.toml` (`[tool.uv] override-dependencies`).

Lần chạy đầu sẽ tự tải các file trọng số: `yoloe-26s-seg.pt`, `yoloe-26s-seg-pf.pt`, `mobileclip2_b.ts` (khoảng 240 MB, để mã hoá prompt chữ) và model OCR.

## Chạy

```bash
uv run python src/barcode_cv/yolo_model.py
```

Cửa sổ gồm hình camera và panel nhân viên. Panel hiển thị:
- mã hộp,
- checklist cập nhật realtime (`đã quét/cần`),
- item không có trong đơn,
- cảnh báo **"chưa đọc được mã → xoay mã"**,
- lý do NG.

| Phím | Tác dụng |
|---|---|
| `Enter` / `Space` / click **XONG** | Lần 1: Compare. Nếu NG thì sửa rồi bấm lại. Nếu OK thì dán seal rồi bấm lần 2 để gửi kết quả. |
| `q` | Thoát |

Terminal in ra mỗi mã khi đọc được lần đầu (`[scan] ...`) và bảng tổng kết mỗi khi có kết quả.

## BE (FastAPI + PostgreSQL)

### Chạy BE

1. Tạo database (một lần):
   ```bash
   psql -U postgres -c "CREATE DATABASE barcode_cv"
   ```
2. Thêm vào `.env`:
   ```
   DATABASE_URL=postgresql://postgres:<mật khẩu>@localhost:5432/barcode_cv
   ```
   Các bảng (`boxes`, `checklist_items`, `inspections`, xem `src/barcode_cv/schema.sql`) được tạo tự động khi server khởi động.
3. Chạy server:
   ```bash
   uv run uvicorn server:app --app-dir src/barcode_cv --host 0.0.0.0 --port 8000
   ```
   Tài liệu API tương tác ở `http://localhost:8000/docs`.
4. Nạp checklist. Có hai cách:
   - từ file JSON cùng định dạng với `mock_checklist.json`:
     ```bash
     uv run python src/barcode_cv/server.py seed mock_checklist.json
     ```
   - hoặc qua API: `PUT /boxes/{box_id}/checklist`.

Tự kiểm tra BE (chạy với `DATABASE_URL`, tự xoá dữ liệu test, in ra `ok`):

```bash
uv run python src/barcode_cv/server.py check
```

### API

| Gọi | Dữ liệu |
|---|---|
| `GET /boxes/{box_id}/checklist` | `[{"item_id": "...", "barcodes": ["893..."], "quantity": 1}]`. Trả 404 nếu hộp không có đơn. |
| `PUT /boxes/{box_id}/checklist` | Tạo mới hoặc thay toàn bộ checklist của một hộp (cùng định dạng như trên). Trả 422 nếu trùng `item_id` hoặc `quantity < 1`. |
| `POST /inspections` | Kết quả do camera gửi: `inspection_id, box_id, items, qty, result, missing, extra, wrong, unread, visual_count, seal_status, time` (có múi giờ) kèm `image_jpeg_b64`. Gửi lại cùng `inspection_id` thì không tạo bản ghi trùng (`"duplicate": true`). |
| `GET /inspections?box_id=&limit=` | Danh sách kết quả, mới nhất trước, không kèm ảnh |
| `GET /inspections/{id}/image` | Ảnh JPEG lúc bấm Xong |

### Kết nối camera với BE

Thêm vào `.env` trên máy đặt camera:

```
BE_URL=http://<host BE>:8000
```

- **Không có `BE_URL`:** checklist lấy từ `mock_checklist.json` (map `box_id → checklist`), và kết quả chỉ lưu trên máy.
- **Kết quả luôn được lưu trên máy trước** (`results/results.jsonl` cùng ảnh `.jpg`), sau đó mới POST lên BE. Lỗi mạng hoặc lỗi server (5xx) được thử lại 3 lần. BE từ chối dữ liệu (4xx) thì không thử lại. Mất mạng thì không mất kết quả.

BE hiện chưa có xác thực. Chỉ chạy trong mạng nội bộ, hoặc đặt sau reverse proxy có xác thực trước khi mở ra ngoài.

## Train model OBB cho hộp (tuỳ chọn)

Box detector zero-shot hiện bắt được hộp ở khoảng 74% frame (đo trên 100 frame webcam thật). Để cải thiện:

```bash
uv run python src/barcode_cv/train_obb.py collect   # SPACE: lưu frame khi khung xanh đúng, q: thoát
uv run python src/barcode_cv/train_obb.py train     # fine-tune yolo26n-obb.pt -> runs/obb/train/weights/best.pt
```

Nên thu khoảng 150–300 ảnh, đủ nhiều góc, khoảng cách và ánh sáng, có cả frame không có hộp. Model chỉ nên thay vào bước Box khi precision và recall đều cao (ví dụ > 0.8).

## Cấu trúc

| File | Nội dung |
|---|---|
| `src/barcode_cv/yolo_model.py` | Vòng lặp chính: box, item, barcode, compare, seal, kết quả |
| `src/barcode_cv/barcode_reader.py` | Đọc barcode (OpenCV → zxing → OCR), kiểm tra EAN-13 |
| `src/barcode_cv/inspection.py` | `BoxSession`: Expected / Scanned / Visual count, Compare |
| `src/barcode_cv/be_client.py` | API client, mock, lưu kết quả trên máy |
| `src/barcode_cv/server.py` | BE: FastAPI + PostgreSQL, lệnh `seed` / `check` |
| `src/barcode_cv/schema.sql` | Schema PostgreSQL |
| `src/barcode_cv/ui.py` | Panel nhân viên, nút Xong |
| `src/barcode_cv/box_tracker.yaml` | ByteTrack, ngưỡng khớp với `BOX_CONF` (0.12) của box detector |
| `src/barcode_cv/train_obb.py` | Thu dữ liệu và train model OBB cho hộp |
| `mock_checklist.json` | Checklist giả khi chưa có BE |

Tự kiểm tra (in ra `ok`):

```bash
cd src/barcode_cv
uv run python inspection.py
uv run python barcode_reader.py
```

## Giới hạn đã biết

- **Box zero-shot còn yếu:** bắt được hộp ở khoảng 74% frame. Barcode vẫn đọc trên cả khung hình và box được giữ thêm 1 s để bù, nhưng muốn ổn định thì cần train model OBB.
- **Seal:** dựa vào diện tích seal tăng thêm so với mốc lúc OK, chưa phân loại được seal còn nguyên hay bị rách, và chưa được kiểm chứng với seal thật. Cần ảnh đã gán nhãn để train model OBB + classification.
- **Barcode trên webcam 1280×720:** mỗi vạch chỉ khoảng 2 px. Frame bị nhòe do chuyển động sẽ không đọc được, nên cầm yên khoảng nửa giây. OCR dự phòng mất khoảng 1,1 s mỗi lần và chỉ đọc mã 13 số.
- **Đếm trùng:** nếu tracker đổi ID của cùng một item (do bị che hoặc di chuyển nhanh), item đó bị đếm 2 lần.
- **Tốc độ:** khoảng 7–9 FPS trên CPU khi có hộp trong khung hình.
