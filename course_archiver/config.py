"""Machine-specific paths and settings for source and frozen builds."""
import os
import sys
from pathlib import Path


def _app_data_path():
    """Return a writable, persistent per-user directory outside PyInstaller's extraction folder."""
    home = Path.home()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    return base / "Eshra7ly Downloader"


PROJECT_DIR = Path(__file__).resolve().parent
APP_DATA_DIR = _app_data_path()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

BROWSER_PROFILE_DIR = os.environ.get(
    "ESHRA7LY_PROFILE_DIR",
    str(APP_DATA_DIR / "browser_profile"),
)

# Recordings default to a normal user-owned folder, never the EXE install or extraction directory.
DEFAULT_OUTPUT_DIR = str(Path.home() / "Videos" / "Eshra7ly Downloader")

# How long to wait for the selected recording's HLS playlists.
CAPTURE_TIMEOUT = 300
# Extra seconds to listen after the first master playlist appears.
CAPTURE_SETTLE = 6
