"""Device connection – auto ADB, no temp-file locks."""

from __future__ import annotations
import io
import threading
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw

from .hierarchy import parse_hierarchy_xml, load_hierarchy_from_file
from .element import UIElement


class DeviceConnector:
    def __init__(self):
        self.device = None
        self.demo_mode = True
        self.last_hierarchy: Optional[UIElement] = None
        self.last_screenshot: Optional[Image.Image] = None
        self.screen_size: Tuple[int, int] = (1080, 2400)
        self.serial: Optional[str] = None
        self._model_cache: dict = {}
        self._cap_running = False
        self._cap_thread = None
        self._frame_callback = None

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
            import adbutils
            devices = adbutils.adb.device_list()
            if not devices:
                self.device = None
                self.serial = None
                return "No ADB device found"
            first = devices[0].serial
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
        """Return [(serial, label), ...] for every connected phone."""
        import adbutils
        rows = []
        for d in adbutils.adb.device_list():
            serial = d.serial
            if serial not in self._model_cache:
                try:
                    model = (d.shell("getprop ro.product.model") or "").strip()
                except Exception:
                    model = ""
                self._model_cache[serial] = model or serial
            rows.append((serial, self._model_cache[serial]))
        return rows

    def select_serial(self, serial: str) -> str:
        import uiautomator2 as u2
        self.serial = serial
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
        return self._refresh_real()

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
            if self.demo_mode or not self.device:
                if self.last_screenshot and self._frame_callback:
                    try:
                        self._frame_callback(self.last_screenshot)
                    except Exception:
                        pass
                threading.Event().wait(0.3)
                continue
            try:
                # Fast raw screencap
                img = self._adb_screencap()
                if img:
                    # Downscale for smoother UI updates
                    w, h = img.size
                    if w > 540:
                        scale = 540 / w
                        img = img.resize((540, int(h * scale)), Image.Resampling.BILINEAR)
                    self.last_screenshot = img.convert("RGB")
                    self.screen_size = self.last_screenshot.size
                    if self._frame_callback:
                        self._frame_callback(self.last_screenshot)
            except Exception:
                threading.Event().wait(0.05)

    def _refresh_real(self) -> str:
        if not self.device:
            return self._connect_real()
        try:
            xml = self.device.dump_hierarchy()
            self.last_hierarchy = parse_hierarchy_xml(xml)
            img = self.device.screenshot(format="pillow")
            if img is None:
                img = self._adb_screencap()
            self.last_screenshot = img.convert("RGB") if img else None
            if self.last_screenshot:
                self.screen_size = self.last_screenshot.size
            return "Refreshed"
        except Exception as e:
            return f"Refresh failed: {e}"

    def _adb_screencap(self) -> Optional[Image.Image]:
        """Pure adb screencap into memory – no temp files."""
        try:
            import adbutils
            if self.serial:
                d = adbutils.adb.device(serial=self.serial)
            else:
                d = adbutils.adb.device()
            data = d.shell("screencap -p", encoding=None)
            if not data:
                return None
            # Some devices prefix a useless header; strip if needed
            if data[:4] != b"\x89PNG":
                # try finding PNG signature
                idx = data.find(b"\x89PNG")
                if idx >= 0:
                    data = data[idx:]
            return Image.open(io.BytesIO(data))
        except Exception:
            return None

    def _load_demo(self) -> str:
        demo_dir = Path(__file__).parent.parent / "demo"
        xml_path = demo_dir / "sample_hierarchy.xml"
        img_path = demo_dir / "sample_screenshot.png"
        if not xml_path.exists():
            return "Demo files missing"
        self.last_hierarchy = load_hierarchy_from_file(xml_path)
        if img_path.exists():
            with Image.open(img_path) as im:
                self.last_screenshot = im.convert("RGB")
        else:
            self.last_screenshot = self._generate_placeholder_screenshot()
        self.screen_size = self.last_screenshot.size
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
