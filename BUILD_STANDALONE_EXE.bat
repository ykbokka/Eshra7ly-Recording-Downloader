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

rem Extract FFmpeg directly from the archive with Python instead of relying on
rem batch path variables or a temporary extracted directory.
"%VENV_PY%" "%ROOT%\prepare_ffmpeg_bundle.py" --destination "%BUILD_TOOLS%" --temp-dir "%TEMP%"
if errorlevel 1 (
  set "FAIL_REASON=The FFmpeg bundler failed. Read the detailed diagnostic above."
  goto :fail
)

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
