#!/usr/bin/env python3
"""
Video Generation Script for FutureScope - Wan 2.2
Generates video clips via ComfyUI API and stitches them together
Version 5: Added voiceover, music, checkpoint/resume, pre-flight validation
Version 6: Added i2v workflow support, quality gate, per-scene workflow selection
"""

import json
import time
import random
import subprocess
import requests
import os
import copy
import hashlib
import shutil
from pathlib import Path
from datetime import datetime

# Source .env files before reading environment variables
def load_env_files():
    """Load environment variables from .env files."""
    env_files = [
        "/workspace/.openclaw/.env",
        os.path.expanduser("~/.openclaw/.env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    for env_file in env_files:
        if os.path.exists(env_file):
            with open(env_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        # Always update from .env (overrides existing)
                        os.environ[key] = value

load_env_files()

# Configuration — paths are derived from WORKSPACE_DIR or CLI args
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
OUTPUT_DIR = os.path.join(WORKSPACE_DIR, "videos")

# These are set dynamically in main() based on the script file and GPU detection
SCRIPT_FILE = None
TEMP_DIR = None
LOG_FILE = None

# Model profiles — auto-selected based on GPU VRAM
# Can be overridden with VIDEO_MODEL env var: "ltx23", "wan22"
MODEL_PROFILES = {
    "ltx23": {
        "name": "LTX 2.3",
        "workflow": "workflows/ltx_2_3_t2v.json",
        "i2v_workflow": "workflows/ltx_2_3_i2v.json",
        "t2i_workflow": "workflows/t2i_z_image.json",
        "width": 1280,
        "height": 720,
        "fps": 24,
        "turbo": False,  # LTX doesn't use turbo LoRA
        "min_vram_gb": 32,
        "max_wait": 120,  # Fast model, shorter timeout
        "has_audio": True,
    },
    "wan22": {
        "name": "Wan 2.2",
        "workflow": "workflows/video_wan2_2_14B_t2v.json",
        "i2v_workflow": "workflows/video_wan2_2_14B_i2v.json",
        "t2i_workflow": "workflows/t2i_z_image.json",
        "width": 640,
        "height": 360,
        "fps": 16,
        "turbo": True,  # 4-step turbo LoRA
        "min_vram_gb": 24,
        "max_wait": 600,
        "has_audio": False,
    },
}

# Active model — set in main() by auto-detection or env var
ACTIVE_MODEL = None
WORKFLOW_FILE = None
WIDTH = 640
HEIGHT = 360
FPS = 16
TURBO_MODE = True

# Features
ENABLE_VOICEOVER = True
ENABLE_BACKGROUND_MUSIC = True
ENABLE_UPSCALE = os.environ.get("ENABLE_UPSCALE", "1") != "0"
ENABLE_FRAME_INTERPOLATION = os.environ.get("ENABLE_FRAME_INTERPOLATION", "1") != "0"
TARGET_WIDTH = int(os.environ.get("TARGET_WIDTH", "1920"))
TARGET_HEIGHT = int(os.environ.get("TARGET_HEIGHT", "1080"))
TARGET_FPS = int(os.environ.get("TARGET_FPS", "30"))

# Voiceover settings (ElevenLabs)
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # "Adam" - clear male voice
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Background audio — halal only (ambient/nasheed, no musical instruments)
BACKGROUND_MUSIC_DIR = os.path.join(WORKSPACE_DIR, "assets/music")

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 10
MAX_WAIT_TIME = 600  # Default, overridden by model profile


def detect_gpu_and_select_model():
    """Auto-detect GPU VRAM and select the best video generation model.

    Priority: LTX 2.3 (if VRAM >= 32GB) > Wan 2.2 (fallback).
    Can be overridden with VIDEO_MODEL env var.
    """
    global ACTIVE_MODEL, WORKFLOW_FILE, WIDTH, HEIGHT, FPS, TURBO_MODE, MAX_WAIT_TIME

    # Check for manual override
    forced_model = os.environ.get("VIDEO_MODEL", "").lower()
    if forced_model in MODEL_PROFILES:
        profile = MODEL_PROFILES[forced_model]
        _apply_model_profile(forced_model, profile)
        log(f"Model override: {profile['name']} (VIDEO_MODEL={forced_model})")
        return

    # Auto-detect VRAM via ComfyUI system_stats
    vram_gb = 0
    try:
        r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=10)
        if r.status_code == 200:
            stats = r.json()
            devices = stats.get("devices", [])
            if devices:
                vram_bytes = devices[0].get("vram_total", 0)
                vram_gb = vram_bytes / (1024 ** 3)
                log(f"GPU detected: {devices[0].get('name', 'Unknown')} — {vram_gb:.0f}GB VRAM")
    except Exception as e:
        log(f"Could not detect GPU: {e}")

    # Fallback: check nvidia-smi
    if vram_gb == 0:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                vram_gb = float(result.stdout.strip().split("\n")[0]) / 1024
                log(f"GPU detected via nvidia-smi: {vram_gb:.0f}GB VRAM")
        except Exception:
            log("nvidia-smi not available, defaulting to Wan 2.2")

    # Select model based on VRAM
    # Check if LTX 2.3 workflow exists before selecting it
    ltx_workflow = os.path.join(WORKSPACE_DIR, MODEL_PROFILES["ltx23"]["workflow"])
    wan_workflow = os.path.join(WORKSPACE_DIR, MODEL_PROFILES["wan22"]["workflow"])

    # Note: RTX 5090 reports 32607 MB = 31.8 GB, so threshold is 31.5 to catch 32GB cards
    if vram_gb >= 31.5 and os.path.exists(ltx_workflow):
        _apply_model_profile("ltx23", MODEL_PROFILES["ltx23"])
    elif os.path.exists(wan_workflow):
        _apply_model_profile("wan22", MODEL_PROFILES["wan22"])
    else:
        log("ERROR: No workflow files found!")
        _apply_model_profile("wan22", MODEL_PROFILES["wan22"])


def _apply_model_profile(model_key, profile):
    """Apply a model profile to global settings."""
    global ACTIVE_MODEL, WORKFLOW_FILE, WIDTH, HEIGHT, FPS, TURBO_MODE, MAX_WAIT_TIME
    ACTIVE_MODEL = model_key
    WORKFLOW_FILE = os.path.join(WORKSPACE_DIR, profile["workflow"])
    WIDTH = profile["width"]
    HEIGHT = profile["height"]
    FPS = profile["fps"]
    TURBO_MODE = profile.get("turbo", False)
    MAX_WAIT_TIME = profile.get("max_wait", 600)
    log(f"Selected model: {profile['name']} ({WIDTH}x{HEIGHT} @ {FPS}fps, timeout={MAX_WAIT_TIME}s)")

# Widget parameter name mappings for key node types
WIDGET_MAPPINGS = {
    "CLIPLoader": ["clip_name", "type", "device"],
    "VAELoader": ["vae_name"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CLIPTextEncode": ["text"],
    "EmptyHunyuanLatentVideo": ["width", "height", "length", "batch_size"],
    "KSamplerAdvanced": ["add_noise", "noise_seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise"],
    "LoraLoaderModelOnly": ["lora_name", "strength_model"],
    "ModelSamplingSD3": ["shift"],
    "VAEDecode": [],
    "CreateVideo": ["fps"],
    "SaveVideo": ["filename_prefix", "format", "codec"],
    "WanImageToVideo": ["width", "height", "length", "batch_size"],
    "LoadImage": ["image", "upload"],
}

def log(message):
    """Log message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {message}"
    print(log_msg)
    if LOG_FILE:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(log_msg + "\n")

def compute_hash(text):
    """Compute a short SHA256 hash for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_progress(progress_path, script_file, script_hash):
    """Load checkpoint progress. Returns empty dict if stale or missing."""
    if not os.path.exists(progress_path):
        return {}

    try:
        with open(progress_path, "r") as f:
            progress = json.load(f)
    except (json.JSONDecodeError, IOError):
        log("Checkpoint file corrupted, starting fresh")
        return {}

    if progress.get("script_hash") != script_hash:
        log("Script has been revised (hash mismatch) — wiping cached clips")
        # Wipe old clips but keep the temp dir
        temp_dir = os.path.dirname(progress_path)
        for f in os.listdir(temp_dir):
            if f.endswith(".mp4") or f == "progress.json":
                os.remove(os.path.join(temp_dir, f))
        return {}

    done_count = sum(1 for s in progress.get("scenes", {}).values() if s.get("status") == "done")
    log(f"Checkpoint loaded: {done_count} scenes already completed")
    return progress


def save_progress(progress_path, progress):
    """Atomically save checkpoint progress (write-then-rename to prevent corruption)."""
    tmp_path = progress_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(progress, f, indent=2)
    os.rename(tmp_path, progress_path)


def convert_ui_to_api_workflow(ui_workflow):
    """Convert ComfyUI UI workflow format to API format"""
    api_workflow = {}
    
    links_by_id = {}
    for link in ui_workflow.get("links", []):
        if len(link) >= 5:
            links_by_id[link[0]] = link
    
    SKIP_NODE_TYPES_API = {"MarkdownNote", "Note", "Reroute", "PrimitiveNode", "PrimitiveBoolean", "PrimitiveString", "PrimitiveInteger", "PrimitiveFloat"}
    
    for node in ui_workflow.get("nodes", []):
        node_id = str(node["id"])
        class_type = node["type"]
        
        if node.get("mode") == 4:
            continue
        
        if class_type in SKIP_NODE_TYPES_API:
            continue
        
        inputs = {}
        
        if "widgets_values" in node:
            widgets = node["widgets_values"]
            param_names = WIDGET_MAPPINGS.get(class_type, [])
            for i, value in enumerate(widgets):
                if i < len(param_names):
                    inputs[param_names[i]] = value
        
        # Map connections
        for inp in node.get("inputs", []):
            inp_name = inp.get("name")
            link_id = inp.get("link")
            if inp_name and link_id is not None and link_id in links_by_id:
                link = links_by_id[link_id]
                source_node_id = str(link[1])
                source_slot = link[2]
                inputs[inp_name] = [source_node_id, source_slot]
        
        api_workflow[node_id] = {
            "class_type": class_type,
            "inputs": inputs
        }
    
    return api_workflow

def update_workflow_params(workflow, prompt, seed, frames, width, height, turbo=True):
    """Update workflow parameters for a specific scene"""
    workflow = copy.deepcopy(workflow)
    
    for node_id, node in workflow.items():
        class_type = node.get("class_type", "")
        
        # Update prompt in CLIPTextEncode nodes
        if class_type == "CLIPTextEncode":
            # Check if this is a positive prompt node (typically 89, 99 in the workflow)
            if node_id in ["89", "99"]:
                node["inputs"]["text"] = prompt
        
        # Update video dimensions and length
        if class_type == "EmptyHunyuanLatentVideo":
            node["inputs"]["width"] = width
            node["inputs"]["height"] = height
            node["inputs"]["length"] = frames
        
        # Update seed and steps in KSamplerAdvanced
        if class_type == "KSamplerAdvanced":
            node["inputs"]["noise_seed"] = seed
            if turbo:
                node["inputs"]["steps"] = 4
            else:
                node["inputs"]["steps"] = 20
    
    return workflow

def submit_workflow(workflow):
    """Submit workflow to ComfyUI and return prompt_id"""
    url = f"{COMFYUI_URL}/prompt"
    payload = {"prompt": workflow}
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result.get("prompt_id")
    except Exception as e:
        log(f"Error submitting workflow: {e}")
        return None

def wait_for_completion(prompt_id, max_wait=MAX_WAIT_TIME):
    """Wait for workflow to complete"""
    url = f"{COMFYUI_URL}/history/{prompt_id}"
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            history = response.json()
            
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                if outputs:
                    return outputs
        except Exception as e:
            log(f"Error checking status: {e}")
        
        time.sleep(5)
    
    return None

def download_video(outputs, output_path):
    """Download generated video from ComfyUI"""
    for node_id, node_output in outputs.items():
        # Check for 'videos' key first, then 'images' (SaveVideo uses images for .mp4)
        video_info = None
        if "videos" in node_output and node_output["videos"]:
            video_info = node_output["videos"][0]
        elif "images" in node_output and node_output["images"]:
            img = node_output["images"][0]
            if isinstance(img, dict) and img.get("filename", "").endswith(".mp4"):
                video_info = img
        
        if video_info:
            filename = video_info["filename"]
            subfolder = video_info.get("subfolder", "")
            video_type = video_info.get("type", "output")
            
            # Build download URL
            url = f"{COMFYUI_URL}/view?filename={filename}&type={video_type}"
            if subfolder:
                url += f"&subfolder={subfolder}"
            
            try:
                response = requests.get(url, timeout=60, stream=True)
                response.raise_for_status()
                
                with open(output_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                return True
            except Exception as e:
                log(f"Error downloading video: {e}")
    
    return False

def download_image(outputs, output_path):
    """Download generated image from ComfyUI T2I workflow."""
    for node_id, node_output in outputs.items():
        if "images" in node_output and node_output["images"]:
            img = node_output["images"][0]
            if isinstance(img, dict) and not img.get("filename", "").endswith(".mp4"):
                filename = img["filename"]
                subfolder = img.get("subfolder", "")
                img_type = img.get("type", "output")

                url = f"{COMFYUI_URL}/view?filename={filename}&type={img_type}"
                if subfolder:
                    url += f"&subfolder={subfolder}"

                try:
                    response = requests.get(url, timeout=30)
                    response.raise_for_status()
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    return True
                except Exception as e:
                    log(f"Error downloading image: {e}")
    return False


def load_t2i_workflow(workflow_path, prompt, seed, width=1024, height=1024):
    """Load and configure T2I workflow for generating a reference image."""
    with open(workflow_path, "r") as f:
        ui_workflow = json.load(f)

    api_workflow = convert_ui_to_api_workflow(ui_workflow)
    workflow = copy.deepcopy(api_workflow)

    for node_id, node in workflow.items():
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})

        # Update prompt text
        if class_type == "CLIPTextEncode" and "text" in inputs:
            # Only update if it has a text input (positive prompt)
            if isinstance(inputs.get("text"), str) or (isinstance(inputs.get("text"), list) and len(inputs.get("text", [])) == 0):
                inputs["text"] = prompt

        # Update image dimensions
        if class_type in ("EmptySD3LatentImage", "EmptyLatentImage"):
            inputs["width"] = width
            inputs["height"] = height
            inputs["batch_size"] = 1

        # Update seed
        if class_type in ("KSampler", "KSamplerAdvanced"):
            inputs["seed"] = seed

    return workflow


def generate_reference_image(scene_num, image_prompt, output_dir):
    """Generate a reference image via ComfyUI T2I for an i2v scene.

    Returns the path to the generated image, or None on failure.
    """
    t2i_workflow_key = MODEL_PROFILES.get(ACTIVE_MODEL, {}).get("t2i_workflow")
    if not t2i_workflow_key:
        log(f"Scene {scene_num}: No T2I workflow configured, skipping reference image")
        return None

    t2i_path = os.path.join(WORKSPACE_DIR, t2i_workflow_key)
    if not os.path.exists(t2i_path):
        log(f"Scene {scene_num}: T2I workflow not found at {t2i_path}")
        return None

    output_path = os.path.join(output_dir, f"ref_image_{scene_num:03d}.png")

    for attempt in range(2):
        seed = random.randint(0, 2**32 - 1)
        workflow = load_t2i_workflow(t2i_path, image_prompt, seed)

        prompt_id = submit_workflow(workflow)
        if not prompt_id:
            log(f"Scene {scene_num}: T2I submit failed (attempt {attempt + 1})")
            continue

        outputs = wait_for_completion(prompt_id, max_wait=120)
        if outputs and download_image(outputs, output_path):
            file_size = os.path.getsize(output_path)
            if file_size > 5000:
                log(f"Scene {scene_num}: Reference image generated ({file_size // 1024}KB)")
                return output_path
            else:
                log(f"Scene {scene_num}: Reference image too small ({file_size}B), retrying")
        else:
            log(f"Scene {scene_num}: T2I generation failed (attempt {attempt + 1})")

    log(f"Scene {scene_num}: T2I failed after 2 attempts, will use t2v fallback")
    return None


def generate_all_reference_images(scenes, output_dir):
    """Pre-generate reference images for all i2v scenes that have image_prompt.

    Returns a dict mapping scene_number -> image_path.
    """
    ref_images = {}
    i2v_scenes = [s for s in scenes if s.get("image_prompt")]

    if not i2v_scenes:
        return ref_images

    log(f"T2I: Generating reference images for {len(i2v_scenes)} scenes...")
    os.makedirs(output_dir, exist_ok=True)

    for scene in i2v_scenes:
        scene_num = scene.get("scene_number", 0)
        image_prompt = scene["image_prompt"]

        # Check if already generated (checkpoint/resume)
        expected_path = os.path.join(output_dir, f"ref_image_{scene_num:03d}.png")
        if os.path.exists(expected_path) and os.path.getsize(expected_path) > 5000:
            log(f"Scene {scene_num}: Reusing existing reference image")
            ref_images[scene_num] = expected_path
            continue

        path = generate_reference_image(scene_num, image_prompt, output_dir)
        if path:
            ref_images[scene_num] = path

    log(f"T2I: Generated {len(ref_images)}/{len(i2v_scenes)} reference images")

    # Free GPU memory after T2I batch before video generation
    try:
        requests.post(f"{COMFYUI_URL}/free", json={"free_memory": True}, timeout=10)
        log("T2I: Freed GPU memory before video generation")
    except Exception:
        pass

    return ref_images


def generate_voiceover_track(scenes, output_path):
    """Generate full voiceover track from all scenes using ElevenLabs API.

    Concatenates all scene voiceover text into one script, generates a single
    audio file. This is more natural-sounding and cost-efficient than per-clip TTS.
    """
    if not ENABLE_VOICEOVER:
        return None

    # Build full voiceover script with natural pauses
    full_text = ""
    for scene in scenes:
        voiceover = scene.get("voiceover", "").strip()
        if voiceover:
            full_text += voiceover + " "

    full_text = full_text.strip()
    if not full_text:
        log("No voiceover text found in script")
        return None

    log(f"Generating voiceover: {len(full_text)} chars, ~{len(full_text.split())} words")

    # Try ElevenLabs first
    if ELEVENLABS_API_KEY:
        try:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": ELEVENLABS_API_KEY,
            }
            data = {
                "text": full_text,
                "model_id": ELEVENLABS_MODEL,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.3,
                }
            }

            response = requests.post(url, json=data, headers=headers, timeout=120)

            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                log(f"ElevenLabs voiceover generated: {output_path} ({os.path.getsize(output_path) / 1024:.0f} KB)")
                return output_path
            else:
                log(f"ElevenLabs API error {response.status_code}: {response.text[:200]}")
        except Exception as e:
            log(f"ElevenLabs voiceover failed: {e}")
    else:
        log("ELEVENLABS_API_KEY not set, skipping voiceover")

    # Fallback: edge-tts (free)
    try:
        import asyncio, edge_tts
        communicate = edge_tts.Communicate(full_text, "en-US-GuyNeural")
        asyncio.run(communicate.save(output_path))
        if os.path.exists(output_path):
            log(f"edge-tts voiceover generated: {output_path}")
            return output_path
    except ImportError:
        log("edge-tts not installed either, no voiceover available")
    except Exception as e:
        log(f"edge-tts voiceover failed: {e}")

    return None

