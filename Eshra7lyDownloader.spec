# -*- mode: python ; coding: utf-8 -*-
"""One-file Windows GUI bundle. Build after downloading Chromium and FFmpeg."""
from pathlib import Path
from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve()
APP = ROOT / "course_archiver"
BROWSER_DIR = APP / "browser"
BUILD_TOOLS = ROOT / "build-tools"
FFMPEG = BUILD_TOOLS / "ffmpeg.exe"
FFPROBE = BUILD_TOOLS / "ffprobe.exe"
FFMPEG_LICENSE = BUILD_TOOLS / "FFmpeg-LICENSE.txt"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.txt"

for required in (BROWSER_DIR, FFMPEG, FFPROBE, FFMPEG_LICENSE, NOTICES):
    if not required.exists():
        raise FileNotFoundError(
            f"Missing bundled build input: {required}. Run the Windows packaging workflow first."
        )

ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")
pw_datas, pw_binaries, pw_hiddenimports = collect_all("playwright")

datas = []
datas += ctk_datas
datas += pw_datas
datas += list(Tree(str(BROWSER_DIR), prefix="browser"))
datas += [(str(FFMPEG_LICENSE), "licenses")]
datas += [(str(NOTICES), "licenses")]

binaries = []
binaries += ctk_binaries
binaries += pw_binaries
binaries += [(str(FFMPEG), "tools"), (str(FFPROBE), "tools")]

hiddenimports = sorted(set(
    ctk_hiddenimports
    + pw_hiddenimports
    + [
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "PIL._tkinter_finder",
        "playwright.sync_api",
        "playwright._impl._driver",
    ]
))

a = Analysis(
    [str(APP / "gui.py")],
    pathex=[str(APP)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Eshra7lyDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
