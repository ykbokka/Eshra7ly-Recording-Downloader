# Building the standalone Windows EXE

The repository includes a GitHub Actions workflow that builds the GUI as a single Windows x64 executable. It bundles Python, the GUI dependencies, Playwright, Chromium, FFmpeg, and FFprobe into the application.

## Recommended build

Open the **Actions** tab of this repository, choose **Build standalone Windows EXE**, and select **Run workflow** on the `feature/macos-desktop-gui` branch. After the job succeeds, open that workflow run and download the artifact named `Eshra7lyDownloader-Windows-x64`.

A push to `feature/macos-desktop-gui` also starts a build automatically. Artifacts are kept for 14 days.

## What the one-file build does

- Bundles Chromium in the EXE; users do not install a browser or Playwright separately.
- Bundles FFmpeg and FFprobe; users do not install FFmpeg separately.
- Stores preferences, the browser profile, and hidden working files under `%LOCALAPPDATA%\Eshra7ly Downloader\`.
- Defaults recordings to `%USERPROFILE%\Videos\Eshra7ly Downloader\`.
- Extracts its bundled runtime resources to a temporary PyInstaller directory while running. That extraction is normal for a one-file executable and is removed by the bootloader when the app exits normally.

The EXE still needs internet access and a Windows x64 system. The estimate shown before download is approximate, not a guarantee of final file size.

## Local build inputs

The spec expects these generated inputs:
- `course_archiver/browser/`: Chromium installed with the same Playwright version as `course_archiver/requirements.txt`.
- `build-tools/ffmpeg.exe` and `build-tools/ffprobe.exe`.
- `build-tools/FFmpeg-LICENSE.txt`, copied from the exact FFmpeg archive used.

The workflow downloads these inputs and creates the one-file executable automatically. Read `THIRD_PARTY_NOTICES.txt` and the included FFmpeg license notice before redistributing builds.