def add_audio_to_clip(video_path, audio_path, output_path):
    """Add audio to video clip"""
    if not audio_path or not os.path.exists(audio_path):
        return video_path
    
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        output_path
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path
    except Exception as e:
        log(f"Error adding audio: {e}")
        return video_path

def _escape_drawtext(text):
    """Escape text for ffmpeg drawtext filter."""
    # Order matters: escape backslash first, then special chars
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")  # Replace apostrophe with unicode right single quote
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    text = text.replace('"', '\\"')
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace(";", "\\;")
    return text


def _detect_font():
    """Find a bold font on the system, fall back to ffmpeg default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return f"fontfile={path}:"
    return ""


def _overlay_title(video_path, text, position, output_path):
    """Large centered title overlay with shadow."""
    text = _escape_drawtext(text)
    font = _detect_font()

    if position == "lower_third":
        y_expr = "h*0.75"
    elif position == "top":
        y_expr = "h*0.12"
    else:
        y_expr = "(h-text_h)/2"

    vf = (
        f"drawtext={font}text='{text}':"
        f"fontsize=56:fontcolor=white:"
        f"borderw=3:bordercolor=black@0.8:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"enable='between(t,0.3,999)'"
    )

    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-c:a", "copy", output_path]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path
    except Exception as e:
        log(f"Error adding title overlay: {e}")
        return video_path


def _overlay_bullets(video_path, bullets, position, output_path):
    """Stacked bullet list overlay with semi-transparent background."""
    font = _detect_font()

    if position == "lower_third":
        base_y = "h*0.65"
    elif position == "top":
        base_y = "h*0.10"
    else:
        base_y = "h*0.35"

    # Dark background box
    n_lines = len(bullets[:4])
    box_h = 50 * n_lines + 30
    filters = [
        f"drawbox=x=w*0.08:y={base_y}-15:w=w*0.84:h={box_h}:"
        f"color=black@0.5:t=fill:enable='between(t,0.3,999)'"
    ]

    # Each bullet line
    for i, item in enumerate(bullets[:4]):
        text = _escape_drawtext(f"• {item}")
        y_offset = f"{base_y}+{i * 50}"
        filters.append(
            f"drawtext={font}text='{text}':"
            f"fontsize=34:fontcolor=white:"
            f"borderw=1:bordercolor=black@0.6:"
            f"x=w*0.12:y={y_offset}:"
            f"enable='between(t,0.3,999)'"
        )

    vf = ",".join(filters)
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-c:a", "copy", output_path]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path
    except Exception as e:
        log(f"Error adding bullet overlay: {e}")
        return video_path


def _overlay_stat(video_path, text, position, output_path):
    """Big stat/number callout overlay with glow."""
    text = _escape_drawtext(text)
    font = _detect_font()

    if position == "lower_third":
        y_expr = "h*0.70"
    elif position == "top":
        y_expr = "h*0.15"
    else:
        y_expr = "(h-text_h)/2"

    # Glow layer (larger, blurred) + sharp layer on top
    vf = (
        f"drawtext={font}text='{text}':"
        f"fontsize=84:fontcolor=white@0.3:"
        f"borderw=6:bordercolor=white@0.15:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"enable='between(t,0.2,999)',"
        f"drawtext={font}text='{text}':"
        f"fontsize=80:fontcolor=white:"
        f"borderw=3:bordercolor=black@0.8:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"enable='between(t,0.3,999)'"
    )

    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-c:a", "copy", output_path]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path
    except Exception as e:
        log(f"Error adding stat overlay: {e}")
        return video_path


def apply_scene_overlays(video_path, scene, output_path):
    """Apply text overlays from scene JSON to video clip.

    Supports three overlay types (max one per scene):
    - overlay_title: Large centered text (feature names, section headers)
    - overlay_bullets: Stacked bullet list (feature lists, steps)
    - overlay_stat: Big stat/number callout (pricing, metrics)
    """
    title = scene.get("overlay_title")
    bullets = scene.get("overlay_bullets")
    stat = scene.get("overlay_stat")
    position = scene.get("overlay_position", "center")

    if not any([title, bullets, stat]):
        return video_path

    if title:
        return _overlay_title(video_path, title, position, output_path)
    elif bullets:
        return _overlay_bullets(video_path, bullets, position, output_path)
    elif stat:
        return _overlay_stat(video_path, stat, position, output_path)
    return video_path


def add_text_overlay(video_path, text, output_path):
    """Add text overlay to video (legacy — kept for backward compatibility)."""
    return _overlay_title(video_path, text, "lower_third", output_path)

def stitch_clips(clips, output_path):
    """Stitch clips together via ffmpeg concat."""
    concat_file = f"{TEMP_DIR}/concat.txt"
    with open(concat_file, "w") as f:
        for clip in clips:
            f.write(f"file '{clip}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", output_path]
    subprocess.run(cmd, capture_output=True)
    return output_path

def strip_audio(video_path, output_path):
    """Strip all audio from video file.

    Used when the video model (e.g. LTX 2.3) generates its own audio that
    would conflict with our controlled audio pipeline (ElevenLabs voiceover
    + halal background music).
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-an",  # Remove all audio streams
        "-c:v", "copy",  # Don't re-encode video
        output_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        log(f"Stripped model-generated audio from stitched video")
        return output_path
    except Exception as e:
        log(f"Warning: Could not strip audio: {e}")
        return video_path


