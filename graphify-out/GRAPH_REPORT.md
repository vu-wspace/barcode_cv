# Graph Report - barcode_cv  (2026-11-10)

## Corpus Check
- Corpus is ~15,428 words - fits in a single context window. You may not need a graph.

## Summary
- 280 nodes · 417 edges · 16 communities (10 shown, 6 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 20 edges (avg confidence: 0.85)
- Token cost: 79,218 input · 0 output

## Community Hubs (Navigation)
- Vision Barcode & Pipeline
- Backend API (FastAPI)
- Docker Compose Services
- System Architecture Concepts
- Standalone Scanner Page
- Vision HTTP API
- Backend Client (BE)
- Operator Web UI
- Box Inspection Session
- ScanCore Logic
- Postgres DB Service
- Frame Hand-over (Shared)
- Browser Camera Source
- Backend Project Config
- Vision Project Config

## God Nodes (most connected - your core abstractions)
1. `run()` - 20 edges
2. `BoxSession` - 11 edges
3. `obb_from_mask()` - 9 edges
4. `send_result()` - 8 edges
5. `inside()` - 8 edges
6. `vision service (:8090)` - 8 edges
7. `accept()` - 8 edges
8. `vision` - 7 edges
9. `startCamera()` - 7 edges
10. `segment_items()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `startCamera()` --semantically_similar_to--> `#camera-ctl browser camera controls`  [INFERRED] [semantically similar]
  barcodetalk_twopz/index.html → services/frontend/public/index.html
- `ensureDetector()` --semantically_similar_to--> `Barcode cascade OpenCV -> zxing -> RapidOCR`  [INFERRED] [semantically similar]
  barcodetalk_twopz/index.html → README.md
- `Multi-frame read agreement (3 reads in 1.5 s)` --semantically_similar_to--> `box_id confirmation rule (2 reads in 3 s)`  [INFERRED] [semantically similar]
  barcodetalk_twopz/index.html → README.md
- `#stream MJPEG video img` --shares_data_with--> `vision service (:8090)`  [INFERRED]
  services/frontend/public/index.html → README.md
- `#camera-ctl browser camera controls` --implements--> `Browser camera via WS /camera (~10 fps JPEG)`  [INFERRED]
  services/frontend/public/index.html → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Scan input sources converging on accept()** — barcodetalk_twopz_index_ondetect, barcodetalk_twopz_index_scanimage, barcodetalk_twopz_index_manual, barcodetalk_twopz_index_accept [EXTRACTED 1.00]
- **Docker microservice architecture** — readme_frontend_service, readme_vision_service, readme_backend_service, readme_postgres_db [EXTRACTED 1.00]
- **Vision inspection stages** — readme_box_detection, readme_item_segmentation, readme_barcode_reading_cascade, readme_compare_step, readme_seal_check [EXTRACTED 1.00]

## Communities (16 total, 6 thin omitted)

### Community 0 - "Vision Barcode & Pipeline"
Cohesion: 0.07
Nodes (22): ean13_from_text_boxes(), ean13_ok(), ocr_ean13(), ocr_ean13_async(), read_barcodes(), _upright(), assign(), box_code() (+14 more)

### Community 1 - "Backend API (FastAPI)"
Cohesion: 0.08
Nodes (11): check(), ChecklistItem, get_checklist(), get_image(), Inspection, lifespan(), list_inspections(), post_inspection() (+3 more)

### Community 2 - "Docker Compose Services"
Cohesion: 0.06
Nodes (30): build, depends_on, environment, restart, volumes, condition, db, BACKEND_URL (+22 more)

### Community 3 - "System Architecture Concepts"
Cohesion: 0.08
Nodes (16): Backend API (checklist + inspections endpoints), backend service (FastAPI :8000), barcode-cv Packing Inspection System, ByteTrack (box_tracker.yaml), frontend service (nginx :8080), Known limitations, db (PostgreSQL 18, pgdata volume), Vision env config (CAMERA_SOURCE, BE_URL, MOCK_CHECKLIST...) (+8 more)

### Community 4 - "Standalone Scanner Page"
Cohesion: 0.11
Nodes (18): accept(), CFG scanner config, drawBox(), ensureDetector(), flipCamera(), loop() throttled detect loop, manual() barcode entry, onDetect() (+10 more)

### Community 5 - "Vision HTTP API"
Cohesion: 0.11
Nodes (7): camera(), camera_loop(), config(), health(), state(), stream(), xong()

### Community 6 - "Backend Client (BE)"
Cohesion: 0.20
Nodes (4): _fetch(), fetch_checklist(), _post(), send_result()

### Community 7 - "Operator Web UI"
Cohesion: 0.25
Nodes (12): cam, camStatus(), listCameras(), listItem(), openSocket(), PERMISSION_HELP, poll(), render() (+4 more)

### Community 9 - "ScanCore Logic"
Cohesion: 0.18
Nodes (5): gs1CheckValid(), isReadTrustworthy(), a, assert, w

### Community 10 - "Postgres DB Service"
Cohesion: 0.18
Nodes (11): environment, healthcheck, image, restart, volumes, POSTGRES_DB, POSTGRES_PASSWORD, interval (+3 more)

## Knowledge Gaps
- **14 isolated node(s):** `backend`, `vision`, `ByteTrack (box_tracker.yaml)`, `RapidOCR (PP-OCR)`, `Vision env config (CAMERA_SOURCE, BE_URL, MOCK_CHECKLIST...)` (+9 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 138 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BoxSession` connect `Box Inspection Session` to `Vision Barcode & Pipeline`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **What connects `backend`, `vision`, `ByteTrack (box_tracker.yaml)` to the rest of the system?**
  _14 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Vision Barcode & Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.07138535995160314 - nodes in this community are weakly interconnected._
- **Why does `Shared` connect `Frame Hand-over (Shared)` to `Vision Barcode & Pipeline`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Should `Backend API (FastAPI)` be split into smaller, more focused modules?**
  _Cohesion score 0.07899159663865546 - nodes in this community are weakly interconnected._
- **Why does `Vision pipeline (Box -> Items -> Barcode -> Compare -> Seal)` connect `System Architecture Concepts` to `Standalone Scanner Page`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Should `Docker Compose Services` be split into smaller, more focused modules?**
  _Cohesion score 0.06451612903225806 - nodes in this community are weakly interconnected._