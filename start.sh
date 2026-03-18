#!/bin/bash
set -e

echo "=== RunPod ComfyUI Video Worker ==="
echo "Starting at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# ---------------------------------------------------------------------------
# Paths — comfyui-base installs to /workspace/runpod-slim/ComfyUI/
# Network Volume mounts at /workspace/ (not /runpod-volume/)
# ---------------------------------------------------------------------------
COMFYUI="/workspace/runpod-slim/ComfyUI"
WORKSPACE="/workspace/runpod-slim"

# ---------------------------------------------------------------------------
# Workspace directories
# ---------------------------------------------------------------------------
mkdir -p "$WORKSPACE/videos" "$WORKSPACE/LOG" "$WORKSPACE/temp"

# Link music assets if available
if [ -d "$WORKSPACE/assets/music" ]; then
    echo "Music assets found"
elif [ -d "/workspace/music" ]; then
    mkdir -p "$WORKSPACE/assets"
    ln -sf "/workspace/music" "$WORKSPACE/assets/music"
    echo "Linked music assets"
fi

# ---------------------------------------------------------------------------
# Start ComfyUI in background
# ---------------------------------------------------------------------------
if [ -d "$COMFYUI" ]; then
    echo "Starting ComfyUI from $COMFYUI..."
    cd "$COMFYUI"

    # Use venv if available (comfyui-base creates .venv-cu128)
    if [ -f ".venv-cu128/bin/activate" ]; then
        source .venv-cu128/bin/activate
    fi

    python3 main.py --listen --port 8188 &
    COMFYUI_PID=$!
    echo "ComfyUI PID: $COMFYUI_PID"
else
    echo "ERROR: ComfyUI not found at $COMFYUI"
    echo "Make sure Network Volume is attached with ComfyUI installed"
    exit 1
fi

# ---------------------------------------------------------------------------
# Start the serverless handler
# ---------------------------------------------------------------------------
echo "Starting serverless handler..."
cd /app
python3 handler.py
