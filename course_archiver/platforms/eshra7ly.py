# -*- coding: utf-8 -*-
"""Eshra7ly navigation + authorized HLS capture using project-local Playwright Chromium."""
import getpass
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

PROJECT_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
    BROWSER_DIR = Path(sys._MEIPASS) / "browser"
else:
    BROWSER_DIR = PROJECT_DIR / "browser"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_DIR)

from playwright.sync_api import sync_playwright

import config
from core import hls_parser as hls
from core.errors import CancelledError, StreamDetectionError
from core.models import Capture, safe_headers
from core.redact import redact_url
from .base import baseplatform

START_URL = "https://eshra7ly.net/student/recordings"
HLS_TYPES = ("mpegurl",)


def _is_hls_response(url: str, content_type: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return path.endswith(".m3u8") or any(t in (content_type or "").lower() for t in HLS_TYPES)


def _choose(title: str, items: list[str]) -> int:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    for i, item in enumerate(items, 1):
        print(f"[{i}] {item}")
    while True:
        try:
            value = int(input("\nChoose: ").strip())
            if 1 <= value <= len(items):
                return value - 1
        except ValueError:
            pass
        print("Invalid choice. Try again.")



def _recording_date(card):
    """Return a parsed recording date, checking date-specific DOM fields first."""
    candidates = []
    selectors = [
        "time[datetime]", "[data-date]", "[datetime]", ".yt-date",
        ".recording-date", ".yt-recording-date", ".date",
        "[class*='date' i]",
    ]
    for selector in selectors:
        try:
            nodes = card.locator(selector)
            for i in range(min(nodes.count(), 8)):
                node = nodes.nth(i)
                for attr in ("datetime", "data-date", "title", "aria-label"):
                    value = node.get_attribute(attr)
                    if value:
                        candidates.append(value.strip())
                text = " ".join((node.inner_text() or "").split())
                if text:
                    candidates.append(text)
        except Exception:
            continue

    # Some layouts show the date as plain text without a dedicated date class.
    try:
        candidates.append(" ".join((card.inner_text() or "").split()))
    except Exception:
        pass

    formats = (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%m/%d/%Y", "%m-%d-%Y",
        "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
        "%d %b, %Y", "%d %B, %Y",
    )
    patterns = (
        r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",
        r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b",
    )
    for candidate in candidates:
        for pattern in patterns:
            for match in re.findall(pattern, candidate, flags=re.IGNORECASE):
                value = match.strip()
                for fmt in formats:
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
    return None


def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector)
        for i in range(locator.count()):
            item = locator.nth(i)
            try:
                if item.is_visible() and item.is_enabled():
                    return item
            except Exception:
                continue
    return None


