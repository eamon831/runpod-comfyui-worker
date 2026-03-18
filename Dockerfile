# =============================================================================
# RunPod Serverless ComfyUI Video Worker
#
# Generates videos via ComfyUI (Wan 2.2 / LTX 2.3) on RunPod Serverless.
# Models load from a Network Volume mounted at /runpod-volume.
# =============================================================================

FROM runpod/worker-comfyui:latest-base

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    wget \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir \
    edge-tts \
    boto3 \
    runpod

# ---------------------------------------------------------------------------
# Video-specific ComfyUI custom nodes
# ---------------------------------------------------------------------------
WORKDIR /workspace/ComfyUI/custom_nodes

# VideoHelperSuite — VHS_VideoCombine for video output
RUN git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite && \
    cd ComfyUI-VideoHelperSuite && pip install --no-cache-dir -r requirements.txt

# LTX Video nodes
RUN git clone --depth 1 https://github.com/Lightricks/ComfyUI-LTXVideo && \
    cd ComfyUI-LTXVideo && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# Wan 2.2 video wrapper
RUN git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper && \
    cd ComfyUI-WanVideoWrapper && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# ---------------------------------------------------------------------------
# Real-ESRGAN (upscaling) — pre-built Linux binary
# ---------------------------------------------------------------------------
RUN mkdir -p /usr/local/bin && \
    wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip \
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
COPY workflows/ /app/workflows/

# generate_video_v5.py is copied from the futurescope repo at build time
# Place it next to the handler for subprocess invocation
COPY generate_video_v5.py /app/generate_video_v5.py

RUN chmod +x /app/start.sh

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
CMD ["/app/start.sh"]
