#!/usr/bin/env python3
"""Stage FFmpeg directly from its ZIP into build-tools, without an intermediate extraction tree."""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

import requests


GITHUB_BUILD = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl-shared.zip"
)
GYAN_BUILD = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
LICENSE_FILES = (
    ("FFmpeg LICENSE.md", "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/LICENSE.md"),
    ("COPYING.GPLv2", "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.GPLv2"),
    ("COPYING.GPLv3", "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.GPLv3"),
    ("COPYING.LGPLv2.1", "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.LGPLv2.1"),
    ("COPYING.LGPLv3", "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.LGPLv3"),
)
HEADERS = {"User-Agent": "Eshra7ly-Downloader-Build/1.0"}


def say(message: str) -> None:
    print(message, flush=True)


def find_pair(archive: zipfile.ZipFile):
    files = [item for item in archive.infolist() if not item.is_dir()]
    names = {item.filename.replace("\\", "/").lower(): item for item in files}
    candidates = []
    for item in files:
        path = PurePosixPath(item.filename.replace("\\", "/"))
        if path.name.lower() != "ffmpeg.exe":
            continue
        sibling = str(path.parent / "ffprobe.exe")
        probe = names.get(sibling.lower())
        if probe is not None:
            candidates.append((item, probe, path.parent))
    if not candidates:
        available = [
            item.filename for item in files
            if PurePosixPath(item.filename.replace("\\", "/")).name.lower()
            in ("ffmpeg.exe", "ffprobe.exe")
        ]
        raise ValueError(
            "The ZIP doesn't contain ffmpeg.exe and ffprobe.exe in the same folder. "
            f"Found: {available[:12] or 'no matching executables'}"
        )
    # Prefer the pair whose files are in a bin folder, then the shortest path.
    candidates.sort(key=lambda pair: ("bin" not in str(pair[2]).lower(), len(str(pair[2]))))
    return candidates[0]


