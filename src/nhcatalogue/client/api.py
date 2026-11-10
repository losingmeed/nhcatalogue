"""The nhentai client — the ONLY component that touches the network.

Request strategy chain (plan §1), applied to every outbound request:

1. mirror/domain rotation (health check + demote, user-editable order)
2. browser TLS impersonation (curl_cffi ``impersonate="chrome"``; httpx fallback)
3. manual ``cf_clearance`` cookie + matching User-Agent injection
4. optional FlareSolverr solve for fresh clearance cookies
5. optional SOCKS/HTTP proxy for every host

On a detected Cloudflare challenge we escalate: next domain → … → FlareSolverr
→ BlockedError("blocked → what to do") which the UI renders as a banner.

API endpoints (nhentai public JSON API):

* ``GET /api/galleries/search?query=&page=&sort=``
* ``GET /api/galleries/{id}``
* ``GET /api/galleries/all?page=``
* ``GET /api/galleries/tagged/{tag_id}``
* images: ``https://i{n}.nhentai.net/galleries/{media_id}/{page}.{ext}``
  thumbs/covers: ``https://t{n}.nhentai.net/galleries/{media_id}/cover.{ext}``
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping
from typing import Any

from ..config import FALLBACK_USER_AGENT, IMAGE_HOSTS, THUMB_HOSTS, Settings
from .challenge import looks_like_challenge
from .domains import DomainRotator
from .errors import BlockedError, NotFoundError, RateLimitedError
from .models import Gallery, SearchResult
from .ratelimit import RateLimiter
from .transport import FlareSolverrSession, Transport, TransportResponse, build_transport

log = logging.getLogger("nhcatalogue.client")

SORTS = ("date", "popular", "popular-month", "popular-week", "popular-today")


class NhentaiClient:
    """Async client. Pass ``transport=`` for tests; otherwise the best real one."""

    def __init__(self, settings: Settings, transport: Transport | None = None):
        self.settings = settings
        self.transport = transport or build_transport(settings)
        self.rotator = DomainRotator(settings.domains)
        self.limiter = RateLimiter(
            settings.rate_limit_rps,
            settings.rate_limit_jitter,
            settings.host_concurrency,
        )
        # runtime-acquired clearance (from FlareSolverr) — kept separate from the
        # user-pasted one in settings so we don't overwrite it silently.
        self._runtime_cookies: dict[str, str] = {}
        self._runtime_ua: str | None = None
        self._flaresolverr: FlareSolverrSession | None = None
        self.last_blocked: BlockedError | None = None

    # ------------------------------------------------------------------ helpers

    def refresh_from_settings(self) -> None:
        """Re-read settings that may have changed at runtime (domains, limits, proxy)."""
        self.rotator.set_domains(self.settings.domains)
        self.limiter.update(
            self.settings.rate_limit_rps,
            self.settings.rate_limit_jitter,
            self.settings.host_concurrency,
        )

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": FALLBACK_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://{self.settings.domains[0]}/" if self.settings.domains else "",
        }
        # strategy 3: manual cf_clearance binds to the UA it was issued for
        if self.settings.cf_clearance and self.settings.cf_user_agent:
            headers["User-Agent"] = self.settings.cf_user_agent
        elif self._runtime_ua:
            headers["User-Agent"] = self._runtime_ua
        return headers

    def _cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if self.settings.cf_clearance:
            cookies["cf_clearance"] = self.settings.cf_clearance
        cookies.update(self._runtime_cookies)
        return cookies

    @staticmethod
    def _host(url: str) -> str:
        try:
            return url.split("/", 3)[2]
        except IndexError:  # pragma: no cover - malformed url
            return url

    # ------------------------------------------------------- strategy chain

    async def _send(self, url: str) -> TransportResponse:
        """Send a single request with rate limiting (no retries)."""
        host = self._host(url)
        await self.limiter.acquire(host)
        try:
            return await self.transport.request(
                "GET", url, headers=self._base_headers(), cookies=self._cookies()
            )
        finally:
            self.limiter.release(host)

    async def _flaresolverr_solve(self, url: str) -> bool:
        """Strategy 4: ask FlareSolverr for fresh clearance cookies."""
        if not self.settings.flaresolverr_url:
            return False
        if self._flaresolverr is None:
            self._flaresolverr = FlareSolverrSession(self.settings.flaresolverr_url)
        try:
            result = await self._flaresolverr.solve(url, self.transport)
        except Exception as e:
            log.warning("FlareSolverr failed: %s", e)
            self.last_blocked = BlockedError(f"FlareSolverr error: {e}")
            return False
        self._runtime_cookies = {
            k: v for k, v in result.get("cookies", {}).items() if k == "cf_clearance"
        }
        self._runtime_ua = result.get("user_agent") or None
        if self.settings.cf_clearance:
            # runtime clearance wins for future requests
            self.settings.cf_clearance = self._runtime_cookies.get("cf_clearance", "")
            self.settings.cf_user_agent = self._runtime_ua or self.settings.cf_user_agent
        return bool(self._runtime_cookies)

    async def _request_json(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET a JSON API path through the full strategy chain.

        Chain: for each domain in rotation order → request → on challenge/block,
        escalate to the next domain; once all domains are exhausted, try
        FlareSolverr (if configured) and retry once with fresh cookies.
        Network errors retry with exponential backoff per domain.
        """
        qs = ""
        if params:
            from urllib.parse import urlencode

            qs = "?" + urlencode({k: v for k, v in params.items() if v is not None})

        strategies_tried: list[str] = []
        last_exc: Exception | None = None

        for attempt, round_ in enumerate(("first", "flaresolverr")):
            for domain in self.rotator.order():
                url = f"https://{domain}{path}{qs}"
                strategies_tried.append(f"GET {url}")
                backoff = 0.5
                for _retry in range(max(1, self.settings.max_retries)):
                    try:
                        resp = await self._send(url)
                    except (TimeoutError, OSError) as e:
                        # network/timeout: backoff then retry same domain
                        last_exc = e
                        self.rotator.report_failure(domain, f"network: {e!r}")
                        strategies_tried.append(f"retry {domain} after {e.__class__.__name__}")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 10.0)
                        continue

                    if resp.status == 404:
                        self.rotator.report_success(domain)
                        raise NotFoundError(path)
                    if resp.status == 429:
                        self.rotator.report_success(domain)
                        await asyncio.sleep(5.0 + random.uniform(0, 3))
                        continue

                    if looks_like_challenge(resp.status, resp.headers, resp.content):
                        last_exc = BlockedError(f"{domain}: challenge/block page")
                        self.rotator.report_failure(domain, "cf-challenge")
                        strategies_tried.append(f"{domain}: CF challenge → rotate domain")
                        break  # rotate to next domain immediately

                    if resp.status >= 500:
                        last_exc = BlockedError(f"{domain}: HTTP {resp.status}")
                        self.rotator.report_failure(domain, f"http {resp.status}")
                        strategies_tried.append(f"{domain}: HTTP {resp.status} → rotate")
                        break

                    # success
                    self.rotator.report_success(domain)
                    try:
                        return resp.json()
                    except ValueError as e:
                        # mirror quirk: non-JSON body — treat as domain failure
                        last_exc = e
                        self.rotator.report_failure(domain, "non-json body")
                        break

            if round_ == "first" and self.settings.flaresolverr_url and attempt == 0:
                target = f"https://{self.rotator.order()[0]}{path}{qs}"
                strategies_tried.append(f"FlareSolverr solve {target}")
                solved = await self._flaresolverr_solve(target)
                if solved:
                    strategies_tried.append("FlareSolverr: fresh cf_clearance acquired")
                    continue  # second round with the new cookies
                break

        msg = (
            "Blocked: every domain in the rotation list failed. "
            "Next steps: edit Settings → Domains (add/enable mirrors), paste a fresh "
            "cf_clearance + its User-Agent, set a FlareSolverr endpoint, or configure "
            "a SOCKS/HTTP proxy (VPN)."
        )
        err = BlockedError(msg, strategies_tried)
        self.last_blocked = err
        if isinstance(last_exc, RateLimitedError):  # pragma: no cover
            raise last_exc
        raise err

    # ------------------------------------------------------------ image URLs

    def page_url(self, gallery: Gallery, page: int, host: int = 0) -> str:
        """Image URL builder with i/i2/i5/i7 mirrors (plan §1)."""
        hosts = list(IMAGE_HOSTS) + list(THUMB_HOSTS)
        h = hosts[host % len(hosts)] if host else IMAGE_HOSTS[0]
        p = gallery.pages[page - 1]
        return f"https://{h}/galleries/{gallery.media_id}/{page}.{p.ext}"

    def cover_url(self, gallery: Gallery, host: int = 0) -> str:
        hosts = THUMB_HOSTS
        h = hosts[host % len(hosts)]
        return f"https://{h}/galleries/{gallery.media_id}/cover.{gallery.cover_ext}"

    def thumb_url(self, gallery: Gallery, host: int = 0) -> str:
        hosts = THUMB_HOSTS
        h = hosts[host % len(hosts)]
        return f"https://{h}/galleries/{gallery.media_id}/thumb.{gallery.thumb_ext}"

    async def fetch_image(self, url: str) -> bytes:
        """Fetch raw image bytes through the same chain (used for covers/pages).

        Host rotation on CDN errors mirrors the API strategy: i → i2 → i5 → i7.
        """
        parsed_host = url.split("/")[2]
        host_family = [h for h in IMAGE_HOSTS if h.split(".")[0] == parsed_host.split(".")[0]]
        candidates = [parsed_host] if not host_family else [parsed_host] + [
            h for h in IMAGE_HOSTS if h != parsed_host
        ]
        last: Exception | None = None
        for host in candidates:
            alt_url = url.replace(parsed_host, host, 1)
            try:
                resp = await self._send(alt_url)
            except (TimeoutError, OSError) as e:
                last = e
                continue
            if resp.status == 200 and resp.content:
                return resp.content
            if looks_like_challenge(resp.status, resp.headers, resp.content):
                last = BlockedError(f"{host}: challenge on image CDN")
                continue
            last = BlockedError(f"{host}: HTTP {resp.status} for image")
        raise BlockedError(f"could not fetch image {url}: {last}", [url])

    # ------------------------------------------------------------- API methods

    async def search(self, query: str, page: int = 1, sort: str = "") -> SearchResult:
        """Full nhentai query syntax: tag:, artist:, parody:, character:, group:,
        language:, category:, -tag exclusions, quoted phrases."""
        if sort and sort not in SORTS:
            sort = ""
        raw = await self._request_json(
            "/api/galleries/search", {"query": query, "page": page, "sort": sort}
        )
        return SearchResult.from_api(raw, page)

    async def gallery(self, gallery_id: int) -> Gallery:
        raw = await self._request_json(f"/api/galleries/{int(gallery_id)}")
        return Gallery.from_api(raw)

    async def all_galleries(self, page: int = 1) -> SearchResult:
        raw = await self._request_json("/api/galleries/all", {"page": page})
        return SearchResult.from_api(raw, page)

    async def tagged(self, tag_id: int, page: int = 1, sort: str = "") -> SearchResult:
        if sort and sort not in SORTS:
            sort = ""
        raw = await self._request_json(
            f"/api/galleries/tagged/{int(tag_id)}", {"page": page, "sort": sort}
        )
        return SearchResult.from_api(raw, page)

    async def test_connection(self) -> dict:
        """Settings → 'Test connection' button. Returns a status dict."""
        try:
            raw = await self._request_json("/api/galleries/all", {"page": 1})
            n = len(raw.get("result", [])) if isinstance(raw, dict) else 0
            return {
                "ok": True,
                "message": f"Connected via {self.rotator.order()[0]} ({n} results).",
                "transport": self.transport.name,
                "domain": self.rotator.order()[0],
            }
        except BlockedError as e:
            return {
                "ok": False,
                "message": str(e),
                "transport": self.transport.name,
                "domain": self.rotator.order()[0] if self.rotator.domains else None,
                "strategies": e.strategies,
            }
        except Exception as e:  # pragma: no cover - defensive
            return {"ok": False, "message": f"{e.__class__.__name__}: {e}", "transport": "error"}

    async def close(self) -> None:
        await self.transport.close()

    async def __aenter__(self) -> NhentaiClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
