"""Desktop shell (plan §7 / M5): pywebview window + tray icon.

Flow: pick free port → start uvicorn → open native window → tray icon with
close-to-tray and "Open library folder" shortcuts. pywebview is optional:
when unavailable (headless server / unsupported platform), we fall back to
opening the system browser so ``serve``/``gui`` always work.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from .server import create_app, pick_free_port

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App

log = logging.getLogger("nhcatalogue.desktop")


async def run_desktop(app: App, *, no_tray: bool = False) -> None:
    import uvicorn

    port = pick_free_port()
    api = create_app(app)
    config = uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{port}/"

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    await asyncio.sleep(0.2)  # let the socket bind

    # Inject the auth token into the page the webview loads. The UI stores it
    # in localStorage on first load and sends it as X-NHC-Token on every call.
    url_with_token = f"{url}#token={app.token}"

    def _browser_fallback() -> None:
        import webbrowser

        print(f"pywebview unavailable — opening browser at {url}")
        print(f"  auth token: {app.token}")
        webbrowser.open(url)

    def _run_webview() -> bool:
        try:
            import webview
        except Exception:
            return False
        try:
            window = webview.create_window(
                "nhcatalogue",
                url_with_token,
                width=1400,
                height=900,
                min_size=(900, 600),
                text_select=True,
            )
            if not no_tray:
                try:
                    _start_tray(app, window)
                except Exception:
                    log.exception("tray unavailable; window-only mode")
            webview.start()  # blocks until window closes
            return True
        except Exception:
            log.exception("webview failed")
            return False

    loop = asyncio.get_running_loop()
    ran = await loop.run_in_executor(None, _run_webview)
    if not ran:
        _browser_fallback()
        await server_task  # serve until Ctrl+C
    else:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)


def _make_tray_icon(app: App):
    """Build a pystray icon with close-to-tray + open-library shortcuts."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return None

    # simple flat icon: dark square with a book glyph
    img = Image.new("RGBA", (64, 64), (24, 24, 32, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((14, 12, 50, 52), fill=(90, 90, 140, 255))
    draw.rectangle((20, 18, 44, 46), fill=(24, 24, 32, 255))
    draw.rectangle((23, 21, 32, 43), fill=(160, 160, 210, 255))
    draw.rectangle((34, 21, 41, 43), fill=(160, 160, 210, 255))

    def _open_library():
        import subprocess
        import sys

        folder = str(app.library_root)
        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _quit(icon, item):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open library folder", lambda *_: _open_library()),
        pystray.MenuItem("Show window", lambda *_: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    return pystray.Icon("nhcatalogue", img, "nhcatalogue", menu)


def _start_tray(app: App, window) -> None:
    icon = _make_tray_icon(app)
    if icon is None:
        return

    # pystray needs its own loop; close-to-tray hides the window instead of exit
    window.on_closed = lambda: window.hide()
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
