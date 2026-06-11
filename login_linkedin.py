"""
login_linkedin.py — One-time LinkedIn login to save session cookies.
Run this once before using the pipeline. The saved session is reused
automatically by agent_applier.py so you don't need to log in each time.

Usage:
    python3 login_linkedin.py
"""

from __future__ import annotations

from pathlib import Path

SESSION_FILE = Path("output/linkedin_session.json")


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("\n── LinkedIn Session Login ───────────────────────────")
    print("A browser window will open. Log into LinkedIn normally.")
    print("Once you are fully logged in and can see your feed,")
    print("come back here and press Enter to save your session.")
    print("─────────────────────────────────────────────────────\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        input("Press Enter once you are logged in and can see your LinkedIn feed... ")

        # Verify login succeeded
        if "feed" in page.url or "mynetwork" in page.url or "jobs" in page.url:
            context.storage_state(path=str(SESSION_FILE))
            print(f"\n✓ Session saved to {SESSION_FILE}")
            print("The pipeline will now use this session automatically.\n")
        else:
            # Try navigating to feed to confirm
            page.goto("https://www.linkedin.com/feed/")
            page.wait_for_timeout(2000)
            if "feed" in page.url:
                context.storage_state(path=str(SESSION_FILE))
                print(f"\n✓ Session saved to {SESSION_FILE}")
            else:
                print("\n✗ Does not appear to be logged in. Please try again.")

        browser.close()


if __name__ == "__main__":
    main()
