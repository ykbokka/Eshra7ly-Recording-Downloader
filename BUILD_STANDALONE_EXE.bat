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

rem Python is required only on the build PC, not on PCs that run the finished EXE.
rem Probe actual interpreter startup because the Python install manager can print install hints.
set "PY_CMD="
set "PY_PROBE=%TEMP%\Eshra7ly_PythonProbe_%RANDOM%.txt"
for %%V in (3.14 3.13 3.12 3.11 3.10 3.9) do (
  py -%%V -c "import sys; print('ESHRA7LY_PYTHON_OK') if sys.version_info >= (3, 9) else None" >"%PY_PROBE%" 2>&1
  findstr /x /c:"ESHRA7LY_PYTHON_OK" "%PY_PROBE%" >nul 2>&1
  if not errorlevel 1 if not defined PY_CMD set "PY_CMD=py -%%V"
)
if not defined PY_CMD (
  python -c "import sys; print('ESHRA7LY_PYTHON_OK') if sys.version_info >= (3, 9) else None" >"%PY_PROBE%" 2>&1
  findstr /x /c:"ESHRA7LY_PYTHON_OK" "%PY_PROBE%" >nul 2>&1
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  where py >nul 2>&1
  if not errorlevel 1 (
    echo No usable Python runtime was found. The Python install manager is available.
    choice /c YN /m "Install Python 3.11 now using 'py install 3.11'"
    if errorlevel 2 (
      set "FAIL_REASON=No working Python 3.9+ runtime was found. Install Python 3.11 and rerun this BAT file."
      goto :fail
    )
    py install 3.11
    if errorlevel 1 (
      set "FAIL_REASON=The Python install manager could not install Python 3.11. Install Python from python.org and rerun this BAT file."
      goto :fail
    )
    py -3.11 -c "import sys; print('ESHRA7LY_PYTHON_OK') if sys.version_info >= (3, 9) else None" >"%PY_PROBE%" 2>&1
    findstr /x /c:"ESHRA7LY_PYTHON_OK" "%PY_PROBE%" >nul 2>&1
    if errorlevel 1 (
      set "FAIL_REASON=Python 3.11 installation finished, but the runtime could not be launched. Restart the terminal and retry."
      goto :fail
    )
    set "PY_CMD=py -3.11"
  ) else (
    set "FAIL_REASON=Python 3.9 or newer is required on the build PC. Install Python 3.11 (64-bit), then run this BAT again."
    goto :fail
  )
)
del /q "%PY_PROBE%" >nul 2>&1
echo Using build interpreter: %PY_CMD%
%PY_CMD% -c "import sys; print(sys.executable); print(sys.version)"
if errorlevel 1 (
  set "FAIL_REASON=The selected Python runtime could not start. Install a working Python 3.11+ runtime and retry."
  goto :fail
)