def add_background_music(video_path, output_path):
    """Add background music (halal: ambient/nasheed only, no instruments) to video.

    Handles both cases: video with existing audio (voiceover) and silent video.
    Music is mixed at low volume under voiceover, with fade-out at end.
    """
    if not ENABLE_BACKGROUND_MUSIC:
        return video_path

    # Find music file
    music_files = list(Path(BACKGROUND_MUSIC_DIR).glob("*.mp3")) if os.path.exists(BACKGROUND_MUSIC_DIR) else []

    if not music_files:
        log("No background music found in assets/music/, skipping")
        return video_path

    music_file = random.choice(music_files)
    log(f"Adding background music: {music_file.name}")

    # Get video duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True
    )
    duration = float(probe.stdout.strip())

    # Check if video already has audio (voiceover)
    audio_check = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
        capture_output=True, text=True
    )
    has_audio = bool(audio_check.stdout.strip())

    if has_audio:
        # Mix music under existing voiceover (music at 15% volume)
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", str(music_file),
            "-filter_complex",
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{duration},volume=0.15,afade=t=out:st={duration-3}:d=3[music];"
            f"[0:a][music]amix=inputs=2:duration=first:weights=1 0.15[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-shortest",
            output_path
        ]
    else:
        # No voiceover — music is the only audio (at moderate volume)
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", str(music_file),
            "-filter_complex",
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{duration},volume=0.4,afade=t=out:st={duration-3}:d=3[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-shortest",
            output_path
        ]

    try:
        subprocess.run(cmd, capture_output=True, check=True)
        log(f"Background music added successfully")
        return output_path
    except Exception as e:
        log(f"Error adding background music: {e}")
        return video_path

