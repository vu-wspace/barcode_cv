FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# git: the CLIP dependency is installed from git | libgl1, libglib2.0-0: OpenCV
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    VISION_PORT=8090 RESULTS_DIR=/data/results MOCK_CHECKLIST=/app/mock_checklist.json
WORKDIR /app
# Dependencies first: this layer (CPU-only torch, ~1 GB) is reused until pyproject.toml / uv.lock change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY vision ./vision
COPY mock_checklist.json ./

# Model weights are downloaded on first start into the working directory: /data is a volume, so only once.
WORKDIR /data
EXPOSE 8090
CMD ["python", "-m", "vision"]
