# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# Thin image — ComfyUI + models live on Network Volume at
# /runpod-volume/runpod-slim/ComfyUI/ (populated via comfyui-base pod).
# This image just adds the serverless handler + video pipeline tools.
# =============================================================================

FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    unzip \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir \
    runpod \
    boto3 \
    edge-tts \
    requests

# Verify runpod is installed
RUN python3 -c "import runpod; print(f'runpod {runpod.__version__} installed')"

# ---------------------------------------------------------------------------
# Application files
# ---------------------------------------------------------------------------
WORKDIR /app

COPY handler.py /app/handler.py
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
CMD ["python3", "/app/handler.py"]
