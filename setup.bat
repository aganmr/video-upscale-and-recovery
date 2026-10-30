@echo off
setlocal EnableExtensions
rem ============================================================
rem  setup.bat - one-time video tools setup (run NEXT TO THE EXE)
rem
rem  Downloads the two external video tools the app needs for
rem  upscaling, into this folder:
rem
rem    ffmpeg\ffmpeg.exe                    video decode / encode
rem    esrgan\realesrgan-ncnn-vulkan.exe    Real-ESRGAN 2x/4x
rem
rem  Run this ONCE after extracting the release zip. It is
rem  idempotent: existing ffmpeg\/esrgan\ folders are kept.
rem
rem  Optional: for the high-quality SeedVR2 models, run
rem  enable_seedvr2.bat (downloads ~25 GB).
rem ============================================================
cd /d "%~dp0"
set "TMPDL=%~dp0_tmp_download"
set "FFZIP=%TMPDL%_ff.zip"

echo.
echo === 1/2 ffmpeg ===
if exist "ffmpeg\ffmpeg.exe" (
    echo ffmpeg\ffmpeg.exe already present - skipping download
) else (
    if not exist ffmpeg mkdir ffmpeg
    echo Downloading latest ffmpeg release build ...
    curl.exe -L --fail -sS -o "%FFZIP%" https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    if errorlevel 1 (
        echo ffmpeg download failed.
        goto :fail
    )
    if not exist "%TMPDL%" mkdir "%TMPDL%"
    tar -xf "%FFZIP%" -C "%TMPDL%"
    if errorlevel 1 (
        echo ffmpeg zip extraction failed.
        goto :fail
    )
    set "FFDIR="
    for /d %%D in ("%TMPDL%\ffmpeg-*") do set "FFDIR=%%D"
    if not defined FFDIR (
        echo Could not find the extracted ffmpeg folder in %TMPDL%
        goto :fail
    )
    copy /y "%FFDIR%\bin\ffmpeg.exe" "ffmpeg\ffmpeg.exe" >nul
    if errorlevel 1 (
        echo Could not copy ffmpeg.exe into ffmpeg\
        goto :fail
    )
    echo ffmpeg OK
)

echo.
echo === 2/2 Real-ESRGAN (ncnn-vulkan) ===
if exist "esrgan\realesrgan-ncnn-vulkan.exe" (
    echo esrgan\ already present - skipping download
) else (
    if not exist "%TMPDL%" mkdir "%TMPDL%"
    echo Resolving latest Real-ESRGAN release ...
    set "ESURL="
    for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "$r=Invoke-RestMethod 'https://api.github.com/repos/x40ai/Real-ESRGAN-ncnn-vulkan/releases/latest'; ($r.assets | Where-Object { $_.name -like '*x86_64*.zip' } | Select-Object -First 1).browser_download_url"`) do set "ESURL=%%U"
    if not defined ESURL (
        echo Could not resolve the Real-ESRGAN download URL.
        goto :fail
    )
    echo Downloading %ESURL%
    curl.exe -L --fail -sS -o "%TMPDL%\esrgan.zip" "%ESURL%"
    if errorlevel 1 (
        echo Real-ESRGAN download failed.
        goto :fail
    )
    tar -xf "%TMPDL%\esrgan.zip" -C "%TMPDL%"
    if errorlevel 1 (
        echo Real-ESRGAN zip extraction failed.
        goto :fail
    )
    set "ESDIR="
    for /d %%D in ("%TMPDL%\realesrgan-ncnn-vulkan*") do set "ESDIR=%%D"
    if not defined ESDIR (
        echo Could not find the extracted Real-ESRGAN folder in %TMPDL%
        goto :fail
    )
    if not exist esrgan mkdir esrgan
    xcopy /e /y /q "%ESDIR%\*" "esrgan\" >nul
    if errorlevel 1 (
        echo Could not copy Real-ESRGAN into esrgan\
        goto :fail
    )
    echo Real-ESRGAN OK
)

if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
if exist "%FFZIP%" del /q "%FFZIP%"

echo.
echo ============================================================
echo  DONE. ffmpeg\ and esrgan\ are installed next to the app.
echo
echo  You can now run VideoUpscalerRecovery.exe.
echo
echo  Optional: to enable the high-quality SeedVR2 models,
echo  run enable_seedvr2.bat (downloads ~25 GB).
echo ============================================================
pause
exit /b 0

:fail
echo.
echo *** SETUP FAILED - see the message above ***
if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
if exist "%FFZIP%" del /q "%FFZIP%"
pause
exit /b 1
