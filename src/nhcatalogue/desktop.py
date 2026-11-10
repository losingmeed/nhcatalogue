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
    """Run the local server in a worker thread and pywebview on the main thread.

    pywebview (GTK/Qt/Cocoa) insists on owning the main thread, so uvicorn runs
    its own event loop in a background thread while ``webview.start()`` blocks
    the main one.
    """
    import uvicorn

    port = pick_free_port()
    api = create_app(app)
    config = uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{port}/"

    server_thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    server_thread.start()
    for _ in range(200):  # wait for the socket to bind (max ~10s)
        if server.started:
            break
        await asyncio.sleep(0.05)

    # Inject the auth token into the page the webview loads. The UI stores it
    # in localStorage on first load and sends it as X-NHC-Token on every call.
    url_with_token = f"{url}#token={app.token}"

    def _browser_fallback() -> None:
        import webbrowser

        print(f"pywebview unavailable — opening browser at {url_with_token}")
        print(f"  auth token: {app.token}")
        webbrowser.open(url_with_token)

    def _run_webview() -> bool:
        try:
            import webview
        except Exception:
            log.debug("pywebview not installed")
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
            if no_tray:
                webview.start()  # must run on the main thread; blocks until close
            else:
                # Start the tray only after the GUI loop is up: pystray's
                # GTK/AppIndicator backend must not initialise before Qt.
                def _tray() -> None:
                    try:
                        _start_tray(app, window)
                    except Exception:
                        log.exception("tray unavailable; window-only mode")

                webview.start(_tray)  # must run on the main thread; blocks until close
            return True
        except Exception:
            log.exception("webview failed")
            return False

    try:
        # No run_in_executor: pywebview requires the main thread.
        ran = _run_webview()
        if not ran:
            _browser_fallback()
            try:
                await asyncio.Event().wait()  # serve until Ctrl+C
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
    finally:
        server.should_exit = True
        if server_thread.is_alive():
            server_thread.join(timeout=5)


def _make_tray_icon(app: App, window, allow_close: list[bool]):
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
        allow_close[0] = True  # let destroy() get past the close-to-tray hook
        icon.stop()
        try:
            window.destroy()
        except Exception:
            log.exception("failed to close window from tray")

    menu = pystray.Menu(
        pystray.MenuItem("Open library folder", lambda *_: _open_library()),
        pystray.MenuItem("Show window", lambda *_: window.show()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    return pystray.Icon("nhcatalogue", img, "nhcatalogue", menu)


def _start_tray(app: App, window) -> None:
    allow_close = [False]
    icon = _make_tray_icon(app, window, allow_close)
    if icon is None:
        return

    # close-to-tray: hide the window instead of quitting the app
    def _on_closing() -> bool:
        if allow_close[0]:
            return True  # tray "Quit" asked for a real close
        window.hide()
        return False  # cancel the actual close

    window.events.closing += _on_closing
    # pystray needs its own loop
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
