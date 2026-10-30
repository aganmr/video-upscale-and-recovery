@echo off
rem Build the "Video Upscaler and Recovery" standalone Windows app.
rem Requires: pip install pyinstaller  (or run setup_and_build.bat)
rem Output:   dist\VideoUpscalerRecovery\VideoUpscalerRecovery.exe
rem
rem Builds into a TEMP folder first, then merges into dist\ -
rem PyInstaller wipes its target folder, which would destroy
rem seedvr2\ + output\ if they already live in dist\.
setlocal EnableExtensions
cd /d "%~dp0"
set "TMPDL=%~dp0_tmp_build"
set "TMPDIST=%TMPDL%\dist"

python -m PyInstaller --noconfirm --windowed --onedir --name VideoUpscalerRecovery ^
  --distpath "%TMPDIST%" --workpath "%TMPDL%\work" ^
  --add-data "ffmpeg;ffmpeg" ^
  --add-data "esrgan;esrgan" ^
  stopdjvu_gui.py
if errorlevel 1 goto :fail
if not exist "%TMPDIST%\VideoUpscalerRecovery\VideoUpscalerRecovery.exe" goto :fail

if not exist "dist\VideoUpscalerRecovery" mkdir "dist\VideoUpscalerRecovery"
copy /y "%TMPDIST%\VideoUpscalerRecovery\VideoUpscalerRecovery.exe" "dist\VideoUpscalerRecovery\" >nul
copy /y "README_user.txt" "dist\VideoUpscalerRecovery\" >nul
copy /y "setup.bat" "dist\VideoUpscalerRecovery\" >nul
copy /y "enable_seedvr2.bat" "dist\VideoUpscalerRecovery\" >nul
copy /y "seedvr2_patch.py" "dist\VideoUpscalerRecovery\" >nul
copy /y "download_seedvr2_models.py" "dist\VideoUpscalerRecovery\" >nul
rmdir /s /q "dist\VideoUpscalerRecovery\_internal" 2>nul
xcopy /e /y /q "%TMPDIST%\VideoUpscalerRecovery\_internal" "dist\VideoUpscalerRecovery\_internal\" >nul
if errorlevel 1 goto :fail

if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
echo.
echo Build complete: dist\VideoUpscalerRecovery\VideoUpscalerRecovery.exe
pause
exit /b 0

:fail
echo.
echo *** BUILD FAILED ***
if exist "%TMPDL%" rmdir /s /q "%TMPDL%"
pause
exit /b 1
