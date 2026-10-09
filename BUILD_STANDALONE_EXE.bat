@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Build Eshra7ly Downloader - Standalone EXE
cd /d "%~dp0"

set "ROOT=%CD%"
set "APP_DIR=%ROOT%\course_archiver"
set "BUILD_TOOLS=%ROOT%\build-tools"
set "VENV_DIR=%ROOT%\.build_venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "FAIL_REASON=Build stopped for an unknown reason."

echo.
echo ============================================================
echo   Eshra7ly Downloader - Standalone EXE Builder
echo ============================================================
echo.

if not exist "%APP_DIR%\gui.py" (
  set "FAIL_REASON=course_archiver\gui.py was not found. Put this BAT file in the repository root."
  goto :fail
)
if not exist "%ROOT%\Eshra7lyDownloader.spec" (
  set "FAIL_REASON=Eshra7lyDownloader.spec was not found. Use the updated project source."
  goto :fail
)
if not exist "%ROOT%\THIRD_PARTY_NOTICES.txt" (
  set "FAIL_REASON=THIRD_PARTY_NOTICES.txt was not found. Use the updated project source."
  goto :fail
)

rem Python is required only on the build PC, not on PCs running the finished EXE.
set "PY_CMD=py -3.11"
%PY_CMD% --version >nul 2>&1
if errorlevel 1 set "PY_CMD=python"
%PY_CMD% --version >nul 2>&1
if errorlevel 1 (
  set "FAIL_REASON=Python 3.9 or newer is required on the build PC. Install Python, then run this BAT again."
  goto :fail
)
%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 (
  set "FAIL_REASON=Your selected Python is too old. Python 3.9 or newer is required."
  goto :fail
)

if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if errorlevel 1 rmdir /s /q "%VENV_DIR%"
)
if not exist "%VENV_PY%" (
  echo [1/7] Creating an isolated build environment...
  %PY_CMD% -m venv "%VENV_DIR%"
  if errorlevel 1 (
    set "FAIL_REASON=Could not create the Python build environment."
    goto :fail
  )
)

echo [2/7] Installing the project and packaging dependencies...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
  set "FAIL_REASON=Could not update pip. Check your internet connection."
  goto :fail
)
"%VENV_PY%" -m pip install -r "%APP_DIR%\requirements.txt" pyinstaller
if errorlevel 1 (
  set "FAIL_REASON=Dependency installation failed. Check the network and requirements."
  goto :fail
)

echo [3/7] Preparing the bundled Chromium browser...
rem Remove old browser revisions so the EXE doesn't bundle several copies of Chromium.
if exist "%APP_DIR%\browser" rmdir /s /q "%APP_DIR%\browser"
mkdir "%APP_DIR%\browser" 2>nul
set "PLAYWRIGHT_BROWSERS_PATH=%APP_DIR%\browser"
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
  set "FAIL_REASON=Playwright could not download Chromium. Check your internet connection and try again."
  goto :fail
)

echo [4/7] Preparing standalone FFmpeg and FFprobe...
if not exist "%BUILD_TOOLS%" mkdir "%BUILD_TOOLS%"
set "NEED_FFMPEG=1"
if exist "%BUILD_TOOLS%\ffmpeg.exe" if exist "%BUILD_TOOLS%\ffprobe.exe" if exist "%BUILD_TOOLS%\FFmpeg-LICENSE.txt" (
  "%BUILD_TOOLS%\ffmpeg.exe" -hide_banner -version >nul 2>&1
  if not errorlevel 1 (
    "%BUILD_TOOLS%\ffprobe.exe" -hide_banner -version >nul 2>&1
    if not errorlevel 1 set "NEED_FFMPEG=0"
  )
)
if "%NEED_FFMPEG%"=="0" goto :ffmpeg_ready

set "FFMPEG_WORK=%TEMP%\Eshra7ly_FFmpeg_%RANDOM%_%RANDOM%"
set "FFMPEG_ZIP=%FFMPEG_WORK%.zip"
mkdir "%FFMPEG_WORK%" 2>nul

