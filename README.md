# RunPod ComfyUI Video Worker

RunPod Serverless worker for generating videos via ComfyUI with Wan 2.2 and LTX 2.3 models.

Built for the [FutureScope](https://github.com/eamon831/futurescope) autonomous YouTube pipeline.

## How It Works

1. RunPod Serverless receives a job with a script JSON (scenes + metadata)
2. `start.sh` boots ComfyUI with models from a Network Volume
3. `handler.py` runs `generate_video_v5.py` which generates video clips via ComfyUI
4. Final video + thumbnail are uploaded to Cloudflare R2
5. R2 URLs returned in the job response

## Prerequisites

- RunPod account with API key
- Cloudflare R2 bucket (for video storage)
- ElevenLabs API key (for voiceover)
- Docker Hub account (for pushing the image)

## Setup

### 1. Create a Network Volume

On RunPod dashboard, create a Network Volume (**150GB**, datacenter: **EU-RO-1**) and populate it via a temp pod:

```
/runpod-volume/
  models/
    diffusion_models/
      wan2.2_t2v_14B_fp8_scaled.safetensors
      wan2.2_i2v_14B_fp8_scaled.safetensors
      ltx-video-2-1-fp8-unet.safetensors
    loras/
      wan2.2_lightx2v_4steps_lora_v1.safetensors
    clip/
      (CLIP models for your workflows)
    vae/
      (VAE models for your workflows)
  music/
    (halal background music .mp3 files)
  workspace/
    (empty - working directory for temp files)
```

### 2. Build the Docker Image

Copy `generate_video_v5.py` from the futurescope repo into this directory before building:

```bash
cp /path/to/futurescope/scripts/generate_video_v5.py .
```

Build (must be on x86 Linux, not Apple Silicon):

```bash
docker build -t yourdockerhub/runpod-comfyui-worker:latest .
docker push yourdockerhub/runpod-comfyui-worker:latest
```

### 3. Create Serverless Endpoint

On RunPod dashboard:

| Setting | Value |
|---------|-------|
| Docker image | `yourdockerhub/runpod-comfyui-worker:latest` |
| GPU | RTX 5090 (32GB) |
| Datacenter | EU-RO-1 (same as Network Volume) |
| Network Volume | Attach the 150GB volume from step 1 |
| Max workers | 1 |
| Execution timeout | 7200 (2 hours) |
| Idle timeout | 300 (5 min, for FlashBoot) |
| FlashBoot | Enabled |

### 4. Test

```bash
curl -X POST "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/run" \
  -H "Authorization: Bearer YOUR_RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "script": {
        "metadata": {"title": "Test", "format": "short", "total_duration": "0:30", "total_scenes": 5},
        "scenes": [
          {"scene_number": 1, "duration": 6, "voiceover": "Hello world", "visual_prompt": "A futuristic city skyline at sunset, cinematic", "workflow_type": "t2v"}
        ]
      },
      "ELEVENLABS_API_KEY": "your-key",
      "R2_ACCOUNT_ID": "your-account",
      "R2_ACCESS_KEY": "your-key",
      "R2_SECRET_KEY": "your-secret",
      "R2_BUCKET": "your-bucket",
      "R2_PUBLIC_URL": "https://your-r2-url"
    }
  }'
```

Check status:
```bash
curl "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/status/JOB_ID" \
  -H "Authorization: Bearer YOUR_RUNPOD_API_KEY"
```

## Job Input

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `script` | object | Yes | Full script JSON with `metadata` and `scenes` |
| `ELEVENLABS_API_KEY` | string | Yes | ElevenLabs API key for voiceover |
| `R2_ACCOUNT_ID` | string | Yes | Cloudflare R2 account ID |
| `R2_ACCESS_KEY` | string | Yes | R2 access key |
| `R2_SECRET_KEY` | string | Yes | R2 secret key |
| `R2_BUCKET` | string | Yes | R2 bucket name |
| `R2_PUBLIC_URL` | string | No | R2 public URL prefix |
| `ELEVENLABS_VOICE_ID` | string | No | Voice ID (default: Adam) |
| `VIDEO_MODEL` | string | No | Force model: `ltx23` or `wan22` (default: auto-detect) |
| `ENABLE_UPSCALE` | string | No | `"0"` to disable upscaling |
| `ENABLE_FRAME_INTERPOLATION` | string | No | `"0"` to disable |

## Job Output

```json
{
  "status": "success",
  "video_url": "https://your-r2-url/videos/video-20260318-143052.mp4",
  "thumbnail_url": "https://your-r2-url/videos/thumbnail-20260318-143052.png",
  "video_filename": "video-20260318-143052.mp4",
  "log_tail": "... last 1000 chars of generation log ..."
}
```

## Architecture

```
RunPod Serverless Worker
┌──────────────────────────────────────────┐
│  start.sh                                │
│    ├── Symlink /runpod-volume/models     │
│    ├── Start ComfyUI (background)        │
│    └── Start handler.py                  │
│                                          │
│  handler.py                              │
│    ├── Wait for ComfyUI ready            │
│    ├── Write script JSON to /tmp         │
│    ├── Set env vars from job input       │
│    ├── Run generate_video_v5.py          │
│    ├── Upload video + thumb to R2        │
│    └── Return R2 URLs                    │
│                                          │
│  Network Volume (/runpod-volume/)        │
│    ├── models/ (Wan 2.2, LTX 2.3, etc.) │
│    ├── music/ (background music)         │
│    └── workspace/ (temp files, resume)   │
└──────────────────────────────────────────┘
```

## Research

See [docs/research.md](docs/research.md) for detailed RunPod Serverless research including FlashBoot, costs, and configuration details.
