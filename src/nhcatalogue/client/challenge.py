"""Cloudflare challenge / block detection (plan §1).

A response is "challenged" when it carries a 403/503 status and looks like a
Cloudflare interstitial (``cf-mitigated: challenge`` header, or the classic
challenge / ray-id body markers). Mirrors sometimes return plain 403 HTML —
we treat that as blocked too, because retrying another domain is cheap.
"""

from __future__ import annotations

CHALLENGE_HEADERS = ("cf-mitigated",)
CHALLENGE_MARKERS = (
    "just a moment",
    "attention required! | cloudflare",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "ddos-guard",  # some mirrors sit behind ddos-guard instead
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
)

STATUS_BLOCK = (403, 503)


def looks_like_challenge(status: int, headers: dict[str, str] | None, body: bytes | str) -> bool:
    """Heuristic: is this response a CF/ddos-guard challenge page?"""
    if status not in STATUS_BLOCK:
        return False
    if headers:
        for h in CHALLENGE_HEADERS:
            for k, v in headers.items():
                if k.lower() == h and v.strip().lower() in ("challenge", "block"):
                    return True
    text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else (body or "")
    low = text.lower()
    return any(marker in low for marker in CHALLENGE_MARKERS)


def extract_clearance_cookie(body: str) -> tuple[str, str] | None:
    """Best-effort scrape of cf_clearance from an HTML challenge redirect body."""
    marker = "cf_clearance="
    idx = body.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = start
    while end < len(body) and body[end] not in ('"', "'", ";", "<", " "):
        end += 1
    value = body[start:end].strip()
    return ("cf_clearance", value) if value else None


def parse_flaresolverr_response(payload: dict) -> dict:
    """Validate a FlareSolverr response, returning its solution dict.

    Raises ValueError with a helpful message when the challenge wasn't solved.
    """
    status = payload.get("status")
    if status != "ok":
        raise ValueError(f"FlareSolverr status={status!r} message={payload.get('message')!r}")
    solution = payload.get("solution") or {}
    if not solution.get("url"):
        raise ValueError("FlareSolverr returned no solution URL")
    return solution
