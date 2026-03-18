#!/usr/bin/env python3
"""
RunPod Serverless Handler for ComfyUI Video Generation.

Accepts a FutureScope script JSON, runs generate_video_v5.py via ComfyUI,
and uploads the resulting video + thumbnail to Cloudflare R2.
"""

import runpod
import os
import json
import subprocess
import glob
import time
import sys
import requests
import boto3
from botocore.config import Config


# ---------------------------------------------------------------------------
# Paths — Network Volume mounts at:
#   /runpod-volume/  on serverless
#   /workspace/      on pods
# ---------------------------------------------------------------------------

def detect_volume():
    """Find where the Network Volume is mounted."""
    for path in ["/runpod-volume", "/workspace"]:
        comfyui = f"{path}/runpod-slim/ComfyUI"
        if os.path.isdir(comfyui):
            print(f"Found ComfyUI at: {comfyui}")
            return path
    # Fallback: check what exists
    for path in ["/runpod-volume", "/workspace"]:
        if os.path.exists(path):
            print(f"Volume at {path}, contents: {os.listdir(path)}")
    return "/runpod-volume"  # default

VOLUME = detect_volume()
COMFYUI_DIR = f"{VOLUME}/runpod-slim/ComfyUI"
WORKSPACE = f"{VOLUME}/runpod-slim"
COMFYUI_URL = "http://localhost:8188"
COMFYUI_MAX_WAIT = 300  # seconds
GENERATE_SCRIPT = "/app/generate_video_v5.py"


# ---------------------------------------------------------------------------
# Startup — start ComfyUI before accepting jobs
# ---------------------------------------------------------------------------

def start_comfyui():
    """Start ComfyUI in the background from the Network Volume."""
    print(f"=== RunPod ComfyUI Video Worker ===")
    print(f"Looking for ComfyUI at: {COMFYUI_DIR}")

    # Debug: show volume contents
    if os.path.exists(VOLUME):
        print(f"Volume contents: {os.listdir(VOLUME)}")
    else:
        print(f"WARNING: Volume not mounted at {VOLUME}")

    if not os.path.isdir(COMFYUI_DIR):
        print(f"ERROR: ComfyUI not found at {COMFYUI_DIR}")
        if os.path.exists(VOLUME):
            # Try to find it
            for root, dirs, files in os.walk(VOLUME):
                if "main.py" in files and "comfy" in root.lower():
                    print(f"  Found ComfyUI at: {root}")
                if root.count(os.sep) - VOLUME.count(os.sep) > 2:
                    break
        sys.exit(1)

    # Create workspace dirs
    os.makedirs(f"{WORKSPACE}/videos", exist_ok=True)
    os.makedirs(f"{WORKSPACE}/LOG", exist_ok=True)
    os.makedirs(f"{WORKSPACE}/temp", exist_ok=True)

    # Find venv and use bash activate (venv symlinks may point to
    # a Python version not in this container, so we can't call the
    # venv python directly — must source activate instead)
    comfyui_contents = os.listdir(COMFYUI_DIR)
    venv_dirs = [f for f in comfyui_contents if f.startswith((".venv", "venv"))]
    print(f"ComfyUI venv dirs: {venv_dirs}")

    venv_activate = None
    for name in venv_dirs:
        candidate = os.path.join(COMFYUI_DIR, name, "bin", "activate")
        if os.path.exists(candidate):
            venv_activate = candidate
            break

    if venv_activate:
        print(f"Using venv via bash activate: {venv_activate}")
        comfyui_cmd = ["bash", "-c", f"source {venv_activate} && python main.py --listen --port 8188"]
    else:
        print(f"No venv found in {COMFYUI_DIR}, using system python")
        comfyui_cmd = ["python3", "main.py", "--listen", "--port", "8188"]

    print(f"Starting ComfyUI: {' '.join(comfyui_cmd)}")
    # Let ComfyUI output go to stdout/stderr so we can see errors in logs
    process = subprocess.Popen(
        comfyui_cmd,
        cwd=COMFYUI_DIR,
    )
    print(f"ComfyUI started with PID: {process.pid}")
    return process


def wait_for_comfyui():
    """Block until ComfyUI is responsive or timeout."""
    start = time.time()
    while time.time() - start < COMFYUI_MAX_WAIT:
        try:
            resp = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
            if resp.status_code == 200:
                print("ComfyUI is ready.")
                return True
        except requests.ConnectionError:
            pass
        time.sleep(3)
    raise RuntimeError(f"ComfyUI did not become ready within {COMFYUI_MAX_WAIT}s")


# ---------------------------------------------------------------------------
# R2 upload
# ---------------------------------------------------------------------------

