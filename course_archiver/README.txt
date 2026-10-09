COURSE ARCHIVER - STAGE 1 (media pipeline)

What it does
  Course page -> Recordings tab -> you open a recording -> app captures the HLS playlists your own
  browser session received -> classifies video vs audio from the master playlist (never "first .m3u8")
  -> FFmpeg copies the video to video.mkv -> FFmpeg copies the audio to audio.mka (original AAC, or
  --audio flac) -> FFmpeg muxes to <name>.mkv -> ffprobe validates -> temp files removed.

Setup (Windows)
  pip install -r requirements.txt
  winget install Gyan.FFmpeg          (open a NEW terminal afterwards; ffmpeg AND ffprobe must be on PATH)
  Edit config.py or set ESHRA7LY_BROWSER_EXE / ESHRA7LY_PROFILE_DIR if your Opera GX paths differ.
  Close Opera GX completely before running (the profile folder is locked while it is open).

Run (from this folder)
  python app.py                                  # asks for the course URL
  python app.py <url> --probe                    # detect only: shows quality, duration, video/audio roles
  python app.py <url> --quality 1080 --name "Lesson 01" --out D:\Archive
  python app.py <url> --audio flac               # AAC -> FLAC TRANSCODE (see note)
  python app.py <url> --debug                    # prints ffmpeg commands with signed URLs redacted
  python app.py --help

When the browser opens: the app clicks "Recordings" for you. Apply the course filter and OPEN the
recording yourself. As soon as the player requests its playlists the app captures them and closes
the browser (it waits CAPTURE_TIMEOUT seconds, default 300). Ctrl+C during a download stops FFmpeg
cleanly; partial files stay in .tmp_<name>.

Audio note
  The source audio is AAC (lossy). Default `--audio copy` keeps those exact AAC bytes. `--audio flac`
  re-encodes AAC to FLAC: it is a transcode into a lossless container, it does NOT restore anything
  AAC discarded and makes the file larger. Use it only if you specifically need FLAC.

Security / scope
  * Only your already-logged-in browser profile is used, through Playwright. No cookie reading,
    decryption, password handling, DRM or access-control bypass. Cookies/Authorization are never
    captured or forwarded; only Referer, Origin, User-Agent and Accept-Language the player itself sent.
  * Signed playlist URLs live in memory only and are redacted in all console output.
  * FFmpeg is limited to https (protocol whitelist), so a hostile playlist cannot read local files.
  * Encrypted/DRM playlists are detected and refused.
  * A 401/403/404/410 from the CDN is reported as "link expired or not available to this session".

Tests (no internet, no account data; needs ffmpeg + ffprobe):
  python -m unittest discover -s tests -t . -v

Not yet built (by design): GUI, queue, library manager, metadata files, packaging.
