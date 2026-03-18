# =============================================================================
# RunPod Serverless ComfyUI Video Worker — thin layer
# =============================================================================

FROM runpod/base:0.6.2-cuda12.2.0

# System deps (ffmpeg already in base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core unzip && rm -rf /var/lib/apt/lists/*

# Python deps — use same python that runpod/base provides
RUN python -m pip install --no-cache-dir runpod boto3 edge-tts requests && \
    python -c "import runpod; print(f'runpod OK: {runpod.__version__}')"

# App files
WORKDIR /app
COPY handler.py /app/handler.py
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

CMD ["python", "/app/handler.py"]
