import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(PROJECT_DIR / "browser")

from playwright.sync_api import sync_playwright

import config
from core import hls_parser as hls
from core.errors import StreamDetectionError
from core.models import Capture, safe_headers
from core.redact import redact_url
from .base import baseplatform

RECORDING_TEXTS = ["recordings", "recording", "تسجيلات", "التسجيلات", "تسجيل"]
HLS_TYPES = ("mpegurl",)


def _is_hls_response(url: str, content_type: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return path.endswith(".m3u8") or any(t in (content_type or "").lower() for t in HLS_TYPES)


class eshra7lyplatform(baseplatform):
    def __init__(self, browser_exe=None, profile_dir=None, capture_timeout=None, settle=None):
        self.browser_exe = None
        self.profile_dir = str(PROJECT_DIR / "browser_profile") if profile_dir is None else profile_dir
        self.capture_timeout = capture_timeout or config.CAPTURE_TIMEOUT
        self.settle = config.CAPTURE_SETTLE if settle is None else settle

    def _click_recordings_tab(self, page) -> bool:
        for locator in [page.locator("a"), page.locator("button"), page.locator("[role='tab']")]:
            for element in locator.all():
                try:
                    text = element.inner_text().strip().lower()
                    if any(w.lower() in text for w in RECORDING_TEXTS):
                        print("clicking recordings tab:", text)
                        element.click()
                        return True
                except Exception:
                    pass
        print("Could not find recordings tab automatically; open it yourself.")
        return False

    def extract_media_info(self, url: str) -> dict:
        captures = []
        seq = [0]
        title = ""

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=False,
            )

            try:
                def on_response(response):
                    try:
                        ctype = response.headers.get("content-type", "")
                        if response.status != 200 or not _is_hls_response(response.url, ctype):
                            return

                        text = response.text()
                        if not text.lstrip("\ufeff").lstrip().startswith("#EXTM3U"):
                            return

                        try:
                            raw = response.request.all_headers()
                        except Exception:
                            raw = response.request.headers

                        seq[0] += 1
                        captures.append(
                            Capture(seq[0], response.url, text, safe_headers(raw))
                        )

                        kind = "master" if hls.looks_like_master(text) else "media"
                        print(f"Captured {kind} playlist: {redact_url(response.url)}")

                    except Exception:
                        pass

                context.on("response", on_response)

                page = context.pages[0] if context.pages else context.new_page()

                print("Opening course...")
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                print("\nIf the site asks you to log in, complete login manually in Opera GX.")
                print("Return to this terminal and press ENTER when you're logged in.")
                input()

                print("Looking for recordings tab...")
                if self._click_recordings_tab(page):
                    print("Recordings tab opened.")

                print(
                    f"\nIn Opera GX, apply the course filter and open the recording "
                    f"(waiting up to {self.capture_timeout} seconds).\n"
                )

                deadline = time.monotonic() + self.capture_timeout
                seen_master_at = None

                while time.monotonic() < deadline:
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break

                    if any(hls.looks_like_master(c.text) for c in captures):
                        seen_master_at = seen_master_at or time.monotonic()
                        if time.monotonic() - seen_master_at >= self.settle:
                            break

                try:
                    title = page.title()
                except Exception:
                    title = ""

            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if not captures:
            raise StreamDetectionError(
                "No HLS playlist was detected. Log in if needed, then open a recording so the player starts."
            )

        masters = [c for c in captures if hls.looks_like_master(c.text)]
        newest = (masters or captures)[-1]

        return {
            "type": "hls",
            "captures": captures,
            "headers": newest.headers,
            "title": title,
        }
