"""HTTP transports with browser TLS impersonation.

Transport abstraction so the rest of the client is testable with a mock:

* :class:`CurlCffiTransport` — preferred. Real browser TLS/JA3+HTTP2
  fingerprints via ``curl_cffi`` ``impersonate="chrome"``. Defeats most
  Cloudflare bot-rule challenges with zero user action (plan §1.2).
* :class:`HttpxTransport` — fallback when curl_cffi isn't installed.

Both honour: proxy (http/socks5), per-request Cookie/User-Agent injection
(for manual cf_clearance), and time out politely. FlareSolverr lives here
too because it is a network access strategy (plan §1.4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..config import FALLBACK_USER_AGENT, Settings
from .challenge import parse_flaresolverr_response

try:  # graceful degradation: curl_cffi → httpx (see README)
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

    HAS_CURL_CFFI = True
except Exception:  # pragma: no cover - depends on env
    CurlAsyncSession = None  # type: ignore[assignment]
    HAS_CURL_CFFI = False


@dataclass
class TransportResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "ignore")

    def json(self) -> dict:
        return json.loads(self.text)


class Transport:
    """Minimal async request interface used by the client layer."""

    name = "base"

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> TransportResponse:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - interface
        pass


class CurlCffiTransport(Transport):
    name = "curl_cffi"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._session: CurlAsyncSession | None = None  # type: ignore[valid-type]

    def _ensure_session(self):
        if self._session is None:
            self._session = CurlAsyncSession(  # type: ignore[misc]
                proxy=self.settings.proxy or None,
                timeout=self.settings.request_timeout,
                impersonate="chrome",
                # keep connections but let us set headers per request
            )
        return self._session

    async def request(self, method, url, *, headers=None, cookies=None) -> TransportResponse:
        session = self._ensure_session()
        resp = await session.request(
            method,
            url,
            headers=dict(headers) if headers else None,
            cookies=dict(cookies) if cookies else None,
            impersonate="chrome",
            proxy=self.settings.proxy or None,
            timeout=self.settings.request_timeout,
        )
        return TransportResponse(
            status=resp.status_code,
            headers={str(k).lower(): str(v) for k, v in resp.headers.items()},
            content=resp.content,
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class HttpxTransport(Transport):
    name = "httpx"

    def __init__(self, settings: Settings):
        import httpx

        self.settings = settings
        self._http = httpx.AsyncClient(
            proxy=settings.proxy or None,
            timeout=settings.request_timeout,
            follow_redirects=True,
        )

    async def request(self, method, url, *, headers=None, cookies=None) -> TransportResponse:
        # httpx doesn't do TLS impersonation; emulate a browser header set so
        # we at least look like one to naive filters.
        hdrs = {
            "User-Agent": FALLBACK_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            hdrs.update(dict(headers))
        if cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        resp = await self._http.request(method, url, headers=hdrs)
        return TransportResponse(
            status=resp.status_code,
            headers={str(k).lower(): str(v) for k, v in resp.headers.items()},
            content=resp.content,
        )

    async def close(self) -> None:
        await self._http.aclose()


def build_transport(settings: Settings) -> Transport:
    """Pick the best available transport: curl_cffi (TLS impersonation) else httpx."""
    if HAS_CURL_CFFI:
        try:
            return CurlCffiTransport(settings)
        except Exception:  # pragma: no cover - defensive
            pass
    return HttpxTransport(settings)


class FlareSolverrSession:
    """Optional FlareSolverr client (plan §1.4): ask it to solve a challenge.

    FlareSolverr is an open-source local proxy that runs a real browser and
    returns solved cookies. We use it to obtain fresh cf_clearance cookies
    which are then injected into subsequent normal requests.
    """

    def __init__(self, endpoint: str, timeout: float = 90.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    async def solve(self, url: str, transport: Transport) -> dict:
        """Run ``request.get`` for ``url``; return {cookies, user_agent}."""
        import httpx

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": int(self.timeout * 1000),
        }
        async with httpx.AsyncClient(timeout=self.timeout + 10) as http:
            resp = await http.post(
                f"{self.endpoint}/v1",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            data = resp.json()
        solution = parse_flaresolverr_response(data)
        cookies = {c["name"]: c["value"] for c in solution.get("cookies", []) if "name" in c}
        return {"cookies": cookies, "user_agent": solution.get("userAgent") or ""}
