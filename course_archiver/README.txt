COURSE ARCHIVER - AUTOMATIC ESHRA7LY NAVIGATION + MEDIA PIPELINE

What it does
  Starts at https://eshra7ly.net/student/recordings, asks for login details in the terminal only
  when needed, lets you choose a course, recording group, and unlocked recording, clicks Play,
  captures HLS playlists requested by the normal player, classifies video/audio streams, and uses
  FFmpeg to download and mux the recording into an MKV file.

Setup (Windows)
  pip install -r requirements.txt
  python -m playwright install chromium
  winget install Gyan.FFmpeg
  Open a NEW terminal afterwards; ffmpeg and ffprobe must both be on PATH.

Run (from this folder)
  python app.py
  python app.py --probe
  python app.py --quality 1080 --name "Lesson 01" --out D:\Archive
  python app.py --audio flac
  python app.py --debug
  python app.py --help

Login and privacy
  * Login credentials are requested locally in the terminal; the password is hidden while typing.
  * Credentials are not written to config files or logs. Do not send your password to anyone.
  * If the site requires a one-time code or human verification, complete that step in the opened browser.
  * The app uses a project-local Playwright Chromium profile in browser_profile; it does not use Opera GX.
  * Use this only for recordings you are authorized to access.

Media pipeline
  * Captures HLS playlist responses from the normal browser player and keeps signed URLs in memory.
  * Only Referer, Origin, User-Agent and Accept-Language are forwarded; cookies and Authorization
    headers are not captured or forwarded to FFmpeg.
  * FFmpeg protocol access is restricted to https,tls,tcp to prevent playlists from reading local files.
  * Encrypted/DRM playlists are refused. No DRM, login protection, or access-control bypass is attempted.
  * Default audio mode copies the original AAC stream. --audio flac transcodes that lossy AAC to FLAC;
    it cannot restore quality lost in the original AAC.
  * A 401/403/404/410 from the CDN usually means the signed link expired or the stream is unavailable
    to this session; run again to capture fresh links.

Output
  Files are written to downloads by default. Existing valid files are not downloaded again unless
  --force is supplied. Temporary files are retained on failure for inspection.

Tests
  python -m unittest discover -s tests -t . -v
