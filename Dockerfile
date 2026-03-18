# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# MUST use Python 3.12 + CUDA 12.x to match Network Volume's venv
# (created by comfyui-base pod template with Python 3.12)
# =============================================================================

FROM nvidia/cuda:12.4.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Python 3.12 (default on Ubuntu 24.04) + system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3-pip \
    ffmpeg \
    fonts-dejavu-core \
    unzip \
    curl \
    git \
    && rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED \
    && rm -rf /var/lib/apt/lists/*

# Handler dependencies (installed to system Python 3.12)
RUN python3.12 -m pip install --no-cache-dir \
    runpod boto3 edge-tts requests && \
    python3.12 -c "import runpod; print('runpod OK')"

# App files
WORKDIR /app
COPY handler.py /app/handler.py
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

CMD ["python3.12", "/app/handler.py"]