def validate_clip(output_path, expected_duration_sec):
    """Validate generated clip is not corrupt or truncated."""
    if not os.path.exists(output_path):
        return False, "File not found"

    file_size = os.path.getsize(output_path)
    if file_size < 10000:  # 10KB minimum
        return False, f"File too small ({file_size} bytes)"

    # Check actual duration via ffprobe
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", output_path],
            capture_output=True, text=True, timeout=10
        )
        actual_duration = float(result.stdout.strip())
        if actual_duration < expected_duration_sec * 0.5:
            return False, f"Duration too short ({actual_duration:.1f}s vs expected {expected_duration_sec}s)"
    except Exception:
        pass  # ffprobe failure is not fatal

    return True, "OK"


def upscale_clip(input_path, output_path, target_width=None, target_height=None):
    """Upscale a video clip to target resolution.

    Uses Real-ESRGAN (GPU-accelerated) if available, falls back to ffmpeg lanczos.
    Returns output_path on success, input_path on failure.
    """
    if not ENABLE_UPSCALE:
        return input_path

    tw = target_width or TARGET_WIDTH
    th = target_height or TARGET_HEIGHT

    # Option A: Real-ESRGAN (best quality)
    realesrgan = shutil.which("realesrgan-ncnn-vulkan")
    if realesrgan:
        # Real-ESRGAN works on image frames, so we extract → upscale → reassemble
        frames_dir = output_path + "_frames"
        upscaled_dir = output_path + "_upscaled"
        os.makedirs(frames_dir, exist_ok=True)
        os.makedirs(upscaled_dir, exist_ok=True)

        try:
            # Extract frames
            subprocess.run(
                ["ffmpeg", "-y", "-i", input_path, f"{frames_dir}/frame_%05d.png"],
                capture_output=True, check=True, timeout=120
            )

            # Upscale frames with Real-ESRGAN
            subprocess.run(
                [realesrgan, "-i", frames_dir, "-o", upscaled_dir,
                 "-n", "realesrgan-x4plus", "-s", "4", "-f", "png"],
                capture_output=True, check=True, timeout=600
            )

            # Get original fps
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v",
                 "-show_entries", "stream=r_frame_rate",
                 "-of", "default=noprint_wrappers=1:nokey=1", input_path],
                capture_output=True, text=True, timeout=10
            )
            fps = probe.stdout.strip()

            # Reassemble at target resolution
            subprocess.run(
                ["ffmpeg", "-y", "-framerate", fps,
                 "-i", f"{upscaled_dir}/frame_%05d.png",
                 "-vf", f"scale={tw}:{th}",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                 "-pix_fmt", "yuv420p", output_path],
                capture_output=True, check=True, timeout=300
            )

            # Cleanup frame dirs
            import shutil as sh
            sh.rmtree(frames_dir, ignore_errors=True)
            sh.rmtree(upscaled_dir, ignore_errors=True)

            log(f"  Upscaled via Real-ESRGAN → {tw}x{th}")
            return output_path

        except Exception as e:
            log(f"  Real-ESRGAN failed ({e}), falling back to ffmpeg")
            import shutil as sh
            sh.rmtree(frames_dir, ignore_errors=True)
            sh.rmtree(upscaled_dir, ignore_errors=True)

    # Option B: ffmpeg lanczos (always available)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-vf", f"scale={tw}:{th}:flags=lanczos",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p", output_path],
            capture_output=True, check=True, timeout=120
        )
        log(f"  Upscaled via ffmpeg lanczos → {tw}x{th}")
        return output_path
    except Exception as e:
        log(f"  Upscale failed ({e}), keeping original resolution")
        return input_path


