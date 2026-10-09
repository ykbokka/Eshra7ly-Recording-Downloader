ESHRA7LY DOWNLOADER
Mac-inspired desktop interface + authorized recording archive pipeline

QUICK START (WINDOWS)
1. Open PowerShell in this folder (course_archiver).
2. Install Python dependencies:
     python -m pip install -r requirements.txt
3. Install the project-local Chromium browser:
     $env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\browser"
     python -m playwright install chromium
4. Install FFmpeg and FFprobe, for example:
     winget install Gyan.FFmpeg
   Close and reopen your terminal after installation if FFmpeg is not found.
5. Launch the desktop app:
     python gui.py

The first launch opens a clean desktop window. Choose quality and output folder, then click
"Choose recording". Chromium opens Eshra7ly. If sign-in is needed, enter your credentials in
the app's local sign-in dialog; the password is not saved. Complete any one-time code or human
verification in the visible browser. Course, group, and recording selection are shown in the app.

BACKGROUND BROWSER MODE
- Open Settings and enable "Run Chromium in the background" to run the browser headlessly.
- First sign in with background mode disabled and finish any required verification in the visible browser.
- If the site later requests a one-time code or human verification, disable background mode and retry.
- If headless sign-in cannot finish automatically, the app will show an error explaining how to switch back.

COMMAND-LINE MODE
The original CLI is still available:
     python app.py
     python app.py --probe
     python app.py --quality 1080 --name "Lesson 01" --out D:\Archive
     python app.py --audio flac
     python app.py --debug
     python app.py --help

GUI NOTES
- Preferences are stored in gui_settings.json next to the app.
- Credentials are not written to settings or logs.
- The browser uses a project-local Chromium profile in browser_profile; it does not use Opera GX.
- The recording chooser sorts recordings with detected dates oldest to newest; recordings whose
  dates cannot be read are placed at the bottom.
- The default audio mode copies the original AAC stream. FLAC transcodes the AAC source and cannot
  restore quality that was lost in the original AAC.
- Downloads are saved as MKV files. Existing valid outputs are reused unless changed or re-downloaded.
- Use this only for recordings you are authorized to access.

MEDIA AND SECURITY
- HLS playlists are captured from the normal browser player; signed URLs remain in memory.
- Only selected request headers are forwarded to FFmpeg. Cookies and Authorization headers are not
  captured or forwarded.
- FFmpeg protocol access is restricted to https,tls,tcp.
- Encrypted/DRM playlists are refused. DRM, login protection, and access-control bypass are not supported.
- HTTP 401/403/404/410 from the CDN often means a signed link expired or the stream is unavailable
  to this session; run again to capture fresh links.

TESTS
     python -m unittest discover -s tests -t . -v
