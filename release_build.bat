@echo off
setlocal EnableExtensions
rem ============================================================
rem  release_build.bat - build the SLIM release for GitHub
rem
rem  Builds the exe WITHOUT the ffmpeg\/esrgan\ add-data (so the
rem  core is ~26 MB and the zip stays well under GitHub's 100 MB
rem  asset cap), then assembles a clean release folder and zips
rem  it.
rem
rem  ffmpeg\ + esrgan\ are NOT bundled: the user installs them by
rem  running setup.bat after extracting the zip.
rem
rem  Requires: pyinstaller  (pip install pyinstaller)
rem  Output:   release\VideoUpscalerRecovery-v<VERSION>-windows.zip
rem ============================================================
cd /d "%~dp0"

set "VERSION=1.1.0"
set "APP=VideoUpscalerRecovery"
set "STAGE=release\%APP%-v%VERSION%-windows"
set "ZIP=release\%APP%-v%VERSION%-windows.zip"
set "TMPDL=%~dp0_tmp_release"
set "TMPDIST=%TMPDL%\dist"

echo.
echo === 1/3 Slim build (no ffmpeg\/esrgan\ bundled) ===
python -m PyInstaller --noconfirm --windowed --onedir --name %APP% ^
  --distpath "%TMPDIST%" --workpath "%TMPDL%\work" ^
  --specpath "%TMPDL%" ^
  --icon "%~dp0assets\icons\icon_c.ico" ^
  --add-data "%~dp0assets\icons\icon_c.ico;icons" ^
  stopdjvu_gui.py
if errorlevel 1 goto :fail
if not exist "%TMPDIST%\%APP%\%APP%.exe" goto :fail
if not exist "%TMPDIST%\%APP%\_internal" goto :fail

echo.
echo === 2/3 Assembling %STAGE% ===
if exist "release" rmdir /s /q "release"
if not exist "release" mkdir "release"
if not exist "%STAGE%" mkdir "%STAGE%"

copy /y "%TMPDIST%\%APP%\%APP%.exe" "%STAGE%\" >nul
xcopy /e /y /q "%TMPDIST%\%APP%\_internal" "%STAGE%\_internal\" >nul
if errorlevel 1 goto :fail

copy /y "README_user.txt" "%STAGE%\" >nul
copy /y "setup.bat" "%STAGE%\" >nul
copy /y "enable_seedvr2.bat" "%STAGE%\" >nul
copy /y "seedvr2_patch.py" "%STAGE%\" >nul
copy /y "download_seedvr2_models.py" "%STAGE%\" >nul

echo.
echo === 3/3 Zipping ===
if exist "%ZIP%" del /q "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -LiteralPath '%STAGE%' -DestinationPath '%ZIP%' -CompressionLevel Optimal"
if errorlevel 1 goto :fail
if not exist "%ZIP%" goto :fail

if exist "%TMPDL%" rmdir /s /q "%TMPDL%"

echo.
echo ============================================================
echo  DONE: %ZIP%
for %%F in ("%ZIP%") do echo  size: %%~zF bytes
echo ============================================================
exit /b 0

:fail
echo.
echo *** RELEASE BUILD FAILED - see the message above ***
if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
pause
exit /b 1
