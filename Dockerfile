# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# Based on runpod/comfyui:latest-5090 which has:
# - Python 3.12, CUDA 13.0, PyTorch 2.8+, RTX 5090 support
# - ComfyUI pre-installed (but we use Network Volume's copy)
# =============================================================================

FROM runpod/comfyui:latest-5090

# Install handler dependencies into the system Python 3.12
RUN python3.12 -m pip install --no-cache-dir --break-system-packages \
    runpod boto3 edge-tts && \
    python3.12 -c "import runpod; print('runpod OK')"

# App files
WORKDIR /app
COPY handler.py /app/handler.py
COPY scripts/generate_video_v5.py /app/generate_video_v5.py
COPY workflows/ /app/workflows/

# Override the image's ENTRYPOINT so our handler runs instead of ComfyUI UI
ENTRYPOINT []
CMD ["python3.12", "/app/handler.py"]
