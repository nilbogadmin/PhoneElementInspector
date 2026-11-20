"""Device connection – auto ADB, no temp-file locks."""

from __future__ import annotations
import io
import threading
import time
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw

from .hierarchy import parse_hierarchy_xml, load_hierarchy_from_file
from .element import UIElement
from .paths import resource_dir


class DeviceConnector:
    def __init__(self):
        self.device = None
        self.demo_mode = True
        self.last_hierarchy: Optional[UIElement] = None
        self.last_screenshot: Optional[Image.Image] = None
        self.screen_size: Tuple[int, int] = (1080, 2400)
        self.serial: Optional[str] = None
        self.px_scale = 1.0
        self.display_full_size: Tuple[int, int] = (0, 0)
        self.capture_size: Tuple[int, int] = (0, 0)
        self.wm_sizes: Tuple[Tuple[int, int], ...] = ()
        self._wm_cache: dict = {}
        self.device_px: Tuple[int, int] = (1080, 2400)
        self._model_cache: dict = {}
        self._identity: dict = {}
        self.family: list = []
        self._cap_running = False
        self._cap_thread = None
        self._frame_callback = None
        self._switching = False
        self.frame_serial = None
        self._video_failed = None
        self._mirror_lock = threading.Lock()

    def connect(self) -> str:
        """Try real ADB first; fall back to demo only if nothing is connected."""
        msg = self._connect_real()
        if self.device is not None:
            self.demo_mode = False
            refresh_msg = self._refresh_real()
            return f"{msg}  ·  {refresh_msg}"
        self.demo_mode = True
        return self._load_demo()

    def refresh(self) -> str:
        # Always re-check device list so hot-plug works
        try:
            import adbutils
            devices = adbutils.adb.device_list()
            if devices and (self.demo_mode or self.device is None):
                return self.connect()
            if not devices and not self.demo_mode:
                self.device = None
                self.demo_mode = True
                return self._load_demo()
        except Exception:
            pass
        if not self.demo_mode and self.device is not None:
            return self._refresh_real()
        return self.connect()

    def _connect_real(self) -> str:
        try:
            import uiautomator2 as u2
            from .adb import devices as adb_devices
            rows = [(s, st) for s, st in adb_devices() if st == "device"]
            if not rows:
                try:
                    import adbutils
                    rows = [(d.serial, "device") for d in adbutils.adb.device_list()]
                except Exception:
                    rows = []
            if not rows:
                self.device = None
                self.serial = None
                return "No ADB device found"
            first = rows[0][0]
            self.serial = first
            self.device = u2.connect(first)
            info = self.device.info
            self.screen_size = (
                info.get("displayWidth", 1080),
                info.get("displayHeight", 2400),
            )
            name = info.get("productName") or info.get("model") or "Android"
            return f"Connected: {name}"
        except Exception as e:
            self.device = None
            return f"ADB unavailable ({e})"

    def list_devices(self) -> list:
        """The training menu: first 2024 and first 2025 only."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from .match import model_year, trainer_picks
        import json
        from .paths import app_dir

        remembered = {}
        try:
            raw = json.loads((app_dir() / "data" / "years.json").read_text(encoding="utf-8"))
            remembered = {str(k): v for k, v in raw.items() if v in ("2024", "2025")}
        except Exception:
            remembered = {}
        try:
            from .adb import devices as adb_devices
            serials = [serial for serial, state in adb_devices() if state == "device"]
            self._last_serials = list(serials)
        except Exception:
            serials = list(getattr(self, "_last_serials", []))
        years = {}
        if serials:
            pool = ThreadPoolExecutor(max_workers=4)
            futs = {pool.submit(self.identity, serial): serial for serial in serials}
            for fut in as_completed(futs):
                serial = futs[fut]
                try:
                    ident = fut.result() or {}
                except Exception:
                    ident = {}
                year = model_year(ident.get("label") or "", *(ident.get("family") or []))
                years[serial] = year or remembered.get(serial, "")
            pool.shutdown(wait=True)
        found = []
        for serial in serials:
            found.append((serial, years.get(serial) or remembered.get(serial, "")))
        return trainer_picks(found)

    def identity(self, serial: Optional[str] = None) -> dict:
        """Marketing name plus the aliases shared by every phone of that model."""
        from .match import family_aliases, model_year

        serial = serial or self.serial
        if not serial:
            return {"family": [], "label": "Android"}
        cached = self._identity.get(serial)
        if cached is not None:
            return cached
        info = {"family": [], "label": serial, "ok": False}
        try:
            from .adb import call as adb_call
            data = adb_call(
                ["-s", serial, "shell",
                 "getprop ro.product.marketname; echo ---; "
                 "getprop ro.product.model; echo ---; "
                 "getprop ro.product.vendor.model; echo ---; "
                 "getprop ro.product.name; echo ---; "
                 "getprop ro.boot.hardware.sku"],
                timeout=12,
            )
            parts = [p.strip() for p in data.decode("utf-8", "replace").split("---")]
            parts = [p for p in parts if p and p != "null"]
            if not parts:
                return info
            info = {
                "family": family_aliases(*parts),
                "label": next((p for p in parts if p), serial),
                "ok": True,
            }
            if model_year(*parts) not in ("2024", "2025"):
                return info
        except Exception:
            return info
        self._identity[serial] = info
        self._model_cache[serial] = info["label"]
        if serial == self.serial:
            self.family = list(info["family"])
        return info

    def select_serial(self, serial: str) -> str:
        import uiautomator2 as u2
        self._switching = True
        self.serial = serial
        self.last_hierarchy = None
        if self._mirror_lock.acquire(timeout=4):
            self._mirror_lock.release()
        try:
            self.device = u2.connect(serial)
            self.demo_mode = False
            try:
                info = self.device.info or {}
                self.screen_size = (
                    info.get("displayWidth", 1080),
                    info.get("displayHeight", 2400),
                )
            except Exception:
                pass
            self.family = list(self.identity(serial).get("family") or [])
            return self._refresh_real()
        finally:
            self.frame_serial = serial
            self._switching = False

    def start_live(self, callback):
        """Continuous capture on a background thread. Calls callback(img) for each frame."""
        self._frame_callback = callback
        if self._cap_running:
            return
        self._cap_running = True
        self._cap_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._cap_thread.start()

    def stop_live(self):
        self._cap_running = False

    def _capture_loop(self):
        while self._cap_running:
            if self._switching or self.demo_mode or not self.device or not self.serial:
                threading.Event().wait(0.2)
                continue
            serial = self.serial
            if self._video_failed != serial:
                yielded = False
                try:
                    from .fast_mirror import iter_mirror
                    with self._mirror_lock:
                        for img in iter_mirror(serial, lambda: self._cap_running and not self._switching and self.serial == serial):
                            yielded = True
                            if self.serial != serial or self._switching:
                                break
                            self._publish_frame(img, serial)
                    if not yielded and self._cap_running and self.serial == serial and not self._switching:
                        self._video_failed = serial
                        from .log import log
                        log("mirror video unavailable")
                    continue
                except Exception as exc:
                    if not yielded:
                        self._video_failed = serial
                    try:
                        from .log import log
                        log(f"mirror video failed {exc}")
                    except Exception:
                        pass
                    threading.Event().wait(0.4)
                    continue
            try:
                img = self._adb_screencap()
                if img is not None and self.serial == serial and not self._switching:
                    self._publish_frame(img, serial)
            except Exception:
                pass
            threading.Event().wait(0.2)

    def _publish_frame(self, img, serial: str) -> None:
        self._set_display(img)
        self.frame_serial = serial
        if self._frame_callback:
            self._frame_callback(self.last_screenshot)

    def _refresh_real(self) -> str:
        if not self.device:
            return self._connect_real()
        try:
            xml = self._dump_xml()
            if xml:
                self.last_hierarchy = parse_hierarchy_xml(xml)
            img = self._adb_screencap()
            if img is not None:
                self._set_display(img)
            self._probe_wm()
            return "Refreshed"
        except Exception as e:
            return f"Refresh failed: {e}"

    def _set_display(self, img: Image.Image) -> None:
        """Keep one small top-half frame. Hierarchy stays in device pixels."""
        w, h = img.size
        self.capture_size = (w, h)
        self.device_px = (w, h)
        self.screen_size = (w, h)
        scale = 1.0
        self.px_scale = scale
        dw, dh = img.size
        self.display_full_size = (dw, dh)
        top = max(1, dh // 2)
        self.last_screenshot = img.crop((0, 0, dw, top)).convert("RGB")

    def wm_for(self, serial: Optional[str] = None):
        """Physical and override sizes for one phone. Cached."""
        serial = serial or self.serial
        if not serial:
            return ()
        cached = self._wm_cache.get(serial)
        if cached is not None:
            if serial == self.serial:
                self.wm_sizes = cached
            return cached
        sizes: Tuple[Tuple[int, int], ...] = ()
        try:
            import adbutils
            import re
            text = adbutils.adb.device(serial=serial).shell("wm size") or ""
            found = []
            for line in str(text).splitlines():
                match = re.search(r"(\d+)\s*x\s*(\d+)", line)
                if not match:
                    continue
                pair = (int(match.group(1)), int(match.group(2)))
                if "override" in line.lower():
                    found.insert(0, pair)
                else:
                    found.append(pair)
            sizes = tuple(found)
        except Exception:
            sizes = ()
        self._wm_cache[serial] = sizes
        if serial == self.serial:
            self.wm_sizes = sizes
        return sizes

    def _probe_wm(self) -> None:
        if self.demo_mode or not self.serial:
            return
        self.wm_for(self.serial)

    def _dump_xml(self, device=None) -> str:
        """Full tree. Compressed dumps keep the phone chrome and drop the app."""
        target = device or self.device
        try:
            return target.dump_hierarchy(compressed=False)
        except TypeError:
            return target.dump_hierarchy()

    def _adb_screencap(self) -> Optional[Image.Image]:
        """One screenshot. Used only if the video mirror cannot start."""
        try:
            import shutil
            import subprocess
            adb = shutil.which("adb") or "adb"
            cmd = [adb]
            if self.serial:
                cmd += ["-s", self.serial]
            cmd += ["exec-out", "screencap", "-p"]
            flags = {"creationflags": 0x08000000} if __import__("sys").platform == "win32" else {}
            data = subprocess.check_output(cmd, timeout=8, **flags)
            if not data:
                return None
            if data[:4] != b"\x89PNG":
                idx = data.find(b"\x89PNG")
                if idx >= 0:
                    data = data[idx:]
            im = Image.open(io.BytesIO(data))
            im.load()
            return im
        except Exception:
            return None

    def _load_demo(self) -> str:
        demo_dir = resource_dir() / "demo"
        xml_path = demo_dir / "sample_hierarchy.xml"
        img_path = demo_dir / "sample_screenshot.png"
        if not xml_path.exists():
            return "Demo files missing"
        self.last_hierarchy = load_hierarchy_from_file(xml_path)
        if img_path.exists():
            with Image.open(img_path) as im:
                img = im.convert("RGB")
        else:
            img = self._generate_placeholder_screenshot()
        self._set_display(img)
        return "Demo mode (no ADB device)"

    def _generate_placeholder_screenshot(self) -> Image.Image:
        w, h = 1080, 2400
        img = Image.new("RGB", (w, h), color=(30, 30, 40))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, w, 80], fill=(20, 20, 30))
        draw.text((40, 25), "12:00", fill=(220, 220, 220))
        draw.rectangle([0, 80, w, 220], fill=(50, 90, 180))
        draw.text((40, 130), "Demo Banking App", fill=(255, 255, 255))
        draw.rounded_rectangle([60, 280, w - 60, 520], radius=24, fill=(45, 45, 60))
        draw.text((100, 320), "Available Balance", fill=(180, 180, 200))
        draw.text((100, 390), "$12,450.75", fill=(100, 220, 150))
        return img

    def click(self, x: int, y: int) -> str:
        if self.demo_mode or not self.device:
            return f"[Demo] click ({x}, {y})"
        try:
            self.device.click(x, y)
            return f"Clicked ({x}, {y})"
        except Exception as e:
            return f"Click failed: {e}"

    def get_hierarchy(self) -> Optional[UIElement]:
        return self.last_hierarchy

    def get_screenshot(self) -> Optional[Image.Image]:
        return self.last_screenshot


def screen_locked(text: str) -> bool:
    """True when the dump shows the keyguard. A PIN lock cannot be skipped."""
    low = (text or "").lower()
    if "mshowinglockscreen=true" in low or "isstatusbarkeyguard=true" in low or "mdreaminglockscreen=true" in low:
        return True
    if "mshowinglockscreen=false" in low or "isstatusbarkeyguard=false" in low:
        return False
    return False
