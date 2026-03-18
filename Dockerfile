# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# Thin image — ComfyUI + models live on Network Volume at
# /workspace/runpod-slim/ComfyUI/ (populated via comfyui-base pod template).
# This image just adds the serverless handler + video pipeline tools.
# =============================================================================

FROM runpod/base:0.6.2-cuda12.2.0

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir \
    runpod \
    boto3 \
    edge-tts \
    requests

# ---------------------------------------------------------------------------
# Application files
# ---------------------------------------------------------------------------
WORKDIR /app

COPY handler.py /app/handler.py
COPY start.sh /app/start.sh
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

RUN chmod +x /app/start.sh

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
CMD ["/app/start.sh"]
