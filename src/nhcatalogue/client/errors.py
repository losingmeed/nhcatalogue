"""Client-side exceptions, including the blocked/challenge states."""

from __future__ import annotations


class NhentaiError(Exception):
    """Base class for nhcatalogue client errors."""


class BlockedError(NhentaiError):
    """Every strategy in the chain failed — user action required.

    ``strategies`` records what was attempted so the UI can show exactly
    "blocked → what to do" guidance.
    """

    def __init__(self, message: str, strategies: list[str] | None = None):
        super().__init__(message)
        self.strategies = strategies or []


class NotFoundError(NhentaiError):
    """Gallery / tag not found upstream (non-retryable)."""


class RateLimitedError(NhentaiError):
    """429 from the remote side."""


class FlareSolverrError(NhentaiError):
    """FlareSolverr endpoint failed or returned an unsolved challenge."""
