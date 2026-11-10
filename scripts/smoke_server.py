"""End-to-end smoke test: real uvicorn on a free port, localhost HTTP only.

Verifies SPA serving, token guard, health, settings and DB round-trip without
any external network access.
"""

import asyncio
import json
import tempfile
import urllib.error
import urllib.request

import uvicorn

from nhcatalogue.app import App
from nhcatalogue.server import create_app, pick_free_port


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        app = App(td)
        api = create_app(app)
        port = pick_free_port()
        config = uvicorn.Config(
            api, host="127.0.0.1", port=port, log_level="error", access_log=False
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.4)

        base = f"http://127.0.0.1:{port}"

        def get(path, headers=None, method="GET", body=None):
            req = urllib.request.Request(base + path, headers=headers or {}, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
            try:
                r = urllib.request.urlopen(req, timeout=10)
                return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()

        # SPA served at /
        s, body = await asyncio.to_thread(get, "/")
        assert s == 200 and b'id="root"' in body, (s, body[:120])
        print("SPA served \u2713")

        # token guard
        s, _ = await asyncio.to_thread(get, "/api/health")
        assert s == 401, s
        print("token guard \u2713 (401 without token)")

        h = {"X-NHC-Token": app.token}

        s, body = await asyncio.to_thread(get, "/api/health", h)
        health = json.loads(body)
        assert s == 200 and health["ok"] and health["curl_cffi"], health
        print("health with token \u2713", {k: health[k] for k in ("version", "galleries", "transport", "curl_cffi")})

        # SPA fallback route (client-side routing)
        s, body = await asyncio.to_thread(get, "/library", h)
        assert s == 200 and b'id="root"' in body
        print("SPA fallback \u2713")

        # settings round trip
        s, body = await asyncio.to_thread(get, "/api/settings", h)
        st = json.loads(body)
        assert st["domains"][0] == "nhentai.net" and "has_curl_cffi" in st
        print("settings \u2713  curl_cffi:", st["has_curl_cffi"])

        server.should_exit = True
        await asyncio.wait_for(task, 10)
        app.close()
        print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
