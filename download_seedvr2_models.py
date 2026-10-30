#!/usr/bin/env python3
"""Pre-download the SeedVR2 models used by the app.

Usage: python download_seedvr2_models.py <destination folder>
(Invoked by enable_seedvr2.bat with the venv's Python.)
"""

import os
import sys

MODELS = [
    # (huggingface repo, file name)
    ("numz/SeedVR2_comfyUI", "seedvr2_ema_3b_fp8_e4m3fn.safetensors"),
    ("AInVFX/SeedVR2_comfyUI", "seedvr2_ema_3b-Q8_0.gguf"),
    ("AInVFX/SeedVR2_comfyUI", "seedvr2_ema_7b_fp8_e4m3fn_mixed_block35_fp16.safetensors"),
    ("AInVFX/SeedVR2_comfyUI", "seedvr2_ema_7b-Q4_K_M.gguf"),
    ("numz/SeedVR2_comfyUI", "ema_vae_fp16.safetensors"),
]


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "models"
    os.makedirs(dest, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub is not installed in this Python - run it "
              "with the seedvr2 venv (enable_seedvr2.bat does this).")
        return 1
    for repo, name in MODELS:
        target = os.path.join(dest, name)
        if os.path.exists(target) and os.path.getsize(target) > 1000:
            print("skip (already there): %s" % name)
            continue
        print("downloading %s from %s ..." % (name, repo))
        try:
            hf_hub_download(repo_id=repo, filename=name, local_dir=dest)
        except Exception as e:
            print("FAILED: %s (%s)" % (name, e))
            return 1
    print("All models ready in %s" % os.path.abspath(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