def get_r2_client():
    """Create a boto3 S3 client pointed at Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY"],
        aws_secret_access_key=os.environ["R2_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_to_r2(local_path):
    """Upload a file to R2 and return its public URL."""
    client = get_r2_client()
    bucket = os.environ["R2_BUCKET"]
    key = f"videos/{os.path.basename(local_path)}"

    content_type = "video/mp4" if local_path.endswith(".mp4") else "image/png"
    client.upload_file(
        local_path, bucket, key,
        ExtraArgs={"ContentType": content_type},
    )

    public_url = os.environ.get("R2_PUBLIC_URL", "")
    return f"{public_url}/{key}" if public_url else key


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(job):
    """
    RunPod serverless handler.

    Expected job["input"]:
        script          : dict   — the full script JSON (scenes + metadata)
        ELEVENLABS_API_KEY : str
        ELEVENLABS_VOICE_ID: str  (optional)
        R2_ACCOUNT_ID   : str
        R2_ACCESS_KEY   : str
        R2_SECRET_KEY   : str
        R2_BUCKET       : str
        R2_PUBLIC_URL   : str   (optional)
        VIDEO_MODEL     : str   (optional, "ltx23" or "wan22")
        ENABLE_UPSCALE  : str   (optional, "0" or "1")
        ENABLE_FRAME_INTERPOLATION : str (optional)
    """
    input_data = job["input"]

    # --- 1. Write script JSON to temp file ---
    script_path = "/tmp/script.json"
    with open(script_path, "w") as f:
        json.dump(input_data["script"], f, indent=2)

    # --- 2. Set environment variables from job input ---
    env_keys = [
        "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY",
        "R2_BUCKET", "R2_PUBLIC_URL",
        "VIDEO_MODEL", "ENABLE_UPSCALE", "ENABLE_FRAME_INTERPOLATION",
        "TARGET_WIDTH", "TARGET_HEIGHT", "TARGET_FPS",
    ]
    for key in env_keys:
        if key in input_data and input_data[key]:
            os.environ[key] = str(input_data[key])

    os.environ["WORKSPACE_DIR"] = WORKSPACE
    os.environ["COMFYUI_URL"] = COMFYUI_URL

    # Ensure output dirs exist
    os.makedirs(os.path.join(WORKSPACE, "videos"), exist_ok=True)
    os.makedirs(os.path.join(WORKSPACE, "LOG"), exist_ok=True)

    # --- 3. Wait for ComfyUI ---
    wait_for_comfyui()

    # --- 4. Run generate_video_v5.py ---
    print(f"Starting video generation for: {input_data['script'].get('metadata', {}).get('title', 'unknown')}")

    result = subprocess.run(
        ["python3", GENERATE_SCRIPT, script_path],
        capture_output=True,
        text=True,
        timeout=7200,  # 2 hour max
        cwd=WORKSPACE,
    )

    print("=== STDOUT (last 3000 chars) ===")
    print(result.stdout[-3000:])
    if result.returncode != 0:
        print("=== STDERR (last 2000 chars) ===")
        print(result.stderr[-2000:])

    # --- 5. Find outputs ---
    video_patterns = [
        os.path.join(WORKSPACE, "videos", "video-*.mp4"),
        os.path.join(WORKSPACE, "videos", "short-*.mp4"),
    ]
    videos = []
    for pattern in video_patterns:
        videos.extend(sorted(glob.glob(pattern)))

    if not videos:
        return {
            "status": "failed",
            "error": f"No video output found. Return code: {result.returncode}",
            "stderr": result.stderr[-2000:] if result.stderr else "",
            "stdout": result.stdout[-2000:] if result.stdout else "",
        }

    # Take the most recent video
    latest_video = max(videos, key=os.path.getmtime)

    thumbs = sorted(glob.glob(os.path.join(WORKSPACE, "videos", "thumbnail-*.png")))
    latest_thumb = max(thumbs, key=os.path.getmtime) if thumbs else None

    # --- 6. Upload to R2 ---
    video_url = upload_to_r2(latest_video)
    thumb_url = upload_to_r2(latest_thumb) if latest_thumb else None

    # --- 7. Clean up old outputs (keep workspace tidy) ---
    try:
        os.remove(latest_video)
        if latest_thumb:
            os.remove(latest_thumb)
    except OSError:
        pass

    return {
        "status": "success",
        "video_url": video_url,
        "thumbnail_url": thumb_url,
        "video_filename": os.path.basename(latest_video),
        "log_tail": result.stdout[-1000:],
    }


# ---------------------------------------------------------------------------
# Entry point — start ComfyUI, then accept jobs
# ---------------------------------------------------------------------------

print("Initializing worker...")
comfyui_process = start_comfyui()

runpod.serverless.start({"handler": handler})
