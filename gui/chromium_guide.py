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
from core.show_page import read_show
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
        self._wanted: dict[str, str] = {}
        self._commerce: dict[str, dict] = {}
        self._show_pages: dict = {}
        self._page_frames: dict = {}
        self.thread = threading.Thread(target=self._run, name="chromium", daemon=True)
        self.thread.start()

    def sync_live(self, shows: dict) -> None:
        """Open one tab per live show and close the tab when that show ends."""
        clean = {}
        for name, url in (shows or {}).items():
            if name and url:
                clean[str(name)] = str(url)
        with self._lock:
            self._wanted = clean

    def commerce(self) -> dict:
        with self._lock:
            return {key: dict(value) for key, value in self._commerce.items()}

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
                    self._sync_tabs(context)
                try:
                    context.close()
                except Exception:
                    pass
        except Exception as exc:
            log(f"chromium stopped {exc}")

    def _hook(self, page) -> None:
        if id(page) in self._page_frames:
            return
        bucket = deque(maxlen=30)
        self._page_frames[id(page)] = bucket

        def on_ws(ws):
            def keep(payload):
                text = _frame_text(payload)[:8000]
                bucket.append(text)
                self._push_ws(text)

            ws.on("framereceived", keep)

        try:
            page.on("websocket", on_ws)
        except Exception:
            pass

    def _sync_tabs(self, context) -> None:
        with self._lock:
            wanted = dict(self._wanted)
        for name, page in list(self._show_pages.items()):
            if name not in wanted:
                self._close_tab(name, page)
        for name, url in wanted.items():
            page = self._show_pages.get(name)
            if page is None or _closed(page):
                try:
                    page = context.new_page()
                except Exception as exc:
                    log(f"show tab failed {name} {exc}")
                    continue
                self._hook(page)
                self._show_pages[name] = page
            if not _on_show(page, url):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    log(f"show tab {name}")
                except Exception as exc:
                    log(f"show tab open failed {name} {exc}")
        commerce = {}
        for name, page in list(self._show_pages.items()):
            if _closed(page):
                self._show_pages.pop(name, None)
                continue
            dom = {}
            try:
                dom = page.evaluate(_SHOW_JS) or {}
            except Exception:
                dom = {}
            frames = list(self._page_frames.get(id(page)) or [])
            commerce[name] = read_show(dom, frames)
        with self._lock:
            self._commerce = commerce

    def _close_tab(self, name: str, page) -> None:
        self._show_pages.pop(name, None)
        self._page_frames.pop(id(page), None)
        try:
            page.close()
            log(f"show tab closed {name}")
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


def _closed(page) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


def _on_show(page, url: str) -> bool:
    show_id = (url or "").rstrip("/").rsplit("/", 1)[-1]
    if not show_id:
        return False
    try:
        return show_id in (page.url or "")
    except Exception:
        return False


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


_SHOW_JS = """
() => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const itemNode = document.querySelector("[data-testid='show-product-title']");
  let giveaway = "";
  const nodes = document.querySelectorAll("[data-testid*='giveaway'], [data-testid*='Giveaway']");
  for (const node of nodes) {
    const title = node.querySelector("strong[title], [data-testid*='title']");
    const titleText = clean(title && title.innerText);
    if (titleText && !/giveaway/i.test(titleText)) {
      giveaway = titleText;
      break;
    }
  }
  if (!giveaway) {
    for (const heading of document.querySelectorAll("header strong, h2, h3")) {
      const label = clean(heading.innerText);
      if (!/^giveaway\\b/i.test(label) || /upcoming/i.test(label)) continue;
      const section = heading.closest("section");
      const title = section && section.querySelector("strong[title]");
      if (title && clean(title.innerText)) {
        giveaway = clean(title.innerText);
        break;
      }
    }
  }
  return {item: clean(itemNode && itemNode.innerText), giveaway};
}
"""


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
