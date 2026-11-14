"""Chromium window. The profile next to the exe keeps the sign-in."""

from __future__ import annotations
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from queue import Empty, Queue

from core.log import log
from core.paths import app_dir
from core.stream_link import read_streams


class ChromiumGuide:
    def __init__(self):
        self.q: Queue = Queue()
        self._lock = threading.Lock()
        self._alive = True
        self._bounds = None
        self._ws = deque(maxlen=40)
        self._status = {}
        self._names = []
        self._page_url = ""
        self.thread = threading.Thread(target=self._run, name="chromium", daemon=True)
        self.thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._status)

    def set_names(self, names) -> None:
        with self._lock:
            self._names = list(names)
        self.q.put(("names",))

    def goto(self, url: str) -> None:
        self.q.put(("goto", url))

    def move(self, x: int, y: int, w: int, h: int) -> None:
        self._bounds = (int(x), int(y), max(50, int(w)), max(50, int(h)))
        self.q.put(("bounds",))

    def close(self) -> None:
        self._alive = False
        self.q.put(("quit",))
        self.thread.join(3)

    def _push_ws(self, text: str) -> None:
        if text:
            self._ws.append(text[:6000])

    def _run(self) -> None:
        try:
            self._ensure_browser()
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            log(f"chromium unavailable {exc}")
            return
        profile = app_dir() / "data" / "chromium-profile"
        profile.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(app_dir() / "data" / "ms-playwright")
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                self._hook(page)
                context.on("page", self._hook)
                log("chromium ready")
                while self._alive:
                    try:
                        cmd = self.q.get(timeout=1.5)
                    except Empty:
                        cmd = None
                    if cmd and cmd[0] == "quit":
                        break
                    if cmd and cmd[0] == "goto":
                        self._go(page, cmd[1])
                    self._place(context, page)
                    self._poll(page)
                try:
                    context.close()
                except Exception:
                    pass
        except Exception as exc:
            log(f"chromium stopped {exc}")

    def _hook(self, page) -> None:
        def on_ws(ws):
            ws.on("framereceived", lambda payload: self._push_ws(_frame_text(payload)))
            ws.on("framesent", lambda payload: self._push_ws(_frame_text(payload)))

        try:
            page.on("websocket", on_ws)
        except Exception:
            pass

    def _go(self, page, url: str) -> None:
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            log(f"guide open {url}")
        except Exception as exc:
            log(f"guide open failed {exc}")

    def _place(self, context, page) -> None:
        bounds = self._bounds
        if not bounds:
            return
        x, y, w, h = bounds
        try:
            page.evaluate("document.title = 'PEI-GUIDE'")
        except Exception:
            pass
        if sys.platform == "win32" and _place_win32(x, y, w, h):
            return
        try:
            session = context.new_cdp_session(page)
            info = session.send("Browser.getWindowForTarget")
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": info["windowId"],
                    "bounds": {
                        "left": x,
                        "top": y,
                        "width": w,
                        "height": h,
                        "windowState": "normal",
                    },
                },
            )
        except Exception:
            pass

    def _poll(self, page) -> None:
        with self._lock:
            names = list(self._names)
        blobs = list(self._ws)
        links = []
        page_url = ""
        try:
            data = page.evaluate(_PAGE_JS)
            page_url = data.get("href") or ""
            blobs.extend(data.get("blobs") or [])
            links = data.get("links") or []
        except Exception:
            pass
        status = read_streams(names, blobs, links, page_url)
        with self._lock:
            self._status = status
            self._page_url = page_url

    def _ensure_browser(self) -> None:
        root = app_dir() / "data" / "ms-playwright"
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root)
        if any(root.rglob("chrome.exe")) or any(root.rglob("chrome")):
            return
        import playwright

        driver = Path(playwright.__file__).resolve().parent / "driver"
        node = driver / ("node.exe" if sys.platform == "win32" else "node")
        cli = driver / "package" / "cli.js"
        if not node.exists() or not cli.exists():
            raise RuntimeError("playwright driver missing")
        log("downloading chromium")
        kwargs = {
            "env": {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(root)},
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000
        subprocess.check_call([str(node), str(cli), "install", "chromium"], **kwargs)
        log("chromium downloaded")


def _frame_text(payload) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8", "replace")
    return str(payload or "")


def _place_win32(x: int, y: int, w: int, h: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if "PEI-GUIDE" in title.value and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        if not found:
            return False
        user32.SetWindowPos(found[0], -1, x, y, w, h, 0x0010)
        return True
    except Exception:
        return False


_PAGE_JS = """
() => {
  const blobs = [];
  const keys = ["initialRoomDossier", "initialRoom", "roomDossier", "pageInfo", "__NEXT_DATA__", "__data"];
  for (const key of keys) {
    try {
      if (window[key] != null) blobs.push(JSON.stringify(window[key]).slice(0, 20000));
    } catch (e) {}
  }
  try {
    if (window.jQuery) {
      window.jQuery("[data-room],[data-user],[data-username],[data-model],[data-streamer]").slice(0, 30).each(function () {
        try { blobs.push(JSON.stringify(window.jQuery(this).data()).slice(0, 4000)); } catch (e) {}
      });
    }
  } catch (e) {}
  const links = [];
  document.querySelectorAll("a[href]").forEach((node) => {
    if (links.length >= 80) return;
    links.push({
      text: (node.innerText || "").slice(0, 80),
      href: node.href || "",
      cls: node.className || ""
    });
  });
  return {href: location.href, blobs, links};
}
"""