def validate_archive(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            find_pair(archive)
        return True
    except (OSError, zipfile.BadZipFile, ValueError):
        return False


def download_archive(temp_dir: Path) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        temp_dir.glob("Eshra7ly_FFmpeg_*.zip"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for archive_path in existing:
        if validate_archive(archive_path):
            say(f"Reusing FFmpeg archive: {archive_path}")
            return archive_path
        say(f"Skipping incomplete or unsuitable FFmpeg archive: {archive_path.name}")

    sources = (
        ("GitHub FFmpeg shared build", GITHUB_BUILD),
        ("Gyan FFmpeg build", GYAN_BUILD),
    )
    errors = []
    for title, url in sources:
        target = temp_dir / f"Eshra7ly_FFmpeg_{int(time.time())}_{os.getpid()}.zip"
        partial = target.with_suffix(".download")
        say(f"Downloading {title}...")
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=(20, 60)) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", "0") or 0)
                received = 0
                last_update = time.monotonic()
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        received += len(chunk)
                        if time.monotonic() - last_update >= 2:
                            if total:
                                say(f"  {received / 1048576:.1f} / {total / 1048576:.1f} MiB")
                            else:
                                say(f"  {received / 1048576:.1f} MiB received")
                            last_update = time.monotonic()
            if not validate_archive(partial):
                raise ValueError("The downloaded archive is incomplete or lacks the required executables.")
            partial.replace(target)
            say(f"Downloaded and validated: {target}")
            return target
        except Exception as exc:
            errors.append(f"{title}: {exc}")
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            say(f"  Download attempt failed: {exc}")

    raise RuntimeError("Both FFmpeg download sources failed. " + " | ".join(errors))


def fetch_license_texts() -> str:
    parts = [
        "FFmpeg third-party licensing information",
        "",
        "The exact applicable license is determined by this FFmpeg binary's build configuration.",
        "The documents below are included for reference; see the bundled FFmpeg version/configuration.",
        "",
    ]
    for name, url in LICENSE_FILES:
        say(f"Fetching license document: {name}")
        with requests.get(url, headers=HEADERS, timeout=(15, 30)) as response:
            response.raise_for_status()
            text = response.text
        if not text.strip():
            raise RuntimeError(f"Downloaded an empty license document: {name}")
        parts.extend([
            "=" * 72,
            name,
            url,
            "=" * 72,
            text,
            "",
        ])
    return "\n".join(parts)


def stage(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path) as archive:
        ffmpeg_info, ffprobe_info, bin_dir = find_pair(archive)
        bin_prefix = str(bin_dir).rstrip("/")
        dll_infos = [
            item for item in archive.infolist()
            if not item.is_dir()
            and str(PurePosixPath(item.filename.replace("\\", "/")).parent) == bin_prefix
            and PurePosixPath(item.filename.replace("\\", "/")).suffix.lower() == ".dll"
        ]
        bundled_license = next(
            (
                item for item in archive.infolist()
                if not item.is_dir()
                and PurePosixPath(item.filename.replace("\\", "/")).name.lower() == "license.txt"
            ),
            None,
        )

        # Resolve licensing before changing an existing working staging area.
        if bundled_license:
            with archive.open(bundled_license) as source:
                license_text = source.read().decode("utf-8", errors="replace")
            license_text = (
                "FFmpeg licensing information copied from the downloaded FFmpeg package.\n\n"
                + license_text
            )
        else:
            license_text = fetch_license_texts()

        say(f"FFmpeg archive member: {ffmpeg_info.filename}")
        say(f"FFprobe archive member: {ffprobe_info.filename}")
        say(f"Bundling {len(dll_infos)} neighboring DLL files.")

        for old in (destination / "ffmpeg.exe", destination / "ffprobe.exe",
                    destination / "FFmpeg-LICENSE.txt"):
            try:
                old.unlink(missing_ok=True)
            except OSError as exc:
                raise RuntimeError(f"Cannot remove previous staged file {old}: {exc}") from exc
        for old in destination.glob("*.dll"):
            try:
                old.unlink(missing_ok=True)
            except OSError as exc:
                raise RuntimeError(f"Cannot replace previous FFmpeg DLL {old}: {exc}") from exc

        for info, name in ((ffmpeg_info, "ffmpeg.exe"), (ffprobe_info, "ffprobe.exe")):
            target = destination / name
            try:
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not extract {info.filename} directly to {target}: {exc}"
                ) from exc
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError(f"{target} is missing or empty after extraction.")
            say(f"Created {target} ({target.stat().st_size / 1048576:.1f} MiB)")

        for info in dll_infos:
            name = PurePosixPath(info.filename.replace("\\", "/")).name
            target = destination / name
            try:
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except Exception as exc:
                raise RuntimeError(f"Could not extract required FFmpeg DLL {name}: {exc}") from exc

    ffmpeg_path = destination / "ffmpeg.exe"
    ffprobe_path = destination / "ffprobe.exe"
    run_env = os.environ.copy()
    run_env["PATH"] = str(destination) + os.pathsep + run_env.get("PATH", "")
    say("Verifying staged FFmpeg...")
    result = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-version"],
        cwd=str(destination), env=run_env, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "The staged FFmpeg executable did not start. "
            "This often means a dependent DLL is missing.\n"
            + (result.stderr or result.stdout or "No diagnostic output.")
        )
    version = result.stdout or result.stderr
    say("Verifying staged FFprobe...")
    probe = subprocess.run(
        [str(ffprobe_path), "-hide_banner", "-version"],
        cwd=str(destination), env=run_env, capture_output=True, text=True, timeout=30,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "The staged FFprobe executable did not start.\n"
            + (probe.stderr or probe.stdout or "No diagnostic output.")
        )

    license_path = destination / "FFmpeg-LICENSE.txt"
    license_path.write_text(
        license_text + "\n\n" + "=" * 72 + "\nBundled FFmpeg build details\n"
        + "=" * 72 + "\n" + version,
        encoding="utf-8",
    )
    say("FFmpeg and FFprobe are staged and runnable.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--temp-dir", required=True, type=Path)
    args = parser.parse_args()

    try:
        archive = download_archive(args.temp_dir)
        stage(archive, args.destination)
        return 0
    except Exception as exc:
        say("")
        say("[FFmpeg bundler error]")
        say(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
