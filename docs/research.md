# RunPod Serverless Research

Research findings for deploying ComfyUI video generation on RunPod Serverless.

## FlashBoot

- Optimization layer that retains worker state after spin-down
- Cold start times: sub-250ms for popular endpoints, P95 < 2.3s, P90 < 2s
- Works better with consistent request volume
- Free, no extra cost — enabled per endpoint
- Container state is cached, so ComfyUI + loaded models can survive spin-down
- Source: https://www.runpod.io/blog/introducing-flashboot-serverless-cold-start

## Endpoint Configuration

| Setting | Range | Our Config |
|---------|-------|------------|
| Execution timeout | 5s — 7 days | 7200s (2 hours) |
| Job TTL | 10s — 7 days | 24 hours (default) |
| Idle timeout | default 5s | 300s (5 min, keeps FlashBoot warm) |
| Active (warm) workers | 0+ | 0 (rely on FlashBoot) |
| Max workers | 1+ | 1 |
| GPUs per worker | 1+ | 1 |
| Auto-scaling | queue delay or request count | queue delay (4s threshold) |

Source: https://docs.runpod.io/serverless/endpoints/endpoint-configurations

## Network Volumes

- Mount at `/runpod-volume/` inside serverless workers
- Persist across worker restarts
- Recommended for models (avoids baking 25GB+ into Docker image)
- Attach via: Serverless > Endpoint > Manage > Edit > Advanced > Network Volumes
- Add latency vs NVMe but acceptable for model loading (one-time at startup)
- - **Our config:** 200GB, EU-RO-1, standard storage ($14/month)
- Source: https://docs.runpod.io/storage/network-volumes

## Official ComfyUI Worker

- Repo: https://github.com/runpod-workers/worker-comfyui
- Pre-built images: `runpod/worker-comfyui:<version>-base` (no models)
- Image-only out of the box — no video generation support
- Custom nodes must be added via Dockerfile
- Workflows submitted as JSON to `/run` or `/runsync` endpoints
- Output: base64-encoded images by default, or custom S3/R2 upload in handler

## Video Generation on Serverless

- Requires custom nodes: VideoHelperSuite, LTXVideo, WanVideoWrapper
- VHS_VideoCombine node handles video output in ComfyUI
- Reference implementation: https://github.com/f00d4tehg0dz/runpod_comfyui_ltx2_flux
  - Targets RTX 5090 / Blackwell GPUs
  - Models download at container startup, skip if cached on volume
  - Uses CUDA 12.8 + PyTorch 2.8.0
  - Includes 30+ custom nodes for video workflows

## GPU Options

| GPU | VRAM | Use Case |
|-----|------|----------|
| RTX 4090 | 24GB | Wan 2.2 (turbo mode) |
| RTX 5090 | 32GB | LTX 2.3 + Wan 2.2 (auto-select) |
| A100 | 80GB | Both models, fastest |
| H100 | 80GB | Both models, fastest |

Minimum 24GB VRAM required for Wan 2.2.
32GB+ VRAM required for LTX 2.3.

## Docker Build Notes

- Cannot build x86 CUDA images on Apple Silicon (M1/M2/M3)
- Options for building:
  1. Rent a cheap RunPod CPU pod to build + push
  2. GitHub Actions CI/CD (ubuntu-latest runner)
  3. GCP Cloud Build
- Image size without models: ~8-10GB
- First pull cold start: ~2-3 min (mitigated by FlashBoot)

## Cost Analysis

### Current (Persistent RTX 5090 Pod)
- ~$0.69/hr x 24hr = ~$16.56/day
- Monthly: ~$497 (even when idle)

### Serverless (RTX 4090, Pay-Per-Use)
- GPU time per video: ~30-45 min = ~$0.35-0.52/video
- 1 video/day = ~$0.44/day average
- VPS for orchestration: ~$5/month
- Monthly (30 videos): ~$18

### Savings: ~96% reduction ($497 -> ~$18/month)

## API Reference

### Submit Job
```bash
curl -X POST "https://api.runpod.ai/v2/{ENDPOINT_ID}/run" \
  -H "Authorization: Bearer {RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input": {"script": {...}, "ELEVENLABS_API_KEY": "..."}}'
```

Response: `{"id": "job-abc123", "status": "IN_QUEUE"}`

### Check Status
```bash
curl "https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{JOB_ID}" \
  -H "Authorization: Bearer {RUNPOD_API_KEY}"
```

Response: `{"id": "job-abc123", "status": "COMPLETED", "output": {...}}`

### Status Values
- `IN_QUEUE` — waiting for worker
- `IN_PROGRESS` — worker processing
- `COMPLETED` — done, output available
- `FAILED` — error, check output.error
- `CANCELLED` — cancelled by user
- `TIMED_OUT` — exceeded execution timeout

## Sources

- [FlashBoot Blog Post](https://www.runpod.io/blog/introducing-flashboot-serverless-cold-start)
- [Endpoint Configuration Docs](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)
- [Network Volumes Docs](https://docs.runpod.io/storage/network-volumes)
- [ComfyUI Serverless Tutorial](https://docs.runpod.io/tutorials/serverless/comfyui)
- [Official worker-comfyui Repo](https://github.com/runpod-workers/worker-comfyui)
- [LTX2 + Flux RunPod Repo](https://github.com/f00d4tehg0dz/runpod_comfyui_ltx2_flux)
- [ComfyUI Serverless Deployment Guide](https://www.mikedegeofroy.com/blog/comfyui-serverless)
- [Deploy ComfyUI Blog](https://www.runpod.io/blog/deploy-comfyui-as-a-serverless-api-endpoint)
