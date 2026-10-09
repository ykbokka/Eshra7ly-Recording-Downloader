import time

from playwright.sync_api import sync_playwright

import config
from core import hls_parser as hls
from core.errors import StreamDetectionError
from core.models import Capture, safe_headers
from core.redact import redact_url
from .base import baseplatform

RECORDING_TEXTS = ["recordings", "recording", "تسجيلات", "التسجيلات", "تسجيل"]
HLS_TYPES = ("mpegurl",)  # application/vnd.apple.mpegurl, application/x-mpegurl, audio/mpegurl ...


def _is_hls_response(url: str, content_type: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return path.endswith(".m3u8") or any(t in (content_type or "").lower() for t in HLS_TYPES)


class eshra7lyplatform(baseplatform):
    def __init__(self, browser_exe=None, profile_dir=None, capture_timeout=None, settle=None):
        self.browser_exe = browser_exe or config.BROWSER_EXECUTABLE
        self.profile_dir = profile_dir or config.BROWSER_PROFILE_DIR
        self.capture_timeout = capture_timeout or config.CAPTURE_TIMEOUT
        self.settle = config.CAPTURE_SETTLE if settle is None else settle

    # -- same tab-finding logic that worked in the Stage 1 test -----------------------------
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
        print("could not automatically find recordings tab; open it yourself in the browser window")
        return False

    def extract_media_info(self, url: str) -> dict:
        captures = []
        seq = [0]

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self.profile_dir, headless=False, executable_path=self.browser_exe
            )
            try:
                # Passive listener: observes responses the page already received; modifies nothing.
                def on_response(response):
                    try:
                        ctype = response.headers.get("content-type", "")
                        if response.status != 200 or not _is_hls_response(response.url, ctype):
                            return
                        text = response.text()
                        if not text.lstrip("﻿").lstrip().startswith("#EXTM3U"):
                            return
                        try:
                            raw = response.request.all_headers()
                        except Exception:
                            raw = response.request.headers
                        seq[0] += 1
                        captures.append(Capture(seq[0], response.url, text, safe_headers(raw)))  # cookies never kept
                        kind = "master" if hls.looks_like_master(text) else "media"
                        print(f"captured {kind} playlist: {redact_url(response.url)}")
                    except Exception:
                        pass

                context.on("response", on_response)

                page = context.pages[0] if context.pages else context.new_page()
                print("opening course...")
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                print("looking for recordings tab...")
                if self._click_recordings_tab(page):
                    print("recordings tab opened")
                print(f"\n>>> In the browser: apply the course filter and OPEN the recording you want "
                      f"(waiting up to {self.capture_timeout}s) <<<\n")

                deadline = time.monotonic() + self.capture_timeout
                seen_master_at = None
                while time.monotonic() < deadline:
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break  # browser window closed
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
                "No HLS playlist was detected. Open a recording in the browser window so the player starts."
            )
        masters = [c for c in captures if hls.looks_like_master(c.text)]
        newest = (masters or captures)[-1]
        return {"type": "hls", "captures": captures, "headers": newest.headers, "title": title}
