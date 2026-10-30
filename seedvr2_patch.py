#!/usr/bin/env python3
"""
Patches the third-party SeedVR2 CLI so its VRAM telemetry reports the GPU
that actually does the work (--cuda_device) instead of always cuda:0.

Why this is needed:
    The CLI's pre-parser tries to remap the selected GPU to cuda:0 by
    setting CUDA_VISIBLE_DEVICES. But it first validates the device ID
    with a torch call (cuda.is_available / device_count) - and the CUDA
    driver reads that environment variable on its very first call, so
    the later assignment is a no-op. On multi-GPU systems the model then
    runs on cuda:N while every [VRAM] log line, the startup banner and
    the peak-memory summary read the (idle) cuda:0.

What the patch changes (telemetry ONLY):
    - Debug.__init__ gains a `device` parameter (CUDA index)
    - _collect_memory_metrics / peak summary query that device
    - the startup banner reports that device's name + size
    - peak-memory stat resets target that device
    - the CLI points the shared Debug instance at --cuda_device
    - the import-time "Initial CUDA memory" print follows the
      CUDA_VISIBLE_DEVICES the pre-parse exports for --cuda_device

    Model placement, performance and all processing behavior are
    untouched.

Usage:
    python seedvr2_patch.py [path-to-seedvr2-folder]
    (default: the seedvr2\\ folder next to this script)

Idempotent: re-running on an already-patched folder is a no-op.
Re-run after any SeedVR2 update (setup_seedvr2.bat does this for you).
"""
import py_compile
import sys
from pathlib import Path

# Each edit: (old_text, new_text, replace_all)
DEBUG_PY_EDITS = [
    (
        """    def __init__(self, enabled: bool = False, show_timestamps: bool = True):
        self.enabled = enabled
        self.show_timestamps = show_timestamps
""",
        """    def __init__(self, enabled: bool = False, show_timestamps: bool = True, device: Optional[int] = None):
        self.enabled = enabled
        self.show_timestamps = show_timestamps
        self.device_index = device  # CUDA index for VRAM telemetry (None = cuda:0)
""",
        False,
    ),
    (
        """    @torch._dynamo.disable  # Skip tracing to avoid datetime.now() warnings
    def log(self, message: str, level: str = "INFO", category: str = "general", force: bool = False, indent_level: int = 0) -> None:
""",
        """    def _telemetry_device(self) -> Optional[str]:
        \"\"\"CUDA device string for VRAM telemetry (None = default cuda:0).\"\"\"
        return f"cuda:{self.device_index}" if self.device_index is not None else None

    @torch._dynamo.disable  # Skip tracing to avoid datetime.now() warnings
    def log(self, message: str, level: str = "INFO", category: str = "general", force: bool = False, indent_level: int = 0) -> None:
""",
        False,
    ),
    (
        "                props = torch.cuda.get_device_properties(0)\n",
        "                props = torch.cuda.get_device_properties(self.device_index if self.device_index is not None else 0)\n",
        False,
    ),
    (
        "            metrics['vram_allocated'], metrics['vram_reserved'], metrics['vram_peak_alloc'], metrics['vram_peak_rsv'] = get_vram_usage(device=None, debug=self)\n",
        "            metrics['vram_allocated'], metrics['vram_reserved'], metrics['vram_peak_alloc'], metrics['vram_peak_rsv'] = get_vram_usage(device=self._telemetry_device(), debug=self)\n",
        False,
    ),
    (
        "            vram_info = get_basic_vram_info(device=None)\n",
        "            vram_info = get_basic_vram_info(device=self._telemetry_device())\n",
        True,  # occurs in _collect_memory_metrics AND log_peak_memory_summary
    ),
    (
        "        reset_vram_peak(device=None, debug=self)\n",
        "        reset_vram_peak(device=self._telemetry_device(), debug=self)\n",
        False,
    ),
]

CLI_EDITS = [
    (
        """    # Update debug instance with --debug flag
    debug.enabled = args.debug
""",
        """    # Update debug instance with --debug flag
    debug.enabled = args.debug

    # Point VRAM telemetry at the GPU that actually does the work.
    # (The pre-parse CUDA_VISIBLE_DEVICES remap is a no-op: the CUDA
    # driver is initialized during device-ID validation, before the env
    # var is set, so on multi-GPU systems cuda:0 is NOT the selected GPU.)
    if args.cuda_device:
        _first_dev = str(args.cuda_device).split(',')[0].strip()
        if _first_dev.isdigit():
            debug.device_index = int(_first_dev)
""",
        False,
    ),
]

MEMORY_MANAGER_EDITS = [
    (
        """import torch
import gc
import sys
import time
import psutil
import platform
""",
        """import os
import torch
import gc
import sys
import time
import psutil
import platform
""",
        False,
    ),
    (
        """# Initial VRAM check at module load
vram_info = get_basic_vram_info(device=None)
""",
        """# Initial VRAM check at module load
# (patched) report the GPU selected via --cuda_device when the CLI's
# pre-parse exported it as CUDA_VISIBLE_DEVICES (only valid while the
# driver still sees all GPUs - fall back to cuda:0 otherwise)
_initial_dev = None
_cv = os.environ.get("CUDA_VISIBLE_DEVICES")
if _cv and "," not in _cv.strip() and _cv.strip().isdigit():
    try:
        if int(_cv.strip()) < torch.cuda.device_count():
            _initial_dev = "cuda:" + _cv.strip()
    except Exception:
        pass
vram_info = get_basic_vram_info(device=_initial_dev)
""",
        False,
    ),
]


def apply_edits(path: Path, edits, name: str) -> int:
    text = path.read_text(encoding="utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    changed = 0
    for old, new, replace_all in edits:
        old_s = old.replace("\n", eol)
        new_s = new.replace("\n", eol)
        # Check "new" first: for insert-style edits the anchor text
        # (old) survives inside new, so "old present" is NOT proof the
        # edit is missing.
        if new_s in text:
            continue  # this edit is already applied
        if old_s not in text:
            print(f"  ERROR: anchor not found in {name} (did upstream change?):")
            print(f"    {old.splitlines()[0]}")
            sys.exit(1)
        count = text.count(old_s)
        if count > 1 and not replace_all:
            print(f"  ERROR: anchor is not unique in {name} ({count} occurrences):")
            print(f"    {old.splitlines()[0]}")
            sys.exit(1)
        text = text.replace(old_s, new_s) if replace_all else text.replace(old_s, new_s, 1)
        changed += 1
        print(f"  patched: {old.splitlines()[0].strip()[:72]}")
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    else:
        root = Path(__file__).resolve().parent / "seedvr2"
    debug_py = root / "src" / "utils" / "debug.py"
    mem_py = root / "src" / "optimization" / "memory_manager.py"
    cli = root / "inference_cli.py"
    for p in (debug_py, mem_py, cli):
        if not p.exists():
            print(f"ERROR: {p} not found - is this a SeedVR2 folder?")
            sys.exit(1)

    total = (
        apply_edits(debug_py, DEBUG_PY_EDITS, "src/utils/debug.py")
        + apply_edits(mem_py, MEMORY_MANAGER_EDITS, "src/optimization/memory_manager.py")
        + apply_edits(cli, CLI_EDITS, "inference_cli.py")
    )

    if total == 0:
        print("Already patched - nothing to do.")
        return

    for p in (debug_py, mem_py, cli):
        py_compile.compile(str(p), doraise=True)

    print("Done: [VRAM] lines, banner and peak summary now follow --cuda_device.")


if __name__ == "__main__":
    main()
