# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# Thin image — ComfyUI + models live on Network Volume at
# /workspace/runpod-slim/ComfyUI/ (populated via comfyui-base pod template).
# This image just adds the serverless handler + video pipeline tools.
# =============================================================================

FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3-pip \
    ffmpeg \
    fonts-dejavu-core \
    wget \
    unzip \
    git \
    && rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED \
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
# Real-ESRGAN (upscaling) — pre-built Linux binary
# ---------------------------------------------------------------------------
RUN wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip \
    -O /tmp/realesrgan.zip && \
    cd /tmp && unzip -o realesrgan.zip -d realesrgan && \
    cp realesrgan/realesrgan-ncnn-vulkan /usr/local/bin/ && \
    cp -r realesrgan/models /usr/local/share/realesrgan-models && \
    chmod +x /usr/local/bin/realesrgan-ncnn-vulkan && \
    rm -rf /tmp/realesrgan*

# ---------------------------------------------------------------------------
# RIFE (frame interpolation) — pre-built Linux binary
# ---------------------------------------------------------------------------
RUN wget -q https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-ubuntu.zip \
    -O /tmp/rife.zip && \
    cd /tmp && unzip -o rife.zip -d rife && \
    cp rife/rife-ncnn-vulkan-20221029-ubuntu/rife-ncnn-vulkan /usr/local/bin/ && \
    cp -r rife/rife-ncnn-vulkan-20221029-ubuntu/models /usr/local/share/rife-models && \
    chmod +x /usr/local/bin/rife-ncnn-vulkan && \
    rm -rf /tmp/rife*

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
