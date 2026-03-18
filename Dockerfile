# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# ComfyUI + models live on Network Volume at /runpod-volume/runpod-slim/ComfyUI/
# This image provides CUDA runtime + our serverless handler.
# =============================================================================

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# System dependencies + Python 3.12
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    python3-pip \
    ffmpeg \
    fonts-dejavu-core \
    unzip \
    wget \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3.12 the default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --set python3 /usr/bin/python3.12

# Remove EXTERNALLY-MANAGED restriction and install pip for 3.12
RUN rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
RUN python3.12 -m pip install --no-cache-dir \
    runpod \
    boto3 \
    edge-tts \
    requests

# Verify
RUN python3.12 -c "import runpod; print(f'runpod {runpod.__version__} OK')"
RUN python3.12 -c "import boto3; print('boto3 OK')"

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
CMD ["python3.12", "/app/handler.py"]
