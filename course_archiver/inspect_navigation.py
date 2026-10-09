import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OPERA = r"C:\Users\Souhaib Bokka\AppData\Local\Programs\Opera GX\opera.exe"
PROFILE = r"C:\Users\Souhaib Bokka\AppData\Roaming\Opera Software\Opera GX Stable"
START_URL = "https://eshra7ly.net/student/recordings"

async def choose(title, items):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)

    for i, item in enumerate(items, 1):
        print(f"[{i}] {item}")

    while True:
        try:
            n = int(input("\nChoose: ").strip())
            if 1 <= n <= len(items):
                return n - 1
        except ValueError:
            pass
        print("Invalid choice. Try again.")

async def main():
    if not Path(OPERA).exists():
        print("ERROR: Opera GX was not found:")
        print(OPERA)
        return

    print("Launching Opera GX...")
    print("Opening Eshra7ly recordings...")
    print()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            PROFILE,
            executable_path=OPERA,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled"
            ],
        )

        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        print(f"URL: {page.url}")

        # ------------------------------------------------------------
        # STEP 1: FIND COURSES
        # ------------------------------------------------------------

        course_select = page.locator("select#course")

        try:
            await course_select.wait_for(timeout=15000)
        except Exception:
            print()
            print("ERROR: Could not find the course selector.")
            print("Make sure you are logged into Eshra7ly in Opera GX.")
            await context.close()
            return

        options = await course_select.locator("option").all()

        courses = []
        values = []

        for option in options:
            text = (await option.inner_text()).strip()
            value = await option.get_attribute("value")

            if text and value:
                courses.append(text)
                values.append(value)

        if not courses:
            print("ERROR: No courses were found.")
            await context.close()
            return

        course_index = await choose("COURSES FOUND", courses)

        selected_course = courses[course_index]
        selected_value = values[course_index]

        print()
        print(f"Selected course: {selected_course}")

        # ------------------------------------------------------------
        # STEP 2: SELECT COURSE + APPLY FILTER
        # ------------------------------------------------------------

        await course_select.select_option(selected_value)

        apply_button = page.get_by_role("button", name="Apply Filters")

        try:
            await apply_button.click()
        except Exception:
            # fallback in case the site's button doesn't expose the role
            apply_button = page.locator("button").filter(
                has_text="Apply Filters"
            )
            await apply_button.first.click()

        print("Course filter applied.")

        await page.wait_for_timeout(2500)

        # ------------------------------------------------------------
        # STEP 3: FIND GROUPS
        # ------------------------------------------------------------

        group_links = page.locator(
            'a[href*="/student/recordings/group-recordings/"]'
        )

        count = await group_links.count()

        groups = []
        group_hrefs = []

        for i in range(count):
            link = group_links.nth(i)
            text = (await link.inner_text()).strip()
            href = await link.get_attribute("href")

            if text and href and href not in group_hrefs:
                groups.append(text)
                group_hrefs.append(href)

        if not groups:
            print()
            print("No recording groups were found.")
            print("The selected course may have no recordings.")
            await context.close()
            return

        group_index = await choose("GROUPS FOUND", groups)

        selected_group = groups[group_index]
        selected_group_href = group_hrefs[group_index]

        print()
        print(f"Selected group: {selected_group}")

        # ------------------------------------------------------------
        # STEP 4: OPEN GROUP
        # ------------------------------------------------------------

        if selected_group_href.startswith("/"):
            selected_group_href = "https://eshra7ly.net" + selected_group_href

        await page.goto(selected_group_href, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        print(f"Group page: {page.url}")

        # ------------------------------------------------------------
        # STEP 5: FIND RECORDINGS
        # ------------------------------------------------------------

        cards = page.locator(".yt-recording-card.unlocked")

        try:
            await cards.first.wait_for(timeout=15000)
        except Exception:
            print()
            print("ERROR: No unlocked recording cards were found.")
            await context.close()
            return

        card_count = await cards.count()

        recordings = []

        for i in range(card_count):
            card = cards.nth(i)

            title_locator = card.locator(".yt-title")

            if await title_locator.count():
                title = (await title_locator.first.inner_text()).strip()
            else:
                title = (await card.inner_text()).strip()

            title = " ".join(title.split())

            if title:
                recordings.append(title)

        if not recordings:
            print("ERROR: Recording cards were found but no titles were readable.")
            await context.close()
            return

        recording_index = await choose("RECORDINGS FOUND", recordings)

        selected_recording = recordings[recording_index]

        print()
        print(f"Selected recording: {selected_recording}")

        # ------------------------------------------------------------
        # STEP 6: CLICK PLAY ON THE SELECTED CARD
        # ------------------------------------------------------------

        selected_card = cards.nth(recording_index)

        play_button = selected_card.locator(
            "button.yt-btn.yt-btn-play.play_recording"
        )

        if not await play_button.count():
            play_button = selected_card.locator("button.play_recording")

        if not await play_button.count():
            print()
            print("ERROR: Play button was not found inside the selected recording.")
            await context.close()
            return

        print("Clicking Play...")

        await play_button.first.click()

        # Give the player a moment to initialize.
        await page.wait_for_timeout(5000)

        print()
        print("=" * 70)
        print("PLAY CLICKED SUCCESSFULLY")
        print("=" * 70)
        print()
        print(f"Course    : {selected_course}")
        print(f"Group     : {selected_group}")
        print(f"Recording : {selected_recording}")
        print(f"URL       : {page.url}")
        print()
        print("Navigation pipeline works.")
        print()
        print("The browser will stay open so you can verify the recording.")
        print("Press ENTER here when you're finished inspecting it.")

        input()

        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