if exist "%VENV_DIR%" if not exist "%VENV_PY%" rmdir /s /q "%VENV_DIR%"
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; print('ESHRA7LY_VENV_OK') if sys.version_info >= (3, 9) else None" >"%PY_PROBE%" 2>&1
  findstr /x /c:"ESHRA7LY_VENV_OK" "%PY_PROBE%" >nul 2>&1
  if errorlevel 1 rmdir /s /q "%VENV_DIR%"
  del /q "%PY_PROBE%" >nul 2>&1
)
if not exist "%VENV_PY%" (
  echo [1/7] Creating an isolated build environment...
  %PY_CMD% -m venv "%VENV_DIR%"
  if errorlevel 1 (
    set "FAIL_REASON=Could not create the Python build environment. Check that the selected Python includes the venv module."
    goto :fail
  )
  if not exist "%VENV_PY%" (
    set "FAIL_REASON=Python returned without creating .build_venv\Scripts\python.exe. Remove the .build_venv folder and retry."
    goto :fail
  )
  "%VENV_PY%" -c "import sys; print('ESHRA7LY_VENV_OK') if sys.version_info >= (3, 9) else None" >"%PY_PROBE%" 2>&1
  findstr /x /c:"ESHRA7LY_VENV_OK" "%PY_PROBE%" >nul 2>&1
  if errorlevel 1 (
    set "FAIL_REASON=The new virtual environment was created but its Python could not be started. Delete .build_venv and retry."
    goto :fail
  )
  del /q "%PY_PROBE%" >nul 2>&1
)
if not exist "%VENV_PY%" (
  set "FAIL_REASON=The build Python environment is missing. Delete .build_venv and run this BAT again."
  goto :fail
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

echo [3/7] Preparing the bundled headless Chromium browser...
rem Preserve an existing download so rerunning after a later failure doesn't redownload it.
if not exist "%APP_DIR%\browser" mkdir "%APP_DIR%\browser"
set "PLAYWRIGHT_BROWSERS_PATH=%APP_DIR%\browser"
rem The app always runs Chromium headlessly, so the full GUI browser is not needed.
rem Remove only full-Chromium folders; keep chromium_headless_shell and its downloaded files.
for /d %%D in ("%APP_DIR%\browser\chromium-*") do if exist "%%~fD" rmdir /s /q "%%~fD"
"%VENV_PY%" -m playwright install --only-shell chromium
if errorlevel 1 (
  set "FAIL_REASON=Playwright could not download Chromium headless shell. Check the connection and retry."
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

rem Reuse an extracted archive left behind by the previous failed build, if one exists.
set "FFMPEG_WORK="
set "FFMPEG_FILE="
set "FFPROBE_FILE="
set "REUSED_FFMPEG_WORK="
for /d %%D in ("%TEMP%\Eshra7ly_FFmpeg_*") do if not defined REUSED_FFMPEG_WORK call :check_cached_ffmpeg "%%~fD"
if defined REUSED_FFMPEG_WORK (
  set "FFMPEG_WORK=%REUSED_FFMPEG_WORK%"
  echo Reusing extracted FFmpeg files from the previous build attempt...
  goto :ffmpeg_files_ready
)

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

for /r "%FFMPEG_WORK%" %%F in (ffmpeg.exe) do if not defined FFMPEG_FILE set "FFMPEG_FILE=%%F"
for /r "%FFMPEG_WORK%" %%F in (ffprobe.exe) do if not defined FFPROBE_FILE set "FFPROBE_FILE=%%F"

:ffmpeg_files_ready
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

rem Stage the executables immediately, before license downloads give antivirus time to quarantine
rem or remove files from the extracted archive. This also makes the copy failure more specific.
echo Staging FFmpeg binaries now...
echo FFmpeg source: "%FFMPEG_FILE%"
echo FFprobe source: "%FFPROBE_FILE%"
if not exist "%FFMPEG_FILE%" (
  set "FAIL_REASON=FFmpeg source file is missing immediately after extraction. Check Windows Security Protection History. The script will not disable antivirus."
  goto :fail
)
if not exist "%FFPROBE_FILE%" (
  set "FAIL_REASON=FFprobe source file is missing immediately after extraction. Check Windows Security Protection History. The script will not disable antivirus."
  goto :fail
)

del /f /q "%BUILD_TOOLS%\ffmpeg.exe" "%BUILD_TOOLS%\ffprobe.exe" "%BUILD_TOOLS%\FFmpeg-LICENSE.txt" 2>nul
for %%F in ("%BUILD_TOOLS%\*.dll") do if exist "%%~fF" del /f /q "%%~fF"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Copy-Item -LiteralPath $env:FFMPEG_FILE -Destination (Join-Path $env:BUILD_TOOLS 'ffmpeg.exe') -Force"
if errorlevel 1 (
  set "FAIL_REASON=PowerShell couldn't copy ffmpeg.exe into build-tools. Read the error above and check Windows Security Protection History."
  goto :fail
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Copy-Item -LiteralPath $env:FFPROBE_FILE -Destination (Join-Path $env:BUILD_TOOLS 'ffprobe.exe') -Force"
if errorlevel 1 (
  set "FAIL_REASON=PowerShell couldn't copy ffprobe.exe into build-tools. Read the error above and check Windows Security Protection History."
  goto :fail
)
rem A shared FFmpeg build needs the DLLs stored beside its executables.
for %%F in ("%FFMPEG_BIN%*.dll") do if exist "%%~fF" copy /y "%%~fF" "%BUILD_TOOLS%\" >nul
if not exist "%BUILD_TOOLS%\ffmpeg.exe" (
  set "FAIL_REASON=ffmpeg.exe disappeared after staging. Check Windows Security Protection History."
  goto :fail
)
if not exist "%BUILD_TOOLS%\ffprobe.exe" (
  set "FAIL_REASON=ffprobe.exe disappeared after staging. Check Windows Security Protection History."
  goto :fail
)
"%BUILD_TOOLS%\ffmpeg.exe" -hide_banner -version >nul 2>&1
if errorlevel 1 (
  set "FAIL_REASON=The staged FFmpeg could not start. Its dependent DLLs may be missing or Windows Security may have quarantined a file."
  goto :fail
)
"%BUILD_TOOLS%\ffprobe.exe" -hide_banner -version >nul 2>&1
if errorlevel 1 (
  set "FAIL_REASON=The staged FFprobe could not start. Its dependent DLLs may be missing or Windows Security may have quarantined a file."
  goto :fail
)

rem Prefer the license notice supplied with the downloaded package, if present.
set "FFMPEG_LICENSE_SOURCE="
for /r "%FFMPEG_WORK%" %%F in (LICENSE.txt) do if not defined FFMPEG_LICENSE_SOURCE set "FFMPEG_LICENSE_SOURCE=%%F"

rem BtbN shared builds do not always ship a LICENSE.txt. If absent, bundle the
rem official FFmpeg license overview and both GPL/LGPL license texts from upstream.
if defined FFMPEG_LICENSE_SOURCE goto :license_ready
echo No packaged LICENSE.txt found. Downloading official FFmpeg license texts...
set "LICENSE_DIR=%FFMPEG_WORK%\ffmpeg-license-texts"
if not exist "%LICENSE_DIR%" mkdir "%LICENSE_DIR%"
curl.exe -fL --retry 2 --connect-timeout 20 "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/LICENSE.md" -o "%LICENSE_DIR%\LICENSE.md"
if errorlevel 1 (
  set "FAIL_REASON=Could not download the official FFmpeg license overview."
  goto :fail
)
curl.exe -fL --retry 2 --connect-timeout 20 "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.GPLv2" -o "%LICENSE_DIR%\COPYING.GPLv2"
if errorlevel 1 (
  set "FAIL_REASON=Could not download the official GPLv2 license text."
  goto :fail
)
curl.exe -fL --retry 2 --connect-timeout 20 "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.GPLv3" -o "%LICENSE_DIR%\COPYING.GPLv3"
if errorlevel 1 (
  set "FAIL_REASON=Could not download the official GPLv3 license text."
  goto :fail
)
curl.exe -fL --retry 2 --connect-timeout 20 "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.LGPLv2.1" -o "%LICENSE_DIR%\COPYING.LGPLv2.1"
if errorlevel 1 (
  set "FAIL_REASON=Could not download the official LGPLv2.1 license text."
  goto :fail
)
curl.exe -fL --retry 2 --connect-timeout 20 "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.LGPLv3" -o "%LICENSE_DIR%\COPYING.LGPLv3"
if errorlevel 1 (
  set "FAIL_REASON=Could not download the official LGPLv3 license text."
  goto :fail
)
(
  echo FFmpeg official licensing information
  echo.
  echo LICENSE.md explains the upstream FFmpeg license choices.
  echo The GPL and LGPL documents below are included as reference texts.
  echo Which terms apply depends on the binary configuration and its dependencies.
  echo.
  echo ============================================================
  echo FFmpeg LICENSE.md
  echo ============================================================
  type "%LICENSE_DIR%\LICENSE.md"
  echo.
  echo ============================================================
  echo COPYING.GPLv2
  echo ============================================================
  type "%LICENSE_DIR%\COPYING.GPLv2"
  echo.
  echo ============================================================
  echo COPYING.GPLv3
  echo ============================================================
  type "%LICENSE_DIR%\COPYING.GPLv3"
  echo.
  echo ============================================================
  echo COPYING.LGPLv2.1
  echo ============================================================
  type "%LICENSE_DIR%\COPYING.LGPLv2.1"
  echo.
  echo ============================================================
  echo COPYING.LGPLv3
  echo ============================================================
  type "%LICENSE_DIR%\COPYING.LGPLv3"
) > "%FFMPEG_WORK%\FFmpeg-LICENSE.txt"
if errorlevel 1 (
  set "FAIL_REASON=Could not assemble the FFmpeg licensing information."
  goto :fail
)
set "FFMPEG_LICENSE_SOURCE=%FFMPEG_WORK%\FFmpeg-LICENSE.txt"

:license_ready

rem Validate source paths first: antivirus quarantine or a partial archive can remove files after discovery.
echo FFmpeg source: "%FFMPEG_FILE%"
echo FFprobe source: "%FFPROBE_FILE%"
echo FFmpeg license source: "%FFMPEG_LICENSE_SOURCE%"
if not exist "%FFMPEG_FILE%" (
  set "FAIL_REASON=The discovered ffmpeg.exe disappeared or was quarantined. Check Windows Security Protection History, then rerun the builder."
  goto :fail
)
if not exist "%FFPROBE_FILE%" (
  set "FAIL_REASON=The discovered ffprobe.exe disappeared or was quarantined. Check Windows Security Protection History, then rerun the builder."
  goto :fail
)
if not exist "%FFMPEG_LICENSE_SOURCE%" (
  set "FAIL_REASON=The FFmpeg license notice source could not be found."
  goto :fail
)

rem Bundle the applicable license texts next to the staged tools.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Copy-Item -LiteralPath $env:FFMPEG_LICENSE_SOURCE -Destination (Join-Path $env:BUILD_TOOLS 'FFmpeg-LICENSE.txt') -Force; if (-not (Test-Path (Join-Path $env:BUILD_TOOLS 'FFmpeg-LICENSE.txt'))) { throw 'Destination FFmpeg-LICENSE.txt was not created.' }"
if errorlevel 1 (
  set "FAIL_REASON=Could not stage the FFmpeg license notice. Read the PowerShell copy error above."
  goto :fail
)
"%BUILD_TOOLS%\ffmpeg.exe" -hide_banner -version >> "%BUILD_TOOLS%\FFmpeg-LICENSE.txt" 2>&1
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

:check_cached_ffmpeg
set "CANDIDATE_FF="
set "CANDIDATE_FP="
for /r "%~1" %%F in (ffmpeg.exe) do if not defined CANDIDATE_FF set "CANDIDATE_FF=%%F"
for /r "%~1" %%F in (ffprobe.exe) do if not defined CANDIDATE_FP set "CANDIDATE_FP=%%F"
if defined CANDIDATE_FF if defined CANDIDATE_FP (
  set "REUSED_FFMPEG_WORK=%~1"
  set "FFMPEG_FILE=%CANDIDATE_FF%"
  set "FFPROBE_FILE=%CANDIDATE_FP%"
) else (
  set "FFMPEG_FILE="
  set "FFPROBE_FILE="
)
exit /b