def interpolate_fps(input_path, output_path, target_fps=None):
    """Interpolate video to target FPS.

    Uses RIFE (GPU-accelerated) if available, falls back to ffmpeg minterpolate.
    Returns output_path on success, input_path on failure.
    """
    if not ENABLE_FRAME_INTERPOLATION:
        return input_path

    tfps = target_fps or TARGET_FPS

    # Check current fps — skip if already at target
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=10
        )
        fps_str = probe.stdout.strip()
        if "/" in fps_str:
            num, den = fps_str.split("/")
            current_fps = float(num) / float(den)
        else:
            current_fps = float(fps_str)
        if current_fps >= tfps:
            log(f"  FPS already {current_fps:.0f}, skipping interpolation")
            return input_path
    except Exception:
        pass

    # Option A: RIFE (best quality)
    rife = shutil.which("rife-ncnn-vulkan")
    if rife:
        frames_dir = output_path + "_src_frames"
        interp_dir = output_path + "_interp_frames"
        os.makedirs(frames_dir, exist_ok=True)
        os.makedirs(interp_dir, exist_ok=True)

        try:
            # Extract frames
            subprocess.run(
                ["ffmpeg", "-y", "-i", input_path, f"{frames_dir}/frame_%05d.png"],
                capture_output=True, check=True, timeout=120
            )

            # Calculate multiplier (e.g. 16fps → 30fps ≈ 2x)
            multiplier = max(2, round(tfps / current_fps))

            # Interpolate with RIFE
            subprocess.run(
                [rife, "-i", frames_dir, "-o", interp_dir,
                 "-m", "rife-v4.6", "-n", str(multiplier)],
                capture_output=True, check=True, timeout=600
            )

            # Reassemble
            subprocess.run(
                ["ffmpeg", "-y", "-framerate", str(tfps),
                 "-i", f"{interp_dir}/frame_%05d.png",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                 "-pix_fmt", "yuv420p", output_path],
                capture_output=True, check=True, timeout=300
            )

            import shutil as sh
            sh.rmtree(frames_dir, ignore_errors=True)
            sh.rmtree(interp_dir, ignore_errors=True)

            log(f"  Interpolated via RIFE → {tfps}fps")
            return output_path

        except Exception as e:
            log(f"  RIFE failed ({e}), falling back to ffmpeg")
            import shutil as sh
            sh.rmtree(frames_dir, ignore_errors=True)
            sh.rmtree(interp_dir, ignore_errors=True)

    # Option B: ffmpeg minterpolate (always available)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-vf", f"minterpolate=fps={tfps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p", output_path],
            capture_output=True, check=True, timeout=300
        )
        log(f"  Interpolated via ffmpeg minterpolate → {tfps}fps")
        return output_path
    except Exception as e:
        log(f"  Interpolation failed ({e}), keeping original FPS")
        return input_path


