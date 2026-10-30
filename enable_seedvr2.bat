@echo off
setlocal EnableExtensions
rem ============================================================
rem  enable_seedvr2.bat - OPTIONAL: enable the SeedVR2 models
rem  Creates .\seedvr2 with a Python venv, the SeedVR2 CLI and
rem  pre-downloaded models (~25 GB), and applies the VRAM telemetry
rem  patch (seedvr2_patch.py). Needs internet + NVIDIA GPU.
rem
rem  Run AFTER setup.bat. Only needed for the high-quality
rem  seedvr2 models in the Upscale dropdown.
rem ============================================================
cd /d "%~dp0"
set "DIR=seedvr2"

echo.
echo Enabling SeedVR2 (downloads ~25 GB, one-time).
echo This creates .\seedvr2 with a Python venv, the CLI and models.
set "CONT="
set /p "CONT=Press Y to continue, any other key to cancel: "
if /i not "%CONT%"=="Y" goto :cancel
echo.

where uv >nul 2>nul
if not errorlevel 1 goto :have_uv
echo [1/7] Installing uv Python package manager...
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto :fail
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 goto :fail
goto :uv_done
:have_uv
echo [1/7] uv already installed
:uv_done

if exist "%DIR%\inference_cli.py" goto :clone_done
echo [2/7] Cloning SeedVR2 CLI...
git clone https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git "%DIR%"
if errorlevel 1 goto :fail
goto :clone_done
:clone_done
echo [2/7] SeedVR2 clone present

if exist "%DIR%\.venv\Scripts\python.exe" goto :venv_done
echo [3/7] Creating Python 3.12 virtual environment...
uv venv "%DIR%\.venv" --python 3.12
if errorlevel 1 goto :fail
goto :venv_done
:venv_done
echo [3/7] Virtual environment present

echo [4/7] Installing PyTorch CUDA + dependencies, several GB...
uv pip install --python "%DIR%\.venv\Scripts\python.exe" torch torchvision --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto :fail
rem NOTE: no sageattention install - it has no working Windows build
rem (pure-Python wheel, requires Linux-only triton). The CLI's "sdpa"
rem attention mode already uses PyTorch's native flash-attention kernels.
uv pip install --python "%DIR%\.venv\Scripts\python.exe" -r "%DIR%\requirements.txt"
if errorlevel 1 goto :fail

echo [5/7] Pre-downloading models, ~20 GB...
"%DIR%\.venv\Scripts\python.exe" "%~dp0download_seedvr2_models.py" "%DIR%\models\SEEDVR2"
if errorlevel 1 goto :fail

echo [6/7] Applying VRAM telemetry patch...
"%DIR%\.venv\Scripts\python.exe" "%~dp0seedvr2_patch.py" "%DIR%"
if errorlevel 1 goto :fail

echo [7/7] Verifying installation...
"%DIR%\.venv\Scripts\python.exe" -c "import torch; print('torch', torch.__version__, '- CUDA available:', torch.cuda.is_available())"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  SeedVR2 is ready. The app will now show the seedvr2
echo  models in the Upscale dropdown.
echo ============================================================
exit /b 0

:cancel
echo.
echo Cancelled - nothing was done.
exit /b 0

:fail
echo.
echo SETUP FAILED - see the messages above.
exit /b 1
