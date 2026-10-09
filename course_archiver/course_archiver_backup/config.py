"""Machine-specific settings. Override with environment variables instead of editing code if you like."""
import os

# Opera GX (Chromium) launched through Playwright with the existing profile.
BROWSER_EXECUTABLE = os.environ.get(
    "ESHRA7LY_BROWSER_EXE",
    r"C:\Users\Souhaib Bokka\AppData\Local\Programs\Opera GX\opera.exe",
)
BROWSER_PROFILE_DIR = os.environ.get(
    "ESHRA7LY_PROFILE_DIR",
    r"C:\Users\Souhaib Bokka\AppData\Roaming\Opera Software\Opera GX Stable",
)

# How long to wait (seconds) for you to filter/open a recording after the Recordings tab is clicked.
CAPTURE_TIMEOUT = 300
# Extra seconds to keep listening after the first playlists show up (player may request more).
CAPTURE_SETTLE = 6

DEFAULT_OUTPUT_DIR = "downloads"