def upload_image_to_comfyui(image_path):
    """Upload a reference image to ComfyUI for i2v workflow.

    Returns the filename as stored by ComfyUI, or None on failure.
    """
    if not os.path.exists(image_path):
        log(f"Reference image not found: {image_path}")
        return None

    try:
        with open(image_path, "rb") as f:
            files = {"image": (os.path.basename(image_path), f, "image/png")}
            response = requests.post(
                f"{COMFYUI_URL}/upload/image",
                files=files,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            filename = result.get("name", os.path.basename(image_path))
            log(f"Image uploaded to ComfyUI: {filename}")
            return filename
    except Exception as e:
        log(f"Failed to upload image to ComfyUI: {e}")
        return None


def load_i2v_workflow(workflow_path, prompt, seed, frames, width, height, image_name, turbo=True):
    """Load and configure i2v workflow for a character-consistent scene.

    The i2v workflow uses WanImageToVideo instead of EmptyHunyuanLatentVideo,
    and requires a LoadImage node pointing to the uploaded reference image.
    """
    with open(workflow_path, "r") as f:
        ui_workflow = json.load(f)

    api_workflow = convert_ui_to_api_workflow(ui_workflow)

    workflow = copy.deepcopy(api_workflow)

    for node_id, node in workflow.items():
        class_type = node.get("class_type", "")

        # Update prompt in CLIPTextEncode nodes
        if class_type == "CLIPTextEncode":
            if node_id in ["89", "99"]:
                node["inputs"]["text"] = prompt

        # Update image in LoadImage node
        if class_type == "LoadImage":
            node["inputs"]["image"] = image_name

        # Update video dimensions and length in WanImageToVideo
        if class_type == "WanImageToVideo":
            node["inputs"]["width"] = width
            node["inputs"]["height"] = height
            node["inputs"]["length"] = frames

        # Update seed and steps in KSamplerAdvanced
        if class_type == "KSamplerAdvanced":
            node["inputs"]["noise_seed"] = seed
            if turbo:
                node["inputs"]["steps"] = 4
            else:
                node["inputs"]["steps"] = 20

    return workflow


def generate_scene(scene_num, prompt, duration_sec, seed=None, tool_name=None, progress=None, progress_path=None,
                   workflow_type="t2v", reference_image=None):
    """Generate a single video scene with checkpoint-aware resume."""
    frames = duration_sec * FPS  # Proper duration from script
    output_path = f"{TEMP_DIR}/clip_{scene_num:03d}.mp4"

    # Checkpoint-aware skip: reuse clip only if prompt hasn't changed
    prompt_hash = compute_hash(prompt)
    scene_key = str(scene_num)

    if progress and scene_key in progress.get("scenes", {}):
        entry = progress["scenes"][scene_key]
        clip_file = os.path.join(TEMP_DIR, entry.get("clip", ""))
        if (entry.get("prompt_hash") == prompt_hash
                and entry.get("status") == "done"
                and os.path.exists(clip_file)
                and os.path.getsize(clip_file) > 10000):
            log(f"Scene {scene_num}: Unchanged, reusing cached clip ({os.path.getsize(clip_file)//1024}KB)")
            return clip_file
        elif entry.get("prompt_hash") != prompt_hash:
            log(f"Scene {scene_num}: Prompt revised, regenerating")

    if seed is None:
        seed = random.randint(0, 2**32 - 1)

    log(f"Scene {scene_num}: Starting generation")
    log(f"  Prompt: {prompt[:60]}...")
    log(f"  Duration: {duration_sec}s ({frames} frames)")
    if workflow_type == "i2v":
        log(f"  Workflow: i2v (character scene)")

    # Select and load workflow based on type
    use_i2v = False
    uploaded_image_name = None

    if workflow_type == "i2v" and reference_image:
        i2v_workflow_key = MODEL_PROFILES.get(ACTIVE_MODEL, {}).get("i2v_workflow")
        if i2v_workflow_key:
            i2v_path = os.path.join(WORKSPACE_DIR, i2v_workflow_key)
            if os.path.exists(i2v_path):
                uploaded_image_name = upload_image_to_comfyui(reference_image)
                if uploaded_image_name:
                    use_i2v = True
                else:
                    log(f"  Warning: Image upload failed, falling back to t2v")
            else:
                log(f"  Warning: i2v workflow not found at {i2v_path}, falling back to t2v")
        else:
            log(f"  Warning: No i2v workflow configured for {ACTIVE_MODEL}, falling back to t2v")

    if use_i2v:
        i2v_workflow_path = os.path.join(WORKSPACE_DIR, MODEL_PROFILES[ACTIVE_MODEL]["i2v_workflow"])
        workflow = load_i2v_workflow(
            i2v_workflow_path,
            prompt, seed, frames, WIDTH, HEIGHT, uploaded_image_name, TURBO_MODE
        )
    else:
        # Standard t2v workflow
        with open(WORKFLOW_FILE, "r") as f:
            ui_workflow = json.load(f)
        api_workflow = convert_ui_to_api_workflow(ui_workflow)
        workflow = update_workflow_params(api_workflow, prompt, seed, frames, WIDTH, HEIGHT, TURBO_MODE)
    
    for attempt in range(MAX_RETRIES):
        log(f"Scene {scene_num}: Attempt {attempt + 1}/{MAX_RETRIES}")

        # Before retry, try to diagnose and fix common issues
        if attempt > 0:
            log(f"  Running auto-diagnostics before retry...")
            auto_fix_errors()

        prompt_id = submit_workflow(workflow)
        if not prompt_id:
            log(f"  Failed to submit workflow")
            # Check if ComfyUI is down
            try:
                r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
                if r.status_code != 200:
                    log("  ComfyUI not responding, waiting 30s...")
                    time.sleep(30)
            except Exception:
                log("  ComfyUI unreachable, waiting 30s...")
                time.sleep(30)
            continue

        log(f"  Submitted, prompt_id: {prompt_id}")

        # Wait for completion
        start_wait = time.time()
        while time.time() - start_wait < MAX_WAIT_TIME:
            time.sleep(5)
            elapsed = int(time.time() - start_wait)

            outputs = wait_for_completion(prompt_id, max_wait=5)
            if outputs:
                # Download video
                if download_video(outputs, output_path):
                    # Quality gate: validate clip before accepting
                    valid, reason = validate_clip(output_path, duration_sec)
                    if not valid:
                        log(f"Scene {scene_num}: Quality check failed: {reason}")
                        if attempt < MAX_RETRIES - 1:
                            log(f"  Retrying with modified prompt...")
                            # Modify seed for retry
                            seed = random.randint(0, 2**32 - 1)
                            if use_i2v:
                                workflow = load_i2v_workflow(
                                    os.path.join(WORKSPACE_DIR, MODEL_PROFILES["wan22"]["i2v_workflow"]),
                                    prompt, seed, frames, WIDTH, HEIGHT, uploaded_image_name, TURBO_MODE
                                )
                            else:
                                workflow = update_workflow_params(api_workflow, prompt, seed, frames, WIDTH, HEIGHT, TURBO_MODE)
                            break  # Break inner wait loop, retry in outer loop
                        else:
                            log(f"  Accepting despite quality issue (max retries reached)")

                    file_size = os.path.getsize(output_path)
                    log(f"Scene {scene_num}: Complete! ({file_size//1024}KB)")

                    # Post-processing: upscale + frame interpolation
                    final_clip = output_path
                    if ENABLE_UPSCALE:
                        upscaled_path = f"{TEMP_DIR}/clip_{scene_num:03d}_upscaled.mp4"
                        final_clip = upscale_clip(final_clip, upscaled_path)
                    if ENABLE_FRAME_INTERPOLATION:
                        interpolated_path = f"{TEMP_DIR}/clip_{scene_num:03d}_interpolated.mp4"
                        final_clip = interpolate_fps(final_clip, interpolated_path)

                    # Save checkpoint
                    final_clip_name = os.path.basename(final_clip)
                    if progress is not None and progress_path:
                        progress.setdefault("scenes", {})[scene_key] = {
                            "prompt_hash": prompt_hash,
                            "clip": final_clip_name,
                            "status": "done"
                        }
                        save_progress(progress_path, progress)
                    return final_clip

            if elapsed % 30 == 0:
                log(f"  Waiting... {elapsed}s elapsed")

        log(f"  Timeout after {MAX_WAIT_TIME}s, retrying...")

    log(f"Scene {scene_num}: FAILED after {MAX_RETRIES} attempts")
    return None


def auto_fix_errors():
    """Attempt to diagnose and fix common issues before retry."""
    # 1. Free GPU memory
    try:
        r = requests.post(f"{COMFYUI_URL}/free", json={"free_memory": True}, timeout=10)
        if r.status_code == 200:
            log("  Auto-fix: Freed GPU memory")
    except Exception:
        log("  Auto-fix: Could not free GPU memory (ComfyUI may be down)")

    # 2. Check ComfyUI queue — clear stuck jobs
    try:
        r = requests.get(f"{COMFYUI_URL}/queue", timeout=5)
        if r.status_code == 200:
            queue = r.json()
            pending = len(queue.get("queue_pending", []))
            running = len(queue.get("queue_running", []))
            if pending > 5 or running > 1:
                log(f"  Auto-fix: Queue congested ({pending} pending, {running} running), clearing...")
                requests.post(f"{COMFYUI_URL}/queue", json={"clear": True}, timeout=5)
    except Exception:
        pass

    # 3. Brief cooldown
    time.sleep(5)

def main():
    """Main generation pipeline.

    Usage: python generate_video_v5.py <script_file>
    Example: python generate_video_v5.py scripts/script-20260308-1430.json

    Paths are derived automatically:
      TEMP_DIR = temp/clips-<script_basename>
      LOG_FILE = LOG/video-generation-<script_basename>.log
    """
    global SCRIPT_FILE, TEMP_DIR, LOG_FILE

    import sys
    if len(sys.argv) > 1:
        SCRIPT_FILE = sys.argv[1]
    else:
        # Fall back to finding the most recent script file
        scripts_dir = os.path.join(WORKSPACE_DIR, "scripts")
        script_files = sorted(Path(scripts_dir).glob("script-*.json"), key=os.path.getmtime, reverse=True)
        if script_files:
            SCRIPT_FILE = str(script_files[0])
        else:
            print("ERROR: No script file provided and no script-*.json found in scripts/")
            print("Usage: python generate_video_v5.py <script_file>")
            sys.exit(1)

    # Derive temp and log paths from script filename
    script_basename = Path(SCRIPT_FILE).stem  # e.g. "script-20260308-1430"
    TEMP_DIR = os.path.join(WORKSPACE_DIR, f"temp/clips-{script_basename}")
    LOG_FILE = os.path.join(WORKSPACE_DIR, f"LOG/video-generation-{script_basename}.log")

    log("=" * 60)
    log("VIDEO GENERATION v5")
    log("=" * 60)

    # Create directories
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Detect GPU and select best model
    detect_gpu_and_select_model()

    # Load script
    log(f"Loading script: {SCRIPT_FILE}")
    with open(SCRIPT_FILE, "r") as f:
        script = json.load(f)

    scenes = script.get("scenes", [])
    total_scenes = len(scenes)
    video_title = script.get("metadata", {}).get("title", "Untitled")
    video_format = script.get("metadata", {}).get("format", "long")

    log(f"Title: {video_title}")
    log(f"Format: {video_format}")
    log(f"Total scenes: {total_scenes}")

    # Override resolution for shorts (9:16 vertical)
    if video_format == "short":
        global WIDTH, HEIGHT, TARGET_WIDTH, TARGET_HEIGHT
        WIDTH, HEIGHT = HEIGHT, WIDTH  # Swap to vertical
        TARGET_WIDTH, TARGET_HEIGHT = TARGET_HEIGHT, TARGET_WIDTH
        log(f"Shorts mode: resolution set to {WIDTH}x{HEIGHT} (9:16 vertical)")

    # Pre-flight: catch broken scripts before burning GPU hours
    is_short = video_format == "short"
    min_scenes = 5 if is_short else 10
    min_duration = 25 if is_short else 60

    if not scenes or len(scenes) < min_scenes:
        log(f"ABORT: Script has {len(scenes)} scenes (need >= {min_scenes}). Fix script and re-run.")
        sys.exit(1)

    empty_prompts = [s.get("scene_number", i) for i, s in enumerate(scenes, 1) if not s.get("visual_prompt", "").strip()]
    if empty_prompts:
        log(f"ABORT: {len(empty_prompts)} scenes have empty visual_prompt: {empty_prompts[:5]}. Fix script and re-run.")
        sys.exit(1)

    total_duration = sum(s.get("duration", 8) for s in scenes)
    if total_duration < min_duration:
        log(f"ABORT: Total duration {total_duration}s is under {min_duration}s. Script likely incomplete.")
        sys.exit(1)

    log(f"Pre-flight passed: {len(scenes)} scenes, {total_duration}s total")
    
    # Settings
    log(f"Resolution: {WIDTH}x{HEIGHT}")
    log(f"Turbo mode: {TURBO_MODE}")
    log(f"Voiceover: {ENABLE_VOICEOVER}")
    log(f"Background music: {ENABLE_BACKGROUND_MUSIC}")
    log(f"Upscale: {ENABLE_UPSCALE} ({TARGET_WIDTH}x{TARGET_HEIGHT})")
    log(f"Frame interpolation: {ENABLE_FRAME_INTERPOLATION} ({TARGET_FPS}fps)")

    # Checkpoint setup — enables resume after crash and smart invalidation on revision
    with open(SCRIPT_FILE, "r") as sf:
        script_hash = compute_hash(sf.read())
    progress_path = os.path.join(TEMP_DIR, "progress.json")
    progress = load_progress(progress_path, SCRIPT_FILE, script_hash)
    progress["script_file"] = SCRIPT_FILE
    progress["script_hash"] = script_hash
    progress.setdefault("scenes", {})
    save_progress(progress_path, progress)

    # Generate reference images for scenes with image_prompt (T2I pass)
    ref_image_dir = os.path.join(TEMP_DIR, "ref_images")
    ref_images = generate_all_reference_images(scenes, ref_image_dir)

    # Generate each scene
    clips = []
    failed = []
    reused = 0

    for i, scene in enumerate(scenes, 1):
        scene_num = scene.get("scene_number", i)
        duration = scene.get("duration", 8)  # Get from script
        prompt = scene.get("visual_prompt", "")
        voiceover = scene.get("voiceover", "")
        tool_name = scene.get("tool_name", None)  # Extract tool name if present

        wf_type = scene.get("workflow_type", "t2v")
        ref_image = scene.get("reference_image", None)

        # Use T2I-generated reference image if available
        if scene_num in ref_images:
            ref_image = ref_images[scene_num]
            wf_type = "i2v"

        scenes_before = len(progress.get("scenes", {}))
        clip_path = generate_scene(scene_num, prompt, duration, tool_name=tool_name,
                                   progress=progress, progress_path=progress_path,
                                   workflow_type=wf_type, reference_image=ref_image)
        if clip_path and len(progress.get("scenes", {})) == scenes_before:
            reused += 1
        
        if clip_path:
            # Apply scene overlays (title, bullets, stat) or legacy tool_name
            overlay_path = f"{TEMP_DIR}/clip_overlay_{scene_num:03d}.mp4"
            if any(scene.get(k) for k in ("overlay_title", "overlay_bullets", "overlay_stat")):
                clip_path = apply_scene_overlays(clip_path, scene, overlay_path)
            elif tool_name:
                clip_path = add_text_overlay(clip_path, tool_name, overlay_path)

            clips.append(clip_path)
        else:
            failed.append(scene_num)
    
    log(f"Generation complete. Reused: {reused}, Generated: {len(clips) - reused}, Failed: {failed if failed else 'None'}")
    
    # Stitch clips
    log("Stitching clips together...")
    log(f"Found {len(clips)} clips to stitch")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    video_prefix = "short" if video_format == "short" else "video"
    raw_output = f"{TEMP_DIR}/raw_{timestamp}.mp4"

    stitch_clips(clips, raw_output)

    # Strip model-generated audio if the model produces its own (e.g. LTX 2.3)
    # We use our own audio pipeline: ElevenLabs voiceover + halal background music
    if ACTIVE_MODEL and MODEL_PROFILES.get(ACTIVE_MODEL, {}).get("has_audio", False):
        stripped_output = f"{TEMP_DIR}/stripped_{timestamp}.mp4"
        raw_output = strip_audio(raw_output, stripped_output)

    # Generate full voiceover track (single API call for entire script)
    voiceover_path = f"{TEMP_DIR}/voiceover_{timestamp}.mp3"
    voiceover_track = generate_voiceover_track(scenes, voiceover_path)

    # Mix: video + voiceover + background music
    final_output = f"{OUTPUT_DIR}/{video_prefix}-{timestamp}.mp4"
    current = raw_output

    if voiceover_track:
        voiced_output = f"{TEMP_DIR}/voiced_{timestamp}.mp4"
        current = add_audio_to_clip(current, voiceover_track, voiced_output)

    if ENABLE_BACKGROUND_MUSIC:
        final_output = add_background_music(current, final_output)
    else:
        if current != final_output:
            os.rename(current, final_output)
    
    file_size = os.path.getsize(final_output)
    log(f"Video created: {final_output}")
    log(f"Size: {file_size / 1024 / 1024:.1f} MB")

    # Checkpoint served its purpose — clean up
    if os.path.exists(progress_path):
        os.remove(progress_path)
        log("Checkpoint file cleaned up")
    
    # Create thumbnail from first frame
    thumbnail_path = f"{OUTPUT_DIR}/thumbnail-{timestamp}.png"
    thumb_scale = "720:1280" if video_format == "short" else "1280:720"
    subprocess.run([
        "ffmpeg", "-y", "-i", final_output, "-vframes", "1",
        "-vf", f"scale={thumb_scale}", thumbnail_path
    ], capture_output=True)
    
    log(f"Thumbnail created: {thumbnail_path}")
    
    log("=" * 60)
    log("VIDEO GENERATION COMPLETE")
    log(f"Video: {final_output}")
    log(f"Thumbnail: {thumbnail_path}")
    log("=" * 60)
    
    return final_output

if __name__ == "__main__":
    main()
