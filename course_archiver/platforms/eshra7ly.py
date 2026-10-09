# -*- coding: utf-8 -*-
"""Eshra7ly navigation + authorized HLS capture using project-local Playwright Chromium."""
import getpass
import os
import time
from pathlib import Path
from urllib.parse import urljoin

PROJECT_DIR = Path(__file__).resolve().parent.parent
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(PROJECT_DIR / "browser")

from playwright.sync_api import sync_playwright

import config
from core import hls_parser as hls
from core.errors import StreamDetectionError
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
    def __init__(self, browser_exe=None, profile_dir=None, capture_timeout=None, settle=None):
        # Chromium is installed by Playwright into the project-local "browser" directory.
        self.profile_dir = str(profile_dir or config.BROWSER_PROFILE_DIR)
        self.capture_timeout = capture_timeout or config.CAPTURE_TIMEOUT
        self.settle = config.CAPTURE_SETTLE if settle is None else settle

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
                raise StreamDetectionError(
                    "The course selector did not appear. The site may have changed its page layout, "
                    "or this account may not have access to the recordings page."
                )

        print("\nEshra7ly login is required. Credentials are entered locally and are not saved.")
        username = input("Eshra7ly email / username: ").strip()
        password_value = getpass.getpass("Eshra7ly password (hidden): ")
        if not username or not password_value:
            raise StreamDetectionError("Login cancelled: username and password are required.")

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

        course_index = _choose("COURSES FOUND", courses)
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

        group_index = _choose("RECORDING GROUPS FOUND", groups)
        selected_group = groups[group_index]
        page.goto(group_hrefs[group_index], wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        cards = page.locator(".yt-recording-card.unlocked")
        try:
            cards.first.wait_for(state="visible", timeout=12000)
        except Exception:
            raise StreamDetectionError("No available recording cards were found in the selected group.")

        recordings = []
        for i in range(cards.count()):
            card = cards.nth(i)
            title = card.locator(".yt-title")
            label = title.first.inner_text() if title.count() else card.inner_text()
            label = " ".join((label or "").split())
            recordings.append(label or f"Recording {i + 1}")

        recording_index = _choose("RECORDINGS FOUND", recordings)
        selected_recording = recordings[recording_index]
        card = cards.nth(recording_index)
        play = card.locator("button.yt-btn.yt-btn-play.play_recording")
        if not play.count():
            play = card.locator("button.play_recording")
        if not play.count():
            raise StreamDetectionError("The selected recording has no available Play button.")
        print(f"\nSelected: {selected_course} / {selected_group} / {selected_recording}")
        print("Opening the selected recording...")
        play.first.click()
        return selected_course, selected_group, selected_recording

    def extract_media_info(self, url: str = START_URL) -> dict:
        captures = []
        seq = [0]
        title = ""
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=False,
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
                print("Opening Eshra7ly recordings...")
                page.goto(url or START_URL, wait_until="domcontentloaded")
                self._login_if_needed(page)
                course, group, recording = self._select_recording(page)

                deadline = time.monotonic() + self.capture_timeout
                seen_master_at = None
                while time.monotonic() < deadline:
                    page.wait_for_timeout(400)
                    if any(hls.looks_like_master(c.text) for c in captures):
                        seen_master_at = seen_master_at or time.monotonic()
                        if time.monotonic() - seen_master_at >= self.settle:
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
