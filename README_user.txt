================================================================
 Video Upscaler and Recovery - User Guide
================================================================

What this is
------------
A standalone Windows app that AI-upscale videos, and can also
recover videos encrypted by the STOP/Djvu ransomware (.maas
files). Nothing to install - this folder IS the app.

  VideoUpscalerRecovery.exe   <- double-click this to start

----------------------------------------------------------------
Prerequisites
----------------------------------------------------------------
* Windows 10 or 11 (64-bit)
* An NVIDIA graphics card - required for upscaling, not needed
  for recovery. Bigger video memory (VRAM) = bigger models and
  resolutions you can use.
* Free disk space for the output (upscaled videos are larger
  than the originals).

----------------------------------------------------------------
Quick start
----------------------------------------------------------------
1. First time only: double-click setup.bat in THIS folder and
   wait. It downloads the two video tools the app needs
   (ffmpeg + Real-ESRGAN). You only do this once.
2. Double-click VideoUpscalerRecovery.exe
3. "Source folder" -> Browse... and pick the folder with your
   videos (.mp4, and .maas files if you enabled recovery).
   "Scan subfolders" includes subfolders.
4. Set the options (see below).
5. Click Start.
6. When it finishes, click "Open Output Folder" to see the
   results.

----------------------------------------------------------------
Options
----------------------------------------------------------------
Recover STOP/Djvu (.maas)     OFF by default.
    Turn ON to also pick up ransomware-encrypted .maas video
    files from the folder. They are rebuilt into playable
    H.264 videos. (Only the first ~1 second of each clip is
    lost - the ransomware destroys the file header, not the
    video data.)

Upscale (AI, needs NVIDIA GPU)  ON by default.
    AI-upscale every video. Pick a model:
      * animevideov3 / x4plus (Real-ESRGAN)
          Fast. Good for anime and general footage. 2x or 4x.
      * seedvr2 3b / 7b (diffusion)
          Slower, higher quality, resolution-based target
          (720p-2160p). REQUIRES the one-time setup below.
          7b wants a card with ~12 GB+ of VRAM; the GGUF
          variants fit smaller cards.

Write log file                ON by default.
    Appends a log to recovery_log.txt in this folder.

SeedVR2 VRAM options          All ON by default. They only affect
    the seedvr2 models (not the Real-ESRGAN ones) and trade
    speed for memory safety:
      Low tiling (less VRAM, slower)
          Smaller processing tiles - keeps big jobs like 1080p
          inside cards with ~16 GB of video memory.
      DiT offload to CPU
          Keeps part of the model in system RAM.
      VAE offload to CPU
          Same, for the image-reconstruction part of the model.
    Leave them ON. If your card has a lot of free video memory
    and you want maximum speed, you can turn them OFF.

The Start button stays disabled until there are files to
process AND at least one of Recovery / Upscale is enabled.

----------------------------------------------------------------
Where the results go
----------------------------------------------------------------
  output\
    <your folder name>\
      recovered\    H.264 versions / copies (only when
                    upscaling is OFF)
      upscaled\     AI-upscaled videos (when upscaling is ON)

Old videos with an outdated codec (MPEG-4) are converted to
H.264 automatically; modern videos are copied without
re-encoding. When upscaling is ON there is no recovered\
folder - only the upscaled results are kept.

You can re-run the app on the same folder at any time:
finished files are skipped automatically. Changing the model
or scale re-does only the upscaling.

----------------------------------------------------------------
Optional: enable the SeedVR2 models (one time)
----------------------------------------------------------------
The seedvr2 models are not included (they are ~24 GB). To
enable them:

  1. Make sure you are connected to the internet.
  2. Double-click enable_seedvr2.bat in THIS folder, press Y,
     and wait (it downloads Python + the models; can take a
     while).
  3. Restart the app - the seedvr2 entries now appear in the
     Model list.

Everything works without this step; you just get the two
Real-ESRGAN models.

----------------------------------------------------------------
The other files in this folder
----------------------------------------------------------------
  output\                  your results (safe to delete; the
                           app recreates it)
  setup.bat                one-time video-tools setup (run once)
  seedvr2\                 created by enable_seedvr2.bat
  enable_seedvr2.bat       the optional SeedVR2 setup (keep it)
  download_seedvr2_models.py  used by the setup (keep it)
  recovery_log.txt         the log file (created by the app)
  README_user.txt          this file

----------------------------------------------------------------
Notes
----------------------------------------------------------------
* Closing the app mid-run stops everything cleanly and frees
  the GPU - no hidden background processes.
* Upscaling is slow (minutes per video depending on model and
  length). Leave the app open until it finishes.
* If "No NVIDIA GPU" appears, upscaling is skipped but
  recovery/conversion still work.