class eshra7lyplatform(baseplatform):
    def __init__(self, browser_exe=None, profile_dir=None, capture_timeout=None, settle=None,
                 on_status=None, choose_callback=None, login_callback=None, cancel_event=None,
                 headless=True):
        # Chromium is installed by Playwright into the project-local "browser" directory.
        self.profile_dir = str(profile_dir or config.BROWSER_PROFILE_DIR)
        self.capture_timeout = capture_timeout or config.CAPTURE_TIMEOUT
        self.settle = config.CAPTURE_SETTLE if settle is None else settle
        self.on_status = on_status
        self.choose_callback = choose_callback
        self.login_callback = login_callback
        self.cancel_event = cancel_event
        # Always use headless Chromium: no browser window or taskbar button.\n        self.headless = True

    def _status(self, message):
        if self.on_status:
            try:
                self.on_status(str(message))
            except Exception:
                pass

    def _choose(self, title, items):
        if self.choose_callback:
            return self.choose_callback(title, items)
        return _choose(title, items)

    def _login_if_needed(self, page) -> None:
        course_select = page.locator("select#course")
        try:
            course_select.wait_for(state="visible", timeout=6000)
            return
        except Exception:
            pass

        password = _first_visible(page, ["input[type='password']"])
        if password is None:
            # Give a slow authenticated page a little more time before assuming login is needed.
            try:
                course_select.wait_for(state="visible", timeout=7000)
                return
            except Exception:
                if self.headless:
                    raise StreamDetectionError(
                        "The invisible browser could not reach the recordings page. Check that this account "
                        "is already signed in and that no one-time code or human verification is required. "
                        "The browser is intentionally kept invisible."
                    )
                raise StreamDetectionError(
                    "The course selector did not appear. The site may have changed its page layout, "
                    "or this account may not have access to the recordings page."
                )

        self._status("Sign-in is required.")
        if self.login_callback:
            username, password_value = self.login_callback()
        else:
            print("\nEshra7ly login is required. Credentials are entered locally and are not saved.")
            username = input("Eshra7ly email / username: ").strip()
            password_value = getpass.getpass("Eshra7ly password (hidden): ")
        if not username or not password_value:
            raise CancelledError("Login cancelled.")

        user_field = _first_visible(page, [
            "input[type='email']",
            "input[name*='email' i]",
            "input[name*='user' i]",
            "input[name*='login' i]",
            "input[autocomplete='username']",
            "input[type='text']",
        ])
        password = _first_visible(page, ["input[type='password']"])
        if user_field is None or password is None:
            raise StreamDetectionError(
                "Could not identify the login fields on the current page. No credentials were submitted."
            )

        user_field.fill(username)
        password.fill(password_value)
        # Drop references to the password as soon as the form has been filled.
        password_value = None

        submit = _first_visible(page, [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Log in')",
            "button:has-text('تسجيل الدخول')",
        ])
        if submit is None:
            password.press("Enter")
        else:
            submit.click()

        try:
            course_select.wait_for(state="visible", timeout=15000)
            return
        except Exception:
            if self.login_callback and self.headless:
                raise StreamDetectionError(
                    "Invisible-browser sign-in did not finish automatically. Check that your saved browser "
                    "session is still valid. If Eshra7ly requires a one-time code or human verification, "
                    "the session may need to be renewed before using invisible mode."
                )
            if self.login_callback:
                self._status(
                    "Complete any one-time code or human verification in Chromium. "
                    "Waiting for the recordings page…"
                )
                try:
                    course_select.wait_for(state="visible", timeout=120000)
                    return
                except Exception:
                    raise StreamDetectionError(
                        "Login did not reach the recordings page. Check the sign-in details or complete any site "
                        "verification in the opened browser, then try again."
                    )
            print("\nIf Eshra7ly asks for a one-time code or a human verification, complete that step in the opened browser.")
            print("When the recordings page is visible, return here and press ENTER.")
            input()
            try:
                course_select.wait_for(state="visible", timeout=20000)
                return
            except Exception:
                raise StreamDetectionError(
                    "Login did not reach the recordings page. Check the credentials or complete any site verification, "
                    "then run the program again."
                )

    def _select_recording(self, page) -> tuple[str, str, str]:
        course_select = page.locator("select#course")
        options = course_select.locator("option")
        courses = []
        values = []
        for i in range(options.count()):
            option = options.nth(i)
            label = (option.inner_text() or "").strip()
            value = option.get_attribute("value")
            if label and value and value.strip():
                courses.append(label)
                values.append(value)

        if not courses:
            raise StreamDetectionError("No courses are available for this account on the recordings page.")

        self._status(f"Found {len(courses)} courses.")
        course_index = self._choose("COURSES FOUND", courses)
        selected_course = courses[course_index]
        course_select.select_option(values[course_index])

        apply_button = _first_visible(page, [
            "button:has-text('Apply Filters')",
            "input[type='submit'][value*='Apply Filters' i]",
            "button:has-text('تطبيق')",
        ])
        if apply_button is None:
            raise StreamDetectionError("Selected a course, but could not find the Apply Filters button.")
        apply_button.click()
        page.wait_for_timeout(1800)

        group_links = page.locator('a[href*="/student/recordings/group-recordings/"]')
        groups = []
        group_hrefs = []
        for i in range(group_links.count()):
            link = group_links.nth(i)
            label = " ".join((link.inner_text() or "").split())
            href = link.get_attribute("href")
            if label and href and href not in group_hrefs:
                groups.append(label)
                group_hrefs.append(urljoin(START_URL, href))
        if not groups:
            raise StreamDetectionError(
                "No recording groups were found for this course. It may have no recordings or the site layout may have changed."
            )

        self._status(f"Found {len(groups)} recording groups.")
        group_index = self._choose("RECORDING GROUPS FOUND", groups)
        selected_group = groups[group_index]
        page.goto(group_hrefs[group_index], wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        cards = page.locator(".yt-recording-card.unlocked")
        try:
            cards.first.wait_for(state="visible", timeout=12000)
        except Exception:
            raise StreamDetectionError("No available recording cards were found in the selected group.")

        dated_recordings = []
        for i in range(cards.count()):
            card = cards.nth(i)
            title = card.locator(".yt-title")
            label = title.first.inner_text() if title.count() else card.inner_text()
            label = " ".join((label or "").split())
            date_value = _recording_date(card)
            dated_recordings.append((date_value, label or f"Recording {i + 1}", i))

        # Sort known dates oldest-to-newest. Undated items stay at the bottom,
        # retaining their original page order.
        dated_recordings.sort(
            key=lambda item: (item[0] is None, item[0] or datetime.max, item[2])
        )
        recordings = [
            f"{date_value.strftime('%Y-%m-%d') if date_value else 'Date unknown'} | {label}"
            for date_value, label, _ in dated_recordings
        ]

        self._status(f"Found {len(recordings)} unlocked recordings; dates sorted oldest to newest.")
        recording_index = self._choose("RECORDINGS FOUND (OLDEST TO NEWEST)", recordings)
        date_value, selected_recording, original_index = dated_recordings[recording_index]
        card = cards.nth(original_index)
        play = card.locator("button.yt-btn.yt-btn-play.play_recording")
        if not play.count():
            play = card.locator("button.play_recording")
        if not play.count():
            raise StreamDetectionError("The selected recording has no available Play button.")
        self._status(f"Selected: {selected_course} / {selected_group} / {selected_recording}")
        self._status("Opening the selected recording…")
        play.first.click()
        return selected_course, selected_group, selected_recording

    def extract_media_info(self, url: str = START_URL) -> dict:
        captures = []
        seq = [0]
        title = ""
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=True,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()

                def on_response(response):
                    try:
                        ctype = response.headers.get("content-type", "")
                        if response.status != 200 or not _is_hls_response(response.url, ctype):
                            return
                        body = response.text()
                        if not body.lstrip("\ufeff").lstrip().startswith("#EXTM3U"):
                            return
                        try:
                            raw = response.request.all_headers()
                        except Exception:
                            raw = response.request.headers
                        seq[0] += 1
                        captures.append(Capture(seq[0], response.url, body, safe_headers(raw)))
                        kind = "master" if hls.looks_like_master(body) else "media"
                        print(f"Captured {kind} playlist: {redact_url(response.url)}")
                    except Exception:
                        pass

                context.on("response", on_response)
                self._status("Opening Eshra7ly recordings in Chromium…")
                page.goto(url or START_URL, wait_until="domcontentloaded")
                self._login_if_needed(page)
                course, group, recording = self._select_recording(page)

                deadline = time.monotonic() + self.capture_timeout
                last_master_signature = None
                last_master_change_at = None
                while time.monotonic() < deadline:
                    if self.cancel_event is not None and self.cancel_event.is_set():
                        raise CancelledError("cancelled")
                    page.wait_for_timeout(400)
                    signature = tuple(
                        (c.url, c.text) for c in captures
                        if hls.looks_like_master(c.text)
                    )
                    if signature and signature != last_master_signature:
                        last_master_signature = signature
                        last_master_change_at = time.monotonic()
                    if last_master_change_at is not None and time.monotonic() - last_master_change_at >= self.settle:
                        break

                try:
                    title = page.title()
                except Exception:
                    title = ""
                title = recording or title
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if not captures:
            raise StreamDetectionError(
                "No HLS playlist was detected after Play was clicked. The recording may not be available, "
                "or the site may have changed its player."
            )
        masters = [c for c in captures if hls.looks_like_master(c.text)]
        newest = (masters or captures)[-1]
        return {
            "type": "hls",
            "captures": captures,
            "headers": newest.headers,
            "title": title,
        }
