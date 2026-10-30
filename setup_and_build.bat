@echo off
setlocal EnableExtensions
rem ============================================================
rem  setup_and_build.bat - fresh-PC one-shot for this project:
rem
rem    1. checks Python, installs PyInstaller
rem    2. downloads ffmpeg        -> ffmpeg\ffmpeg.exe
rem    3. downloads Real-ESRGAN   -> esrgan\ (exe + models)
rem    4. builds the exe          -> dist\VideoUpscalerRecovery\
rem
rem  SeedVR2 is deliberately NOT set up here: it is a runtime
rem  dependency (~24 GB). Run enable_seedvr2.bat in the SHIPPED
rem  folder (dist\VideoUpscalerRecovery\) on the machine that
rem  will actually use the app.
rem
rem  Re-runnable: existing ffmpeg\/esrgan\ folders are kept.
rem ============================================================
cd /d "%~dp0"
set "TMPDL=%~dp0_tmp_download"

echo.
echo === 1/4 Python ===
where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install Python 3.10+ from https://www.python.org and check
    echo "Add python.exe to PATH" during install, then run again.
    goto :fail
)
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pyinstaller
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo PyInstaller could not be installed. Check your network/pip.
    goto :fail
)
echo PyInstaller OK

echo.
echo === 2/4 ffmpeg ===
if exist "ffmpeg\ffmpeg.exe" (
    echo ffmpeg\ffmpeg.exe already present - skipping download
) else (
    if not exist ffmpeg mkdir ffmpeg
    echo Downloading latest ffmpeg release build ...
    curl.exe -L --fail -sS -o "%TMPDL_ff.zip" https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    if errorlevel 1 (
        echo ffmpeg download failed.
        goto :fail
    )
    if not exist "%TMPDL%" mkdir "%TMPDL%"
    tar -xf "%TMPDL_ff.zip" -C "%TMPDL%"
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
echo === 3/4 Real-ESRGAN (ncnn-vulkan) ===
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
    xcopy /e /y /q "%ESDIR%\*" "esrgan\" >nul
    if errorlevel 1 (
        echo Could not copy Real-ESRGAN into esrgan\
        goto :fail
    )
    echo Real-ESRGAN OK
)

echo.
echo === 4/4 Build ===
rem Build into a TEMP folder, then merge into dist\ - PyInstaller wipes
rem its target folder, which would destroy seedvr2\ + output\ if dist\
rem VideoUpscalerRecovery\ already contains them.
set "TMPDIST=%TMPDL%\dist"
python -m PyInstaller --noconfirm --windowed --onedir --name VideoUpscalerRecovery ^
  --distpath "%TMPDIST%" --workpath "%TMPDL%\work" ^
  --icon "assets/icons/icon_c.ico" ^
  --add-data "ffmpeg;ffmpeg" ^
  --add-data "esrgan;esrgan" ^
  --add-data "assets/icons/icon_c.ico;icons" ^
  stopdjvu_gui.py
if errorlevel 1 (
    echo Build failed.
    goto :fail
)
if not exist "%TMPDIST%\VideoUpscalerRecovery\VideoUpscalerRecovery.exe" (
    echo Build finished but the exe was not found.
    goto :fail
)
if not exist "dist\VideoUpscalerRecovery" mkdir "dist\VideoUpscalerRecovery"
copy /y "%TMPDIST%\VideoUpscalerRecovery\VideoUpscalerRecovery.exe" "dist\VideoUpscalerRecovery\" >nul
copy /y "README_user.txt" "dist\VideoUpscalerRecovery\" >nul
copy /y "setup.bat" "dist\VideoUpscalerRecovery\" >nul
copy /y "enable_seedvr2.bat" "dist\VideoUpscalerRecovery\" >nul
copy /y "seedvr2_patch.py" "dist\VideoUpscalerRecovery\" >nul
copy /y "download_seedvr2_models.py" "dist\VideoUpscalerRecovery\" >nul
rmdir /s /q "dist\VideoUpscalerRecovery\_internal" 2>nul
xcopy /e /y /q "%TMPDIST%\VideoUpscalerRecovery\_internal" "dist\VideoUpscalerRecovery\_internal\" >nul
if errorlevel 1 (
    echo Could not merge the build into dist\.
    goto :fail
)
echo Merged into dist\VideoUpscalerRecovery\ (existing folders preserved)

if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
if exist "%TMPDL_ff.zip%" del /q "%TMPDL_ff.zip%"

echo.
echo ============================================================
echo  DONE: dist\VideoUpscalerRecovery\VideoUpscalerRecovery.exe
echo
echo  Note: SeedVR2 was NOT set up. To enable the SeedVR2 models,
echo  run enable_seedvr2.bat inside dist\VideoUpscalerRecovery\
echo  (downloads ~24 GB of models - do this on the machine that
echo  will use the app).
echo ============================================================
pause
exit /b 0

:fail
echo.
echo *** SETUP FAILED - see the message above ***
if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
if exist "%TMPDL_ff.zip%" del /q "%TMPDL_ff.zip%"
pause
exit /b 1
