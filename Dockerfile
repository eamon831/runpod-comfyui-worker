# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# Uses runpod/base (builds reliably on RunPod) + installs Python 3.12
# to match Network Volume's venv (created by comfyui-base with Python 3.12)
# =============================================================================

FROM runpod/base:0.6.2-cuda12.2.0

# Install Python 3.12 from deadsnakes PPA (base has 3.11 + 3.10)
# This makes /usr/bin/python3.12 available so venv symlinks resolve
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3.12-dev \
    fonts-dejavu-core unzip curl && \
    rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED && \
    rm -rf /var/lib/apt/lists/*

# Bootstrap pip for Python 3.12 and install handler dependencies
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12 && \
    python3.12 -m pip install --no-cache-dir runpod boto3 edge-tts requests && \
    python3.12 -c "import runpod; print('runpod OK')"

# App files
WORKDIR /app
COPY handler.py /app/handler.py
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

CMD ["python3.12", "/app/handler.py"]
