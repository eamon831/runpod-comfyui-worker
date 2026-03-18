# =============================================================================
# RunPod Serverless ComfyUI Video Worker — thin layer
# =============================================================================

FROM runpod/base:0.6.2-cuda12.2.0

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core unzip curl && rm -rf /var/lib/apt/lists/*

# Install pip, then our deps
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3 && \
    pip3 install --no-cache-dir runpod boto3 edge-tts requests && \
    python3 -c "import runpod; print('runpod OK')"

# App files
WORKDIR /app
COPY handler.py /app/handler.py
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

CMD ["python3", "/app/handler.py"]
