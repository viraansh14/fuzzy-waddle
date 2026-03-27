"""Playwright browser session management."""
from __future__ import annotations

import asyncio
import logging
import random

from job_applier.config import BrowserConfig, BehaviorConfig

logger = logging.getLogger("job_applier.browser")


class BrowserSession:
    def __init__(self, browser_cfg: BrowserConfig, behavior_cfg: BehaviorConfig):
        self._browser_cfg = browser_cfg
        self._behavior_cfg = behavior_cfg
        self._playwright = None
        self._browser = None
        self._context = None

    async def __aenter__(self) -> "BrowserSession":
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._browser_cfg.headless,
            slow_mo=self._browser_cfg.slow_mo_ms,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={
                "width": self._browser_cfg.viewport_width,
                "height": self._browser_cfg.viewport_height,
            },
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        # Mask automation markers
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    async def __aexit__(self, *_):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self):
        """Open a new page that blocks image/font requests to speed up navigation."""
        from playwright.async_api import Page, Route

        page = await self._context.new_page()

        async def block_resources(route: Route):
            if route.request.resource_type in ("image", "font", "media"):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_resources)
        return page

    async def add_cookies(self, cookies: list[dict]) -> None:
        await self._context.add_cookies(cookies)

    async def get_cookies(self) -> list[dict]:
        return await self._context.cookies()

    async def human_delay(self) -> None:
        """Sleep a random duration to mimic human timing between applications."""
        lo = self._behavior_cfg.min_delay_between_apps_s
        hi = self._behavior_cfg.max_delay_between_apps_s
        delay = random.uniform(lo, hi)
        logger.debug("Waiting %.1fs before next action", delay)
        await asyncio.sleep(delay)

    async def short_delay(self, min_ms: int = 500, max_ms: int = 1500) -> None:
        """Short random delay between form interactions."""
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))
