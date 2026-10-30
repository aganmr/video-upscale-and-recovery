#!/usr/bin/env python3
"""
stopdjvu_gui.py — Tkinter GUI for "Video Upscaler and Recovery".

Main function: AI video upscaling. Optional: recovery of STOP/Djvu
ransomware-encrypted .maas video files (toggle).

Pairs with recover_stopdjvu_video.py (the engine). Build the standalone
Windows app with build.bat (PyInstaller).
"""

import os
import queue
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Make the engine importable both from source and from the bundled app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recover_stopdjvu_video import (  # noqa: E402
    collect_files, find_ffmpeg, find_esrgan, get_output_dirs,
    ensure_output_dirs, run_batch, list_gpus,
    current_log_path, find_seedvr2, is_seedvr2, terminate_all,
)

APP_VERSION = "1.1.0"
ISSUES_URL = "https://github.com/aganmr/video-upscale-and-recovery/issues"


def dpi_aware():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class App:
    # Display label -> internal model key
    MODEL_KEYS = {
        "animevideov3 (fast, stable)": "animevideov3",
        "x4plus (max quality)": "x4plus",
        "seedvr2 3b (diffusion, high quality)": "seedvr2_3b",
        "seedvr2 3b GGUF (low VRAM)": "seedvr2_3b_gguf",
        "seedvr2 7b (max quality)": "seedvr2_7b",
        "seedvr2 7b GGUF Q4 (low VRAM)": "seedvr2_7b_gguf",
    }
    _ES_MODELS = ["animevideov3 (fast, stable)", "x4plus (max quality)"]
    _SV2_MODELS = [
        "seedvr2 3b (diffusion, high quality)",
        "seedvr2 3b GGUF (low VRAM)",
        "seedvr2 7b (max quality)",
        "seedvr2 7b GGUF Q4 (low VRAM)",
    ]

    def _model_values(self):
        """Model dropdown entries. SeedVR2 entries only appear when the
        seedvr2\\ folder is installed next to the program."""
        vals = list(self._ES_MODELS)
        if find_seedvr2():
            vals += self._SV2_MODELS
            self.seedvr2_hint.config(text="")
        else:
            self.seedvr2_hint.config(
                text="SeedVR2 not installed - run enable_seedvr2.bat next to "
                     "the app to enable the seedvr2 models")
        return vals

    def _model_key(self):
        return self.MODEL_KEYS.get(self.up_model.get(), "animevideov3")

    def _on_model_change(self, *_):
        """Swap the Scale dropdown for a Target-resolution dropdown when a
        SeedVR2 model is selected (it is resolution-based, not 2x/4x)."""
        key = self._model_key()
        st = "readonly" if self.do_upscale.get() else "disabled"
        if key.startswith("seedvr2"):
            self.scale_box.config(values=["720", "1080", "1440", "2160"])
            self.scale_box.set("1080")
            self.scale_lbl.config(text="Target:")
        else:
            self.scale_box.config(values=["2x", "4x"])
            self.scale_box.set("2x")
            self.scale_lbl.config(text="Scale:")
        self.scale_box.config(state=st)
        self._update_seedvr2_opts_state()

    def _update_seedvr2_opts_state(self):
        """The three SeedVR2 VRAM toggles only apply to the seedvr2
        models - disable them for the Real-ESRGAN models."""
        st = "normal" if self._model_key().startswith("seedvr2") else "disabled"
        for cb in (self.cb_low_tiling, self.cb_dit_offload,
                   self.cb_vae_offload):
            cb.config(state=st)

    def __init__(self, root):
        self.root = root
        root.title("Video Upscaler and Recovery  v" + APP_VERSION)
        root.resizable(False, False)
        self._set_window_icon()

        self.q = queue.Queue()
        self.running = False

        self.folder = tk.StringVar(value="")
        self.recursive = tk.BooleanVar(value=True)
        self.do_maas = tk.BooleanVar(value=False)  # .maas recovery: OFF
        self.do_upscale = tk.BooleanVar(value=True)  # upscaling: ON
        self.do_log = tk.BooleanVar(value=True)  # log file: ON
        # SeedVR2 VRAM options (no-op for ESRGAN models), all default ON
        self.low_tiling = tk.BooleanVar(value=True)
        self.dit_offload = tk.BooleanVar(value=True)
        self.vae_offload = tk.BooleanVar(value=True)
        self.up_model = tk.StringVar(value="animevideov3")
        self.up_scale = tk.StringVar(value="2")

        self._build_widgets()
        self._center(640, 444)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_queue)

    def _set_window_icon(self):
        """Set the window/taskbar icon (the bundled .ico). No-op when the
        icon file is not present (e.g. running the source without a build)."""
        for base in (getattr(sys, "_MEIPASS", None),
                     os.path.dirname(os.path.abspath(__file__))):
            if not base:
                continue
            p = os.path.join(base, "icons", "icon_c.ico")
            if os.path.exists(p):
                try:
                    self.root.iconbitmap(p)
                except tk.TclError:
                    pass
                return

    # ------------------------------------------------------------ layout
    def _build_widgets(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Source folder:").pack(side="left")
        self.folder_entry = ttk.Entry(top, textvariable=self.folder, width=52)
        self.folder_entry.pack(side="left", padx=6)
        ttk.Button(top, text="Browse...", command=self._browse).pack(side="left")

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        ttk.Checkbutton(opts, text="Scan subfolders",
                        variable=self.recursive,
                        command=self._update_start_state).pack(side="left")
        ttk.Checkbutton(opts, text="Recover STOP/Djvu (.maas)",
                        variable=self.do_maas,
                        command=self._update_start_state).pack(side="left",
                                                               padx=(24, 0))
        ttk.Checkbutton(opts, text="Write log file",
                        variable=self.do_log).pack(side="left", padx=(24, 0))

        self.seedvr2_hint = ttk.Label(self.root, text="",
                                      foreground="#888888")
        up = ttk.Frame(self.root)
        up.pack(fill="x", **pad)
        ttk.Checkbutton(up, text="Upscale (AI, needs NVIDIA GPU)",
                        variable=self.do_upscale,
                        command=self._toggle_upscale).pack(side="left")
        ttk.Label(up, text="Model:").pack(side="left", padx=(24, 4))
        self.model_box = ttk.Combobox(
            up, width=26, state="readonly", textvariable=self.up_model,
            values=self._model_values())
        self.model_box.pack(side="left")
        self.model_box.set(self._model_values()[0])
        self.scale_lbl = ttk.Label(up, text="Scale:")
        self.scale_lbl.pack(side="left", padx=(16, 4))
        self.scale_box = ttk.Combobox(
            up, width=6, state="readonly", textvariable=self.up_scale,
            values=["2x", "4x"])
        self.scale_box.pack(side="left")
        self.scale_box.set("2x")
        # Only take up a row when there is actually a message to show
        # (i.e. SeedVR2 not installed). An empty hint label would
        # otherwise add a full row of vertical space before the SeedVR2 row.
        if self.seedvr2_hint.cget("text"):
            self.seedvr2_hint.pack(fill="x", padx=12)
        self.up_model.trace_add("write", self._on_model_change)

        sv2 = ttk.Frame(self.root)
        sv2.pack(fill="x", **pad)
        ttk.Label(sv2, text="SeedVR2").pack(side="left")
        self.cb_low_tiling = ttk.Checkbutton(
            sv2, text="Low tiling (less VRAM, slower)",
            variable=self.low_tiling)
        self.cb_low_tiling.pack(side="left", padx=(12, 0))
        self.cb_dit_offload = ttk.Checkbutton(
            sv2, text="DiT offload to CPU", variable=self.dit_offload)
        self.cb_dit_offload.pack(side="left", padx=(14, 0))
        self.cb_vae_offload = ttk.Checkbutton(
            sv2, text="VAE offload to CPU", variable=self.vae_offload)
        self.cb_vae_offload.pack(side="left", padx=(14, 0))

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Start",
                                    command=self._start, state="disabled")
        self.start_btn.pack(side="left")
        ttk.Button(btns, text="Open Output Folder",
                   command=self._open_output).pack(side="right")
        ttk.Button(btns, text="Report an issue",
                   command=self._report_issue).pack(side="right",
                                                     padx=(0, 10))

        prog = ttk.Frame(self.root)
        prog.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(prog, maximum=1, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.count_lbl = ttk.Label(prog, text="0 / 0", width=8)
        self.count_lbl.pack(side="left", padx=(8, 0))

        status = ttk.LabelFrame(self.root, text="Status")
        status.pack(fill="both", expand=True, **pad)
        self.status1 = ttk.Label(status, anchor="w",
                                 text="Select a folder with videos to begin.")
        self.status1.pack(fill="x", padx=8, pady=(6, 2))
        self.status2 = ttk.Label(status, anchor="w", text="", foreground="#555555")
        self.status2.pack(fill="x", padx=8, pady=(0, 6))

        self._folder_trace()
        self._update_seedvr2_opts_state()

    def _center(self, w, h):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    # ---------------------------------------------------------- callbacks
    def _on_close(self):
        """Window close: kill any running external child processes
        (SeedVR2 venv python + its children, ffmpeg, Real-ESRGAN) before
        destroying the window. Without this, closing the app mid-upscale
        would leave the GPU processes running in the background."""
        try:
            terminate_all()
        finally:
            self.root.destroy()

    def _folder_trace(self):
        self.folder.trace_add("write", self._folder_changed)

    def _folder_changed(self, *_):
        self._update_start_state()

    def _scan_counts(self):
        """(n_mp4, n_maas) in the selected folder, honoring the recursive
        setting. .maas files are always counted (the breakdown display
        shows them even when the recovery toggle is OFF)."""
        f = self.folder.get().strip()
        if not f or not os.path.isdir(f):
            return 0, 0
        rec = self.recursive.get()
        n_maas = len(collect_files(f, rec, include_maas=True))
        n_mp4 = len(collect_files(f, rec, include_maas=False))
        return n_mp4, n_maas

    def _update_start_state(self):
        """Re-scan the selected folder: update the 0/N counter and the
        Start button. Start is enabled only when there are processable
        files AND at least one of Recovery / Upscale is ON. Warns with a
        modal when a real folder contains no videos at all."""
        if self.running:
            return
        f = self.folder.get().strip()
        if not f or not os.path.isdir(f):
            self.start_btn.config(state="disabled")
            self.count_lbl.config(text="0 / 0")
            self.status1.config(text="Select a folder with videos to begin.")
            self.status2.config(text="")
            return
        n_mp4, n_maas = self._scan_counts()
        n_proc = n_mp4 + (n_maas if self.do_maas.get() else 0)
        self.count_lbl.config(text="0 / %d" % n_proc)
        has_files = n_proc > 0
        anything_on = self.do_maas.get() or self.do_upscale.get()
        self.start_btn.config(state="normal"
                              if (has_files and anything_on) else "disabled")
        if n_mp4 + n_maas == 0:
            self.status1.config(text="No videos found in %s" % f)
            self.status2.config(text="")
            messagebox.showerror("No videos found",
                                 "No videos (.mp4) found in:\n" + f)
        elif has_files:
            self.status1.config(
                text="Found %d video(s) in %s%s"
                     % (n_proc, f,
                        " (%d .maas)" % n_maas
                        if (n_maas and self.do_maas.get()) else ""))
            if not anything_on:
                self.status2.config(
                    text="Nothing to process - enable Recovery or Upscale")
            else:
                self.status2.config(text="")
        else:
            self.status1.config(text="No processable files in %s" % f)
            self.status2.config(
                text="Nothing to process - enable "
                     + ("Recovery" if self.do_upscale.get()
                        else "Recovery or Upscale"))

    def _toggle_upscale(self):
        st = "readonly" if self.do_upscale.get() else "disabled"
        self.model_box.config(state=st)
        self.scale_box.config(state=st)
        self._update_start_state()

    def _upscale_opts(self):
        """(model_key, setting) from the combobox display strings.
        setting is the 2/4 factor for ESRGAN, or the target resolution
        (720/1080/...) for SeedVR2."""
        model = self._model_key()
        txt = self.up_scale.get().strip()
        try:
            setting = int(txt)
        except ValueError:
            setting = int(txt[0])
        return model, setting

    def _browse(self):
        d = filedialog.askdirectory(title="Select folder with videos",
                                    initialdir=self.folder.get()
                                    or os.path.expanduser("~"))
        if d:
            self.folder.set(d)

    def _open_output(self):
        f = self.folder.get().strip()
        if not f or not os.path.isdir(f):
            messagebox.showwarning("No folder", "Select a source folder first.")
            return
        rec, _up = ensure_output_dirs(f)
        subprocess.Popen(["explorer", os.path.dirname(rec)])

    def _report_issue(self):
        webbrowser.open(ISSUES_URL)

    def _start(self):
        if self.running:
            return
        f = self.folder.get().strip()
        files = collect_files(f, self.recursive.get(),
                              include_maas=self.do_maas.get())
        if not files:
            messagebox.showwarning("No files",
                                   "No processable videos found in:\n" + f)
            return
        have_ffmpeg = bool(find_ffmpeg())
        if not have_ffmpeg:
            if not messagebox.askyesno(
                    "ffmpeg not found",
                    "ffmpeg was not found, so H.264 conversion and AI "
                    "upscaling will be skipped.\n\n"
                    "Run setup.bat next to the app to install the video "
                    "tools, then restart.\n\n"
                    "Continue with a plain copy of the videos?"):
                return

        do_upscale = self.do_upscale.get()
        if do_upscale and not list_gpus():
            if not messagebox.askyesno(
                    "No NVIDIA GPU",
                    "No NVIDIA GPU was detected, so AI upscaling will be "
                    "skipped.\nRecovery and H.264 conversion will still run.\n\n"
                    "Continue without upscaling?"):
                return
            do_upscale = False
        up_model, up_scale = self._upscale_opts()
        if (do_upscale and have_ffmpeg
                and not is_seedvr2(up_model) and not find_esrgan()):
            if not messagebox.askyesno(
                    "Real-ESRGAN not found",
                    "The Real-ESRGAN upscaler was not found, so the "
                    "selected model cannot run.\n\n"
                    "Run setup.bat next to the app to install the video "
                    "tools, then restart.\n\n"
                    "Continue without upscaling?"):
                do_upscale = False

        self.running = True
        self.start_btn.config(state="disabled")
        self.folder_entry.config(state="disabled")
        self.progress.config(maximum=len(files), value=0)
        self.count_lbl.config(text="0 / %d" % len(files))
        self.status1.config(text="Starting...")
        self.status2.config(text="")

        t = threading.Thread(
            target=self._worker,
            args=(f, files, do_upscale, up_model, up_scale,
                  self.do_log.get(), self.low_tiling.get(),
                  self.dit_offload.get(), self.vae_offload.get()),
            daemon=True)
        t.start()

    # ------------------------------------------------------------- worker
    def _worker(self, folder, files, do_upscale=True,
                up_model="animevideov3", up_scale=2, do_log=True,
                low_tiling=True, dit_offload=True, vae_offload=True):
        def cb(i, total, name, phase, r):
            self.q.put(("progress", i, total, name, phase, r))
        try:
            results = run_batch(folder, files, cb,
                                do_upscale, up_model, up_scale, do_log=do_log,
                                low_tiling=low_tiling,
                                dit_offload=dit_offload,
                                vae_offload=vae_offload)
        except Exception as e:
            results = []
            self.q.put(("fatal", str(e)))
        self.q.put(("done", results))

    # -------------------------------------------------------------- queue
    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _k, i, total, name, phase, r = msg
                    self.progress.config(value=i)
                    self.count_lbl.config(text="%d / %d" % (i, total))
                    if phase == "done":
                        self.status1.config(
                            text="Finished (%d/%d): %s" % (i, total, name))
                        self._tally(results_so_far=[r])
                    elif phase.startswith("gpu:") or \
                            phase.startswith("upscaling disabled:"):
                        self.status1.config(text=phase)
                    elif phase.startswith("upscaling "):
                        self.status1.config(
                            text="Upscaling (%d/%d): %s  ->  %s"
                                 % (i, total, name, phase))
                    else:
                        self.status1.config(
                            text="Processing (%d/%d): %s  ->  %s"
                                 % (i, total, name, phase))
                elif kind == "fatal":
                    self.status1.config(text="ERROR: " + msg[1])
                    self._finish()
                elif kind == "done":
                    self._finish(msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _tally(self, results_so_far):
        ok = sum(1 for r in results_so_far if r["ok"])
        bad = len(results_so_far) - ok
        self.status2.config(text="Done so far: %d OK, %d failed" % (ok, bad))

    def _finish(self, results=None):
        self.running = False
        self.folder_entry.config(state="normal")
        if results is not None:
            ok = sum(1 for r in results if r["ok"])
            bad = len(results) - ok
            recs = sum(1 for r in results if r.get("rec_out"))
            ups = sum(1 for r in results if r.get("upscaled_out"))
            rec = os.path.dirname(get_output_dirs(self.folder.get().strip())[0])
            self.status1.config(
                text="DONE - %d OK, %d failed, %d recovered, %d upscaled"
                     % (ok, bad, recs, ups))
            lp = current_log_path()
            logline = ("Log file: " + lp) if lp else "Log file: off"
            self.status2.config(text="Output folder: " + rec + "\n" + logline)
            if bad:
                fails = [r["name"] + ": " + r["reason"] for r in results if not r["ok"]]
                for r in results:
                    if r.get("upscale_error"):
                        fails.append(r["name"] + ": upscale failed - "
                                     + r["upscale_error"])
                self.status2.config(text="Output: " + rec + "\nFailed:\n  "
                                          + "\n  ".join(fails))
        self._update_start_state()


def main():
    dpi_aware()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
