"""Cookie-based authentication manager for job platforms."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from job_applier.browser.session import BrowserSession
from job_applier.utils.errors import AuthenticationError

logger = logging.getLogger("job_applier.auth")

# URL to check if a platform session is active
_LOGIN_CHECK_URLS: dict[str, str] = {
    "linkedin": "https://www.linkedin.com/feed/",
    "indeed": "https://www.indeed.com/",
    "glassdoor": "https://www.glassdoor.com/member/home/index.htm",
}

# CSS selector that is only visible when logged in
_LOGGED_IN_SELECTORS: dict[str, str] = {
    "linkedin": "[data-control-name='identity_profile_photo'], .global-nav__me",
    "indeed": "#indeed-logo, .gnav-LoggedInLinks",
    "glassdoor": ".header-user-dropdown, .userMenu",
}

# Platform login pages
_LOGIN_URLS: dict[str, str] = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "glassdoor": "https://www.glassdoor.com/profile/login_input.htm",
}


class AuthManager:
    def __init__(self, cookie_dir: str):
        self._cookie_dir = Path(cookie_dir)
        self._cookie_dir.mkdir(parents=True, exist_ok=True)

    def _cookie_path(self, platform: str) -> Path:
        return self._cookie_dir / f"{platform}.json"

    def save_cookies(self, platform: str, cookies: list[dict]) -> None:
        path = self._cookie_path(platform)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        os.chmod(path, 0o600)
        logger.info("Saved %d cookies for %s", len(cookies), platform)

    def load_cookies(self, platform: str) -> list[dict] | None:
        path = self._cookie_path(platform)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            cookies = json.load(f)
        logger.info("Loaded %d cookies for %s", len(cookies), platform)
        return cookies

    async def ensure_logged_in(
        self, platform: str, session: BrowserSession
    ) -> None:
        """Ensure the browser session is authenticated for `platform`.

        Tries saved cookies first. If that fails, opens a browser window and
        waits for the user to log in manually, then saves the resulting cookies.
        """
        cookies = self.load_cookies(platform)
        page = await session.new_page()

        if cookies:
            await session.add_cookies(cookies)
            if await self._check_logged_in(platform, page):
                logger.info("Session active for %s (cookies)", platform)
                await page.close()
                return
            logger.info("Saved cookies for %s expired, re-authenticating", platform)

        # Need manual login
        await self._prompt_manual_login(platform, page, session)
        await page.close()

    async def _check_logged_in(self, platform: str, page) -> bool:
        check_url = _LOGIN_CHECK_URLS.get(platform, "")
        selector = _LOGGED_IN_SELECTORS.get(platform, "")
        if not check_url or not selector:
            return False
        try:
            await page.goto(check_url, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_selector(selector, timeout=5000)
            return True
        except Exception:
            return False

    async def _prompt_manual_login(
        self, platform: str, page, session: BrowserSession
    ) -> None:
        login_url = _LOGIN_URLS.get(platform, "")
        if login_url:
            await page.goto(login_url, wait_until="domcontentloaded")

        print(
            f"\n[AUTH] Please log in to {platform.capitalize()} in the browser window."
            f"\nPress ENTER here once you are logged in and see your home feed..."
        )

        # Wait for user to press Enter without blocking the event loop
        await asyncio.get_event_loop().run_in_executor(None, input)

        # Verify login succeeded
        if not await self._check_logged_in(platform, page):
            raise AuthenticationError(
                f"Could not verify login for {platform}. "
                "Please ensure you are logged in and try again."
            )

        cookies = await session.get_cookies()
        self.save_cookies(platform, cookies)
        logger.info("Manual login for %s complete, cookies saved", platform)
