#!/bin/bash
set -e

echo "=== RunPod ComfyUI Video Worker ==="
echo "Starting at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# ---------------------------------------------------------------------------
# Network Volume model symlinks
# ---------------------------------------------------------------------------
VOLUME="/runpod-volume"
COMFYUI="/workspace/ComfyUI"

if [ -d "$VOLUME/models" ]; then
    echo "Linking network volume models..."

    # Link each model subdirectory
    for subdir in diffusion_models loras clip vae upscale_models; do
        if [ -d "$VOLUME/models/$subdir" ]; then
            # Create target dir if missing
            mkdir -p "$COMFYUI/models/$subdir"
            # Symlink individual files (don't clobber existing)
            for file in "$VOLUME/models/$subdir"/*; do
                [ -f "$file" ] || continue
                target="$COMFYUI/models/$subdir/$(basename "$file")"
                if [ ! -e "$target" ]; then
                    ln -s "$file" "$target"
                    echo "  Linked: $subdir/$(basename "$file")"
                fi
            done
        fi
    done
else
    echo "WARNING: No network volume models found at $VOLUME/models"
fi

# ---------------------------------------------------------------------------
# Workspace directories
# ---------------------------------------------------------------------------
WORKSPACE="$VOLUME/workspace"
mkdir -p "$WORKSPACE/videos" "$WORKSPACE/LOG" "$WORKSPACE/temp"

# Link music assets if available
if [ -d "$VOLUME/music" ]; then
    mkdir -p "$WORKSPACE/assets"
    if [ ! -e "$WORKSPACE/assets/music" ]; then
        ln -s "$VOLUME/music" "$WORKSPACE/assets/music"
        echo "Linked music assets"
    fi
fi

# ---------------------------------------------------------------------------
# Start ComfyUI in background
# ---------------------------------------------------------------------------
echo "Starting ComfyUI..."
cd "$COMFYUI" && python3 main.py --listen --port 8188 &
COMFYUI_PID=$!
echo "ComfyUI PID: $COMFYUI_PID"

# ---------------------------------------------------------------------------
# Start the serverless handler
# ---------------------------------------------------------------------------
echo "Starting serverless handler..."
python3 /app/handler.py
