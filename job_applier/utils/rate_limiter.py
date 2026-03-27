import asyncio
import random
import time


class RateLimiter:
    """Simple per-platform rate limiter with random jitter."""

    # Minimum seconds between requests per platform
    _MIN_GAPS: dict[str, float] = {
        "linkedin": 8.0,
        "indeed": 5.0,
        "glassdoor": 10.0,
        "generic": 3.0,
    }

    def __init__(self, min_gap_override: float | None = None):
        self._last: dict[str, float] = {}
        self._override = min_gap_override

    async def wait(self, platform: str) -> None:
        min_gap = self._override or self._MIN_GAPS.get(platform, 5.0)
        # Add up to 50% jitter
        jitter = random.uniform(0, min_gap * 0.5)
        wait_for = min_gap + jitter

        last = self._last.get(platform, 0.0)
        elapsed = time.monotonic() - last
        remaining = wait_for - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

        self._last[platform] = time.monotonic()