rem GitHub-hosted shared build is smaller than the full static archive.
rem Abort a stalled mirror if throughput stays below 50 KB/s for 30 seconds.
echo Trying GitHub-hosted FFmpeg build (about 85 MiB)...
curl.exe -fL --retry 2 --retry-delay 2 --connect-timeout 20 --speed-limit 50000 --speed-time 30 "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip" -o "%FFMPEG_ZIP%"
if errorlevel 1 (
  echo GitHub download failed or was too slow. Trying the alternate FFmpeg mirror...
  del /q "%FFMPEG_ZIP%" 2>nul
  curl.exe -fL --retry 1 --retry-delay 2 --connect-timeout 20 --speed-limit 50000 --speed-time 30 "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -o "%FFMPEG_ZIP%"
)
if errorlevel 1 (
  set "FAIL_REASON=Could not download FFmpeg from either mirror. Check the connection and retry."
  goto :fail
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%FFMPEG_ZIP%' -DestinationPath '%FFMPEG_WORK%' -Force"
if errorlevel 1 (
  set "FAIL_REASON=Could not extract the FFmpeg archive."
  goto :fail
)

set "FFMPEG_FILE="
set "FFPROBE_FILE="
for /r "%FFMPEG_WORK%" %%F in (ffmpeg.exe) do if not defined FFMPEG_FILE set "FFMPEG_FILE=%%F"
for /r "%FFMPEG_WORK%" %%F in (ffprobe.exe) do if not defined FFPROBE_FILE set "FFPROBE_FILE=%%F"
if not defined FFMPEG_FILE (
  set "FAIL_REASON=The downloaded FFmpeg archive did not contain ffmpeg.exe."
  goto :fail
)
if not defined FFPROBE_FILE (
  set "FAIL_REASON=The downloaded FFmpeg archive did not contain ffprobe.exe."
  goto :fail
)
for %%F in ("%FFMPEG_FILE%") do set "FFMPEG_BIN=%%~dpF"
for %%F in ("%FFMPEG_BIN%..") do set "FFMPEG_ROOT=%%~fF"
if not exist "%FFMPEG_ROOT%\LICENSE.txt" (
  set "FAIL_REASON=The FFmpeg package did not include LICENSE.txt; refusing to package without its license notice."
  goto :fail
)

rem Clear stale binaries and DLLs when staging a new FFmpeg package.
del /q "%BUILD_TOOLS%\ffmpeg.exe" "%BUILD_TOOLS%\ffprobe.exe" "%BUILD_TOOLS%\FFmpeg-LICENSE.txt" 2>nul
for %%F in ("%BUILD_TOOLS%\*.dll") do if exist "%%~fF" del /q "%%~fF"
copy /y "%FFMPEG_FILE%" "%BUILD_TOOLS%\ffmpeg.exe" >nul
if errorlevel 1 (
  set "FAIL_REASON=Could not stage ffmpeg.exe."
  goto :fail
)
copy /y "%FFPROBE_FILE%" "%BUILD_TOOLS%\ffprobe.exe" >nul
if errorlevel 1 (
  set "FAIL_REASON=Could not stage ffprobe.exe."
  goto :fail
)
copy /y "%FFMPEG_ROOT%\LICENSE.txt" "%BUILD_TOOLS%\FFmpeg-LICENSE.txt" >nul
if errorlevel 1 (
  set "FAIL_REASON=Could not copy the FFmpeg license notice."
  goto :fail
)
rem Bundle any runtime DLLs supplied beside the FFmpeg executables.
for %%F in ("%FFMPEG_BIN%*.dll") do if exist "%%~fF" copy /y "%%~fF" "%BUILD_TOOLS%\" >nul
"%BUILD_TOOLS%\ffmpeg.exe" -hide_banner -version >nul 2>&1
if errorlevel 1 (
  set "FAIL_REASON=The staged FFmpeg executable could not start."
  goto :fail
)
"%BUILD_TOOLS%\ffprobe.exe" -hide_banner -version >nul 2>&1
if errorlevel 1 (
  set "FAIL_REASON=The staged FFprobe executable could not start."
  goto :fail
)
rmdir /s /q "%FFMPEG_WORK%" 2>nul
del /q "%FFMPEG_ZIP%" 2>nul

:ffmpeg_ready
echo [5/7] Checking Python source syntax...
"%VENV_PY%" -m compileall -q "%APP_DIR%"
if errorlevel 1 (
  set "FAIL_REASON=At least one Python file failed the syntax check."
  goto :fail
)

echo [6/7] Building the one-file Windows application...
"%VENV_PY%" -m PyInstaller --noconfirm --clean "Eshra7lyDownloader.spec"
if errorlevel 1 (
  set "FAIL_REASON=PyInstaller failed. Scroll up for the build error details."
  goto :fail
)
if not exist "%ROOT%\dist\Eshra7lyDownloader.exe" (
  set "FAIL_REASON=The build ended without producing dist\Eshra7lyDownloader.exe."
  goto :fail
)

echo [7/7] Build complete.
echo.
echo ============================================================
echo SUCCESS: dist\Eshra7lyDownloader.exe
echo ============================================================
echo.
echo This is the standalone EXE. Python, Chromium, FFmpeg, and the
echo application's Python dependencies are bundled into it.
echo The first launch can take longer because one-file apps extract
echo their bundled runtime files to a temporary folder.
echo.
start "" explorer.exe "%ROOT%\dist"
pause
exit /b 0

:fail
echo.
echo ============================================================
echo BUILD FAILED
echo %FAIL_REASON%
echo ============================================================
echo.
pause
exit /b 1
