"""Politeness: per-host rate limit (min interval + jitter) and concurrency caps.

Defaults: ~1 request/second with jitter and a per-host concurrency cap of 3
(plan §1 politeness). A test can inject ``sleeper`` and ``clock`` so tests
never actually wait.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        rps: float = 1.0,
        jitter: float = 0.35,
        host_concurrency: int = 3,
        *,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        if rps <= 0:
            raise ValueError("rps must be > 0")
        self.interval = 1.0 / rps
        self.jitter = max(0.0, jitter)
        self.host_concurrency = max(1, host_concurrency)
        self._last: dict[str, float] = defaultdict(float)
        self._sems: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.host_concurrency)
        )
        self._global_lock = asyncio.Lock()
        self._sleep = sleeper or asyncio.sleep
        self._clock = clock or time.monotonic
        # track pending sleep so tests can inspect scheduled delays
        self.last_delay: float = 0.0

    def _jitter(self) -> float:
        if not self.jitter:
            return 0.0
        return random.uniform(0, self.interval * self.jitter)

    async def acquire(self, host: str) -> None:
        """Wait for our turn to talk to ``host`` and hold a concurrency slot.

        Jitter is drawn once per reservation (never re-added while waiting),
        so requests are spaced ≥ interval ± jitter and the wait always ends.
        """
        sem = self._sems[host]
        await sem.acquire()
        try:
            while True:
                now = self._clock()
                last = self._last.get(host, 0.0)
                wait = last + self.interval - now
                if wait <= 0:
                    # reserve this slot; jitter stretches the next reservation
                    self._last[host] = now + self._jitter()
                    self.last_delay = 0.0
                    return
                delay = wait + self._jitter()
                self.last_delay = delay
                await self._sleep(delay)
                # after sleeping, the loop re-checks and (now past `last +
                # interval`) returns — jitter was consumed by this sleep
        except BaseException:
            sem.release()
            raise

    def release(self, host: str) -> None:
        self._sems[host].release()

    def update(self, rps: float, jitter: float, host_concurrency: int) -> None:
        """Reconfigure from live settings (does not affect held slots)."""
        self.interval = 1.0 / max(rps, 0.01)
        self.jitter = max(0.0, jitter)
        self.host_concurrency = max(1, host_concurrency)
        self._sems.clear()
        self._sems = defaultdict(lambda: asyncio.Semaphore(self.host_concurrency))
