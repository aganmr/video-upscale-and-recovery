# Changelog

All notable changes to **Video Upscaler and Recovery** are documented
here. The format follows [Keep a Changelog](https://keepachangelog.com/)
and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-30

First release.

### Added
- Standalone Windows app (`VideoUpscalerRecovery.exe` + `_internal\`)
  for **AI video upscaling**, with optional recovery of
  STOP/Djvu ransomware-encrypted (`.maas`) video files.
- Upscaling engines:
  - **Real-ESRGAN** (ncnn-vulkan) — fast 2x / 4x
  - **SeedVR2** — 3B / 7B plus GGUF quantized variants for low-VRAM
    cards (opt-in, via `enable_seedvr2.bat`)
- Recovery: rebuilds playable MP4s from STOP/Djvu-encrypted video
  files (post-2019 variant) without the attacker's key.
- Three **SeedVR2 VRAM safety** toggles (low tiling, DiT offload to
  CPU, VAE offload to CPU) — all on by default, enabled only for the
  SeedVR2 models.
- Resumable batch runs (per-file MD5 manifest) and a `recovery_log.txt`.
- **`setup.bat`** — one-time, idempotent download of `ffmpeg\` +
  `esrgan\` next to the exe (slim release ships without them).
- **`enable_seedvr2.bat`** — optional, one-time SeedVR2 install
  (Python venv + CLI + ~25 GB of models), with a Y-to-continue gate.
- `seedvr2_patch.py` — patches the third-party SeedVR2 CLI so VRAM
  telemetry follows `--cuda_device` on multi-GPU systems.
