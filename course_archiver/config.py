"""Machine-specific settings. Override paths with environment variables if needed."""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
BROWSER_PROFILE_DIR = os.environ.get(
    "ESHRA7LY_PROFILE_DIR",
    str(PROJECT_DIR / "browser_profile"),
)

# How long to wait for the selected recording's HLS playlists.
CAPTURE_TIMEOUT = 300
# Extra seconds to listen after the first master playlist appears.
CAPTURE_SETTLE = 6

DEFAULT_OUTPUT_DIR = "downloads"
