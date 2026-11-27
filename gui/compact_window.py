"""
Small bottom-right style controller.
Control bar and phone mirror share the same width.
Mirror only shows when exactly one device is connected.
"""
from __future__ import annotations
import random
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import customtkinter as ctk
from tkinter import messagebox
from typing import Optional

from core.device import DeviceConnector
from core.tagger import Tagger
from core.rules import RulesEngine
from core.element import UIElement, flatten_elements
from core.paths import app_dir
from core.log import log
from gui.phone_view import PhoneView
from gui.chromium_guide import ChromiumGuide

BG = "#121214"
SURFACE = "#1c1c1e"
SURFACE2 = "#2c2c2e"
BLUE = "#0a84ff"
GREEN = "#30d158"
RED = "#ff453a"
SECONDARY = "#98989f"
LABEL = "#ffffff"

BAR_W = 900
BAR_H = 84
DROP_H = 48
HEAD_H = 42
PHONE_ASPECT = (19.5 / 9) * 0.5   # half height for top-half only
URL_H = 36
SLOT_H = 330


class CompactController(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Element Inspector")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        data = app_dir() / "data"
        data.mkdir(parents=True, exist_ok=True)
        self.device = DeviceConnector()
        self.tagger = Tagger(storage_path=data / "tags.json")
        self.rules = RulesEngine(tagger=self.tagger, storage_path=data / "rules.json")
        self.rules.set_click_callback(lambda x, y: None)

        self.mirror_on = False
        self.current_element = None
        self.root_element = None
        self._forgotten = set()  # (resource_id, text, bounds) tuples to keep ignored
        self.root_element: Optional[UIElement] = None
        self._rules_job = None
        self._year_on = {"2024": False, "2025": False}
        self._active_year = "2024"
        self._detect_job = None
        self._hier_job = None
        self._frame_pending = False
        self._drag_x = 0
        self._drag_y = 0
        self._serial_by_year: dict[str, str] = {}
        self._u2_cache: dict = {}
        self._u2_lock = threading.Lock()
        self._rule_busy = False
        self._pool = ThreadPoolExecutor(max_workers=24, thread_name_prefix="rules")
        self._hier_busy = False
        self._detect_busy = False
        self._pause_hier = False
        self._tree_sig_val = None
        self._xml_hash = None
        self._mapped_scale = None
        self._dev_sig = None
        self._held = {}
        self._busy_serials = set()
        self._busy_lock = threading.Lock()
        self._overlay_job = None
        self.guide_slots = self._load_slots()
        from core.whatnot_live import ShowBoard
        self.board = ShowBoard()
        self.board.set_names(self.guide_slots)
        self.chromium = None
        self._whatnot_status = {}
        self._whatnot_busy = False
        self._whatnot_at = 0.0
        self._whatnot_misses = {}
        self._adb_bad = set()
        self._adb_refresh_busy = False
        self._controllers = {}
        self._taps_sent = 0
        self._ctrl_lock = threading.Lock()
        self._year_url = {"2024": "", "2025": ""}
        self._year_token = {"2024": 0, "2025": 0}
        self._stream_ok = {}
        self._why = {}
        self._years = self._load_years()

        self._fit_ui()
        self._build_ui()
        self._place_center()
        self.after(200, self._initial_load)
        self._start_detect()
        self.after(15000, self._fullscreen_tick)
        self.after(20000, self._adb_refresh_tick)
        self._bind_drag()
        log("ready")

    def _fit_ui(self):
        """Leave a fixed block for the nine show cards and fit the rest on this screen."""
        sw = self.winfo_screenwidth() or 1920
        sh = self.winfo_screenheight() or 1080
        self._ui_w = min(760, max(560, sw - 24))
        self._ui_head = 32
        self._ui_url = 28
        self._ui_bar = 56
        self._ui_btn_w = max(110, (self._ui_w - 40) // 4)
        self._ui_btn_h = 40
        self._font_btn = 16
        self._font_menu = 14
        self._font_slot = 12
        self._font_overlay = 16
        # Five lines, three rows. This stays on screen; the mirror takes what is left.
        self._ui_slot = min(300, max(228, int((sh - 80) * 0.32)))

    def _build_ui(self):
        btn_font = ctk.CTkFont(family="Poppins Condensed", size=self._font_btn, weight="bold")
        menu_font = ctk.CTkFont(family="Poppins Condensed", size=self._font_menu, weight="bold")

        self.header = ctk.CTkFrame(self, height=self._ui_head, fg_color=SURFACE, corner_radius=12)
        self.header.pack(fill="x", side="top", pady=(0, 4))
        self.header.pack_propagate(False)
        self.drag_zone = ctk.CTkLabel(
            self.header, text="0 controllers", fg_color="transparent",
            font=menu_font, text_color="#d0d0d0", anchor="w",
        )
        self.drag_zone.pack(side="left", fill="both", expand=True)
        self.close_btn = ctk.CTkButton(
            self.header, text="X", width=max(36, self._font_btn * 2), height=max(24, self._ui_head - 8),
            corner_radius=8, fg_color=RED, hover_color="#d32b22", command=self._close, font=btn_font,
        )
        self.close_btn.pack(side="right", padx=6, pady=4)

        self.url_bar = ctk.CTkFrame(self, height=self._ui_url, fg_color=SURFACE, corner_radius=8)
        self.url_bar.pack(fill="x", side="top", pady=(0, 4))
        self.url_bar.pack_propagate(False)
        self.url_entry = ctk.CTkEntry(
            self.url_bar, height=max(22, self._ui_url - 8), corner_radius=6,
            fg_color=SURFACE2, border_width=0,
            font=menu_font, placeholder_text="SIGN IN PAGE",
        )
        self.url_entry.pack(fill="x", padx=8, pady=4)
        self.url_entry.bind("<Return>", self._open_guide_url)

        self.slot_bar = ctk.CTkFrame(self, height=self._ui_slot, fg_color=SURFACE, corner_radius=8)
        self.slot_bar.pack(fill="x", side="top", pady=(0, 4))
        self.slot_bar.pack_propagate(False)
        slot_font = ctk.CTkFont(family="Poppins Condensed", size=self._font_slot, weight="bold")
        wrap = max(140, self._ui_w // 3 - 24)
        self.stream_btns = []
        for col in range(3):
            self.slot_bar.grid_columnconfigure(col, weight=1, uniform="stream")
        for row in range(3):
            self.slot_bar.grid_rowconfigure(row, weight=1, uniform="stream")
        for i in range(9):
            btn = ctk.CTkButton(
                self.slot_bar, text="checking", height=max(72, self._ui_slot // 3 - 10),
                corner_radius=8, fg_color=RED, hover_color="#d32b22",
                font=slot_font, command=lambda i=i: self._streamer_click(i),
            )
            btn.grid(row=i // 3, column=i % 3, sticky="nsew", padx=3, pady=3)
            try:
                btn.configure(wraplength=wrap)
            except Exception:
                pass
            btn.bind("<Button-3>", lambda _e, i=i: self._rename_streamer(i))
            self.stream_btns.append(btn)

        self.mirror_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=16)

        self.year_bar = ctk.CTkFrame(self.mirror_frame, fg_color="transparent")
        self.year_bar.pack(fill="x", padx=8, pady=(8, 0))
        self.year_bar.grid_columnconfigure(0, weight=1, uniform="year")
        self.year_bar.grid_columnconfigure(1, weight=1, uniform="year")
        self.year_btns = {}
        for col, year in enumerate(("2024", "2025")):
            btn = ctk.CTkButton(
                self.year_bar, text=year, height=max(28, self._font_menu + 16), corner_radius=8,
                fg_color=SURFACE2, hover_color="#3a3a3c",
                font=menu_font, command=lambda y=year: self._pick_year(y),
            )
            btn.grid(row=0, column=col, sticky="ew", padx=(0, 4) if col == 0 else (4, 0))
            self.year_btns[year] = btn
        self._paint_years()

        self.phone_view = PhoneView(
            self.mirror_frame,
            on_element_selected=self._on_element_selected,
            on_crop_changed=None,
        )
        self.phone_view.pack(fill="both", expand=True, padx=6, pady=6)
        self.phone_view.set_outline_mode(show_all=False, only_tagged=False)

        self.overlay = ctk.CTkLabel(
            self.mirror_frame, text="", text_color=LABEL,
            font=ctk.CTkFont(family="Poppins Condensed", size=self._font_overlay, weight="bold"),
            fg_color="#1c1c1e", corner_radius=6, justify="center",
        )

        self.bar = ctk.CTkFrame(self, height=self._ui_bar, fg_color=SURFACE, corner_radius=16)
        self.bar.pack(fill="x", side="bottom", pady=(4, 0))
        self.bar.pack_propagate(False)

        self._bar_inner = ctk.CTkFrame(self.bar, fg_color="transparent")
        self._bar_inner.pack(fill="both", expand=True, padx=6, pady=4)

        btn_w, btn_h = self._ui_btn_w, self._ui_btn_h
        self.add_btn = ctk.CTkButton(
            self._bar_inner, text="ADD", width=btn_w, height=btn_h, corner_radius=10,
            fg_color=GREEN, hover_color="#28b34a",
            command=self._quick_rule, font=btn_font,
        )
        self.add_btn.pack(side="left", padx=4)

        self.run_btn = ctk.CTkButton(
            self._bar_inner, text="START", width=btn_w, height=btn_h, corner_radius=10,
            fg_color=BLUE, hover_color="#0066cc",
            command=self._toggle_rules, font=btn_font,
        )
        self.run_btn.pack(side="left", padx=4)

        self.reset_btn = ctk.CTkButton(
            self._bar_inner, text="RESET", width=btn_w, height=btn_h, corner_radius=10,
            fg_color=SURFACE2, hover_color="#3a3a3c",
            command=self._reset_rules, font=btn_font,
        )
        self.reset_btn.pack(side="left", padx=4)

        self.apps_btn = ctk.CTkButton(
            self._bar_inner, text="CLOSE APPS", width=btn_w, height=btn_h, corner_radius=10,
            fg_color="#5c1a1a", hover_color="#7a2424",
            command=self._close_apps, font=btn_font,
        )
        self.apps_btn.pack(side="left", padx=4)

    def _window_height(self) -> int:
        sh = self.winfo_screenheight() or 1080
        return max(640, sh - 72)

    def _format_geo(self, x: int, y: int) -> str:
        """Fixed size. Negative coordinates must not become '+-10' or Tk throws."""
        w, h = self._win_w, self._win_h
        sw = self.winfo_screenwidth() or 1280
        sh = self.winfo_screenheight() or 800
        x = int(x)
        y = int(y)
        x = min(max(x, -w + 80), max(0, sw - 80))
        y = min(max(y, 0), max(0, sh - 40))
        sx = f"+{x}" if x >= 0 else str(x)
        sy = f"+{y}" if y >= 0 else str(y)
        return f"{w}x{h}{sx}{sy}"

    def _place_center(self):
        self.update_idletasks()
        self._win_w = self._ui_w
        self._win_h = self._window_height()
        self.resizable(False, False)
        self.minsize(self._win_w, self._win_h)
        self.maxsize(self._win_w, self._win_h)
        sw = self.winfo_screenwidth() or 1280
        sh = self.winfo_screenheight() or 800
        x = max(0, (sw - self._win_w) // 2)
        y = max(0, (sh - self._win_h) // 2)
        self.geometry(self._format_geo(x, y))
        self.mirror_on = True
        self.mirror_frame.pack(fill="both", expand=True, before=self.bar)
        self._raise_controls()
        self.after(50, self._raise_controls)
        self.after(300, self._raise_controls)
        self._start_live()
        self.after(600, self._start_guide)

    def _apply_geometry(self):
        x, y = self.winfo_x(), self.winfo_y()
        self.geometry(self._format_geo(x, y))

    def _toggle_mirror(self):
        # Only allow mirror when exactly one device
        devices = getattr(self.device, "devices", None) or []
        if not devices and not self.device.demo_mode:
            self._show_overlay("No device")
            return
        # For now treat connected (non-demo) as single-device eligible
        if self.device.demo_mode and not self.mirror_on:
            self._show_overlay("Connect 1 device")
            return
        self.mirror_on = not self.mirror_on
        self._apply_geometry()

    def _bind_drag(self):
        for widget in (self.header, self.drag_zone):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _do_drag(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(self._format_geo(x, y))
        self._sync_guide()

    def _load_slots(self):
        path = app_dir() / "data" / "guide.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            names = list(data.get("names") or [])
        except Exception:
            data = {}
            names = []
        names = (names + [""] * 9)[:9]
        defaults = [
            "krakenhits", "novatcg", "woosleys", "vendturesvault", "traderbea", "edcplug",
            "rosescloset", "vendtures", "traderbeapllus",
        ]
        if not any(names):
            names = defaults
        else:
            for i in range(6, 9):
                if not names[i]:
                    names[i] = defaults[i]
        self._guide_url = data.get("url") or "https://www.whatnot.com"
        return names

    def _save_slots(self):
        path = app_dir() / "data" / "guide.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "url": self.url_entry.get().strip() if hasattr(self, "url_entry") else self._guide_url,
            "names": self.guide_slots,
        }, indent=2), encoding="utf-8")

    def _start_guide(self):
        if self.url_entry.get().strip() == "" and self._guide_url:
            self.url_entry.insert(0, self._guide_url)
        self._paint_streamers()
        self.chromium = ChromiumGuide()
        self.chromium.set_names(self.guide_slots)
        if self._guide_url:
            self.chromium.goto(self._guide_url)
        self._sync_guide()
        log("guide on")

    def _sync_guide(self):
        return

    def _open_guide_url(self, _event=None):
        url = self.url_entry.get().strip()
        self._guide_url = url
        self._save_slots()
        if self.chromium and url:
            self.chromium.goto(url)
            self._show_overlay("Opening")

    def _rename_streamer(self, index: int):
        top = ctk.CTkToplevel(self)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(fg_color=SURFACE)
        top.geometry("280x90")
        entry = ctk.CTkEntry(top, height=32, fg_color=SURFACE2, border_width=0)
        entry.insert(0, self.guide_slots[index] or "")
        entry.pack(fill="x", padx=10, pady=(12, 6))
        def save(_e=None):
            self.guide_slots[index] = entry.get().strip()
            self._save_slots()
            if self.board:
                self.board.set_names(self.guide_slots)
            if self.chromium:
                self.chromium.set_names(self.guide_slots)
            top.destroy()
            self._paint_streamers()
        entry.bind("<Return>", save)
        ctk.CTkButton(top, text="SAVE", height=28, fg_color=GREEN, command=save).pack(pady=(0, 8))
        top.update_idletasks()
        x = self.winfo_rootx() + 40
        y = self.slot_bar.winfo_rooty() + 4
        top.geometry(f"280x90+{x}+{y}")
        entry.focus_set()

    def _streamer_click(self, index: int):
        name = self.guide_slots[index]
        if not name:
            self._rename_streamer(index)
            return
        info = self._streamer_info(name)
        if not info.get("online") or not info.get("url"):
            self._show_overlay("Offline")
            return
        url = info["url"]
        now = time.monotonic()
        if now - getattr(self, "_stream_click_at", 0) < 0.6 and getattr(self, "_stream_click_i", -1) == index:
            return
        self._stream_click_at = now
        self._stream_click_i = index
        log(f"send {name} {url} {self._active_year}")
        year = self._active_year
        threading.Thread(target=self._send_stream, args=(url, name, year), daemon=True).start()

    def _send_stream(self, url: str, name: str, year: str):
        from core.stream_link import safe_url
        url = safe_url(url)
        if not url:
            self.after(0, lambda: self._show_overlay("Bad link"))
            return
        self._year_url[year] = url
        token = self._year_token.get(year, 0) + 1
        self._year_token[year] = token
        self._remember_years()
        self._sync_controllers()
        for serial, ctl in list(self._controllers.items()):
            if ctl.year in ("2024", "2025"):
                continue
            try:
                ctl.year = self._phone_year(serial) or ""
            except Exception:
                ctl.year = ""
        serials = [serial for serial, ctl in self._controllers.items() if ctl.year == year]
        self.after(0, lambda n=len(serials): self._show_overlay(f"{year} sending {n}"))
        log(f"sent {name} to {len(serials)} {year}")
        if not serials:
            return
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ok = 0

        def one(serial):
            try:
                self._open_on_phone(serial, url)
            except Exception as exc:
                from core.panda import shell
                cmd = (
                    "am start -a android.intent.action.VIEW "
                    "-c android.intent.category.BROWSABLE "
                    f"-d {url} -p com.whatnot_mobile -f 0x10008000"
                )
                if not shell(serial, cmd, timeout=8):
                    log(f"{serial} stream failed {exc}")
                    return False
            ctl = self._controllers.get(serial)
            if ctl is not None and self._year_token.get(year) == token:
                ctl.stream_token = token
            log(f"{serial} stream sent")
            return True

        pool = ThreadPoolExecutor(max_workers=8)
        futs = [pool.submit(one, serial) for serial in serials]
        for fut in as_completed(futs):
            try:
                ok += 1 if fut.result() else 0
            except Exception:
                pass
        pool.shutdown(wait=False)
        self.after(0, lambda n=ok, total=len(serials): self._show_overlay(f"{year} sent {n}/{total}"))

    def _mark_stream(self, serial: str, year: str, ok: bool):
        self._stream_ok[serial] = bool(ok)
        now = time.monotonic()
        if now - getattr(self, "_stream_note_at", 0) < 1.0:
            return
        self._stream_note_at = now
        token = self._year_token.get(year, 0)

        def paint():
            ctrls = [c for c in self._controllers.values() if c.year == year]
            good = sum(1 for c in ctrls if c.stream_token == token)
            self._show_overlay(f"{year} {good}/{len(ctrls)}")

        try:
            self.after(0, paint)
        except Exception:
            pass

    def _remember_years(self):
        """Stamp 2024 or 2025 on every phone before a stream send or a tap."""
        from core.match import model_year
        changed = False
        try:
            from core.panda import phones
            for phone in phones():
                serial = phone.get("serial") or ""
                year = model_year(phone.get("model") or "") or self._years.get(serial) or ""
                if serial and year in ("2024", "2025") and self._years.get(serial) != year:
                    self._years[serial] = year
                    changed = True
        except Exception:
            pass
        if changed:
            self._save_years()
        for serial, ctl in list(self._controllers.items()):
            year = self._years.get(serial) or ""
            if year in ("2024", "2025"):
                ctl.year = year

    def _sync_controllers(self):
        from core.phone_controller import PhoneController
        try:
            rows = self._adb_states()
        except Exception as exc:
            log(f"controller list failed {exc}")
            return
        self._remember_years()
        with self._ctrl_lock:
            for serial, state in rows:
                if state == "unauthorized":
                    continue
                if serial not in self._controllers:
                    self._controllers[serial] = PhoneController(self, serial)
                    log(f"controller {serial}")
            running = any(self._year_on.values())
            for ctl in self._controllers.values():
                year = ctl.year
                pending_stream = bool(self._year_url.get(year)) and ctl.stream_token != self._year_token.get(year, 0)
                pending_close = ctl.want_close != ctl.did_close
                if running or (not year) or pending_stream or pending_close:
                    ctl.poke()
            n = len(self._controllers)
            n2024 = sum(1 for c in self._controllers.values() if c.year == "2024")
            n2025 = sum(1 for c in self._controllers.values() if c.year == "2025")
            stamp = (len(rows), n, n2024, n2025, running)
            if stamp != getattr(self, "_farm_stamp", None):
                self._farm_stamp = stamp
                log(f"controllers {n} adb {len(rows)} 2024 {n2024} 2025 {n2025}")
                try:
                    self.after(0, lambda count=n, go=running: self._paint_controllers(count, go))
                except Exception:
                    pass

    def _note_click(self, text: str, tapped: bool = False):
        """Say it on the window. A tap that only hits the log file looks like nothing happened."""
        log("click " + text)
        if tapped:
            self._taps_sent = getattr(self, "_taps_sent", 0) + 1
        now = time.monotonic()
        quiet = text.startswith("looking") or text.startswith("no element")
        show = (not quiet) and (tapped or now - getattr(self, "_click_note_at", 0) > 0.8)
        if show:
            self._click_note_at = now

        def paint(show=show, text=text):
            n = len(getattr(self, "_controllers", {}) or {})
            taps = getattr(self, "_taps_sent", 0)
            word = "clicking" if any(self._year_on.values()) else "idle"
            self.drag_zone.configure(text=f"{n} {word} · {taps} taps")
            if show:
                self._show_overlay(text[:72])

        try:
            self.after(0, paint)
        except Exception:
            pass

    def _click_look(self, serial: str) -> str:
        """This phone only. Look, tap one element, look again before the next press."""
        from core.hierarchy import parse_hierarchy_xml
        from core.match import clicks_for_device
        if not any(self._year_on.values()):
            return "idle"
        if (
            serial
            and serial == (getattr(self.device, "serial", "") or "")
            and time.monotonic() < getattr(self, "_train_until", 0)
        ):
            return "idle"
        rules = self._all_rules()
        if not rules:
            self._note_click("no rules")
            return "idle"
        short = serial[-4:]
        self._note_click(f"looking {short}")
        try:
            xml = self._dump_serial(serial)
        except Exception as exc:
            self._why_set(serial, "could not see the screen")
            reason = " ".join(str(exc).split())[:36] or "failed"
            if self._tap_saved(serial):
                self._note_click(f"tap {short} saved", tapped=True)
                return "hit"
            self._note_click(f"look failed {short} {reason}")
            log(f"{serial} look failed {exc}")
            return "idle"
        root = parse_hierarchy_xml(xml)
        family = []
        try:
            family = list(self.device.identity(serial).get("family") or [])
        except Exception:
            family = []
        taps, seen = clicks_for_device(
            self.tagger, rules, root, family, (), serial=serial, hold=None,
        )
        pairs = list(zip(seen, taps))
        if not pairs:
            self._note_click(f"no element {short}")
            return "idle"
        tag, (x, y) = pairs[0]
        if not any(self._year_on.values()):
            return "idle"
        try:
            self._tap_serial(serial, x, y)
        except Exception as exc:
            self._why_set(serial, "tap was not sent")
            self._note_click(f"tap failed {short}")
            log(f"{serial} tap failed {exc}")
            return "miss"
        self._note_click(f"tap {short} {int(x)} {int(y)}", tapped=True)
        if not getattr(self, "fast", False):
            time.sleep(0.2)
        if not any(self._year_on.values()):
            return "idle"
        try:
            xml2 = self._dump_serial(serial)
        except Exception as exc:
            self._why_set(serial, "could not check the tap")
            log(f"{serial} recheck failed {exc}")
            return "miss"
        root2 = parse_hierarchy_xml(xml2)
        _again, seen2 = clicks_for_device(
            self.tagger, rules, root2, [], (), serial=serial, hold=None,
        )
        if tag in seen2:
            return "miss"
        return "hit"

    def _tap_saved(self, serial: str) -> bool:
        """The screen could not be read. Tap the point that was saved with the rule."""
        for rule in self._all_rules():
            for cond in rule.get("conditions") or []:
                tag = cond.get("tag") or ""
                exact = (self.tagger.tags.get(tag) or {}).get("exact") or {}
                bounds = exact.get("bounds") or []
                if len(bounds) != 4:
                    continue
                x = int((bounds[0] + bounds[2]) / 2)
                y = int((bounds[1] + bounds[3]) / 2)
                try:
                    self._tap_serial(serial, x, y)
                except Exception:
                    return False
                return True
        return False

    def _deliver_stream(self, serial: str, url: str) -> bool:
        try:
            self._open_on_phone(serial, url)
        except Exception as exc:
            log(f"{serial} stream failed {exc}")
            return False
        time.sleep(1.5)
        return self._stream_is_open(serial, url)

    def _stream_is_open(self, serial: str, url: str) -> bool:
        from core.stream_link import stream_reached
        try:
            data = self._adb_call(serial, ["shell", "dumpsys", "activity", "top"], timeout=6)
        except Exception:
            return False
        return stream_reached(data.decode("utf-8", "replace"), url)

    def _adb_try(self, serial: str, args, timeout: float = 4):
        try:
            self._adb_call(serial, args, timeout=timeout)
        except Exception:
            return

    def _open_on_phone(self, serial: str, url: str):
        """Wake a dark or swipe-locked phone, then open the stream."""
        from core.device import screen_locked

        self._phone_url = getattr(self, "_phone_url", {})
        self._phone_url[serial] = url
        self._adb_try(serial, ["shell", "input", "keyevent", "KEYCODE_WAKEUP"], timeout=4)
        self._adb_try(serial, ["shell", "settings", "put", "global", "stay_on_while_plugged_in", "7"], timeout=4)
        try:
            dump = self._adb_call(serial, ["shell", "dumpsys", "window"], timeout=5)
            locked = screen_locked(dump.decode("utf-8", "replace"))
        except Exception:
            locked = True
        if locked:
            self._adb_try(serial, ["shell", "wm", "dismiss-keyguard"], timeout=4)
            size = ()
            try:
                size = self.device.wm_for(serial)
            except Exception:
                size = ()
            width, height = size[0] if size else (720, 1600)
            x = max(1, int(width) // 2)
            y1 = int(height * 0.86)
            y2 = int(height * 0.22)
            self._adb_try(
                serial,
                ["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "180"],
                timeout=4,
            )
        self._adb_call(
            serial,
            ["shell", "am", "start",
             "-a", "android.intent.action.VIEW",
             "-c", "android.intent.category.BROWSABLE",
             "-d", url,
             "-p", "com.whatnot_mobile",
             "-f", "0x10008000"],
            timeout=10,
        )

    def _whatnot_tick(self):
        from core.whatnot_live import fetch_live
        try:
            fresh = fetch_live([n for n in self.guide_slots if n])
            live = [name for name, info in fresh.items() if info.get("online")]
            log("whatnot live " + (", ".join(live) if live else "none"))
            status = dict(self._whatnot_status)
            for name, info in fresh.items():
                if info.get("online"):
                    self._whatnot_misses[name] = 0
                    status[name] = info
                    continue
                misses = self._whatnot_misses.get(name, 0) + 1
                self._whatnot_misses[name] = misses
                # Whatnot often answers with an empty list for a seller who is live.
                # Only drop a green button after three polls that all came back empty.
                if misses >= 3 or not (status.get(name) or {}).get("online"):
                    status[name] = info
            self._whatnot_status = status
        except Exception as exc:
            log(f"whatnot check failed {exc}")
        self._whatnot_busy = False

    def _fullscreen_tick(self):
        if not getattr(self, "_full_busy", False):
            self._full_busy = True
            threading.Thread(target=self._fullscreen_round, daemon=True).start()
        self.after(300000, self._fullscreen_tick)

    def _fullscreen_round(self):
        """Hide the status and nav bars in Whatnot when a phone has left fullscreen."""
        want = "immersive.full=com.whatnot_mobile"
        try:
            serials = self._adb_serials()
        except Exception as exc:
            log(f"fullscreen list failed {exc}")
            self._full_busy = False
            return
        for serial in serials:
            try:
                cur = self._adb_call(serial, ["shell", "settings", "get", "global", "policy_control"], timeout=4)
                text = cur.decode("utf-8", "replace").strip()
                if want in text:
                    continue
                self._adb_call(serial, ["shell", "settings", "put", "global", "policy_control", want], timeout=4)
                log(f"{serial} fullscreen on")
            except Exception as exc:
                log(f"{serial} fullscreen failed {exc}")
        self._full_busy = False

    def _streamer_info(self, name: str) -> dict:
        board = getattr(self, "board", None)
        if not board or not name:
            return {}
        return dict(board.snapshot().get(name) or {})

    def _paint_streamers(self):
        from core.whatnot_live import button_text
        snap = self.board.snapshot() if getattr(self, "board", None) else {}
        live = {}
        for i, btn in enumerate(getattr(self, "stream_btns", [])):
            name = self.guide_slots[i] if i < len(self.guide_slots) else ""
            info = dict(snap.get(name) or {}) if name else {}
            if name and info.get("online") and info.get("url"):
                live[name] = info["url"]
            online = bool(name and info.get("online") and info.get("url"))
            btn.configure(
                text=button_text(name, info if name else None),
                fg_color=GREEN if online else RED,
                hover_color="#28b34a" if online else "#d32b22",
            )
        if getattr(self, "chromium", None):
            self.chromium.sync_live(live)
        self.after(1000, self._paint_streamers)

    def _close(self):
        self._year_on = {"2024": False, "2025": False}
        self._pause_hier = True
        try:
            if getattr(self, "board", None):
                self.board.close()
        except Exception:
            pass
        try:
            if self.chromium:
                self.chromium.close()
        except Exception:
            pass
        try:
            self.device.stop_live()
        except Exception:
            pass
        self.destroy()

    def _initial_load(self):
        threading.Thread(target=self._boot_device, daemon=True).start()

    def _boot_device(self):
        msg = self.device.connect()
        self.after(0, lambda m=msg: self._after_connect(m))

    def _after_connect(self, msg: str):
        short = msg.split("·")[0].strip() if "·" in msg else msg
        self._show_overlay(short)
        self._show_current()

    def _start_detect(self):
        self._detect_job = self.after(2000, self._detect_tick)

    def _detect_tick(self):
        if not self._detect_busy:
            self._detect_busy = True
            threading.Thread(target=self._detect_worker, daemon=True).start()
        self._detect_job = self.after(2000, self._detect_tick)

    def _paint_controllers(self, count: int, clicking: bool):
        word = "clicking" if clicking else "idle"
        self.drag_zone.configure(text=f"{count} controllers {word}")

    def _detect_worker(self):
        try:
            self._sync_controllers()
        except Exception as exc:
            log(f"controller sync failed {exc}")
        if any(self._year_on.values()):
            self._detect_busy = False
            return
        try:
            pairs = self.device.list_devices()
        except Exception:
            pairs = []
        self._detect_busy = False
        sig = tuple(pairs)
        if sig == self._dev_sig:
            return
        self._dev_sig = sig
        try:
            self.after(0, lambda p=list(pairs): self._apply_devices(p))
        except Exception:
            pass

    def _apply_devices(self, pairs):
        mapping = {}
        changed = False
        for serial, year in pairs:
            if year in ("2024", "2025"):
                if self._years.get(serial) != year:
                    self._years[serial] = year
                    changed = True
                if year not in mapping:
                    mapping[year] = serial
        if changed:
            self._save_years()
        self._serial_by_year = mapping
        self._paint_years()
        serial = mapping.get(self._active_year)
        if serial and serial != self.device.serial:
            self._on_device_pick(self._active_year)

    def _load_years(self) -> dict:
        path = app_dir() / "data" / "years.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {str(k): v for k, v in data.items() if v in ("2024", "2025")}

    def _save_years(self):
        path = app_dir() / "data" / "years.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._years), encoding="utf-8")
        except Exception:
            pass

    def _phone_year(self, serial: str) -> str:
        from core.match import model_year
        remembered = self._years.get(serial) or ""
        if remembered in ("2024", "2025"):
            return remembered
        try:
            info = self.device.identity(serial)
        except Exception:
            info = {}
        year = model_year(info.get("label") or "", *(info.get("family") or []))
        if year in ("2024", "2025"):
            if remembered != year:
                self._years[serial] = year
                self._save_years()
            return year
        return remembered if remembered in ("2024", "2025") else ""

    def _serials_for_years(self, years):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        wanted = set(years)
        serials = self._adb_serials()
        if not serials:
            return []
        picked = []
        pool = ThreadPoolExecutor(max_workers=4)
        futs = {pool.submit(self._phone_year, serial): serial for serial in serials}
        for fut in as_completed(futs):
            serial = futs[fut]
            try:
                got = fut.result()
            except Exception:
                got = ""
            if got in wanted:
                picked.append(serial)
            else:
                label = (self.device._identity.get(serial) or {}).get("label") or serial
                log(f"skip {serial} year={got or '-'} {label}")
        pool.shutdown(wait=True)
        return picked

    def _pick_year(self, year: str):
        self._active_year = year
        self._paint_years()
        self._paint_run_btn()
        if not self._serial_by_year.get(year):
            self._show_overlay(f"No {year}")
            return
        self._on_device_pick(year)

    def _paint_years(self):
        for year, btn in getattr(self, "year_btns", {}).items():
            on = self._year_on.get(year, False)
            selected = year == self._active_year
            btn.configure(
                text=f"{year} ON" if on else year,
                fg_color=(RED if selected else "#1e4d2b") if on else (BLUE if selected else SURFACE2),
                hover_color="#d32b22" if on else ("#0066cc" if selected else "#3a3a3c"),
            )

    def _paint_run_btn(self):
        if self._year_on.get(self._active_year):
            self.run_btn.configure(text="STOP", fg_color=RED, hover_color="#d32b22")
        else:
            self.run_btn.configure(text="START", fg_color=BLUE, hover_color="#0066cc")

    def _on_device_pick(self, year: str):
        serial = self._serial_by_year.get(year)
        if not serial or serial == self.device.serial:
            return
        self._pause_hier = True
        self._tree_sig_val = None
        self._mapped_scale = None
        self._xml_hash = None
        self.current_element = None
        self.root_element = None
        self.phone_view.set_elements([])
        self.phone_view.clear_selection()
        self._release_input_grab()
        self._show_overlay(year)
        threading.Thread(target=self._switch_device, args=(serial,), daemon=True).start()

    def _switch_device(self, serial: str):
        try:
            self.device.select_serial(serial)
        except Exception as e:
            self._pause_hier = False
            try:
                self.after(0, lambda: self._show_overlay(str(e)[:30]))
            except Exception:
                pass
            return
        try:
            self.after(0, self._after_switch)
        except Exception:
            self._pause_hier = False

    def _release_input_grab(self):
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.focus_force()
        except Exception:
            pass

    def _after_switch(self):
        self._pause_hier = False
        serial = self.device.serial
        if serial:
            self.device.family = list(self.device.identity(serial).get("family") or [])
        self.current_element = None
        self.root_element = None
        self._tree_sig_val = None
        self._mapped_scale = None
        self._show_current()
        if self.device.last_hierarchy is None:
            threading.Thread(target=self._dump_after_switch, daemon=True).start()
        self._release_input_grab()
        self._raise_controls()
        self.after(250, self._raise_controls)
        log(f"switch {serial} elements={'yes' if self.device.last_hierarchy is not None else 'no'}")

    def _dump_after_switch(self):
        serial = self.device.serial
        try:
            xml = self.device._dump_xml()
            if not xml or self.device.serial != serial:
                return
            from core.hierarchy import parse_hierarchy_xml
            root = parse_hierarchy_xml(xml)
            self.device.last_hierarchy = root
            self.after(0, lambda r=root, s=serial: self._apply_if_same_device(r, s))
            log(f"switch dump {serial}")
        except Exception as exc:
            log(f"switch dump failed {exc}")

    def _show_current(self):
        img = self.device.last_screenshot
        if img is not None:
            self.phone_view.set_frame(img)
        if self.device.last_hierarchy is not None:
            self._apply_hierarchy(self.device.last_hierarchy)
        self._raise_controls()

    def _start_live(self):
        self.device.start_live(self._on_live_frame)
        self._hier_job = self.after(1500, self._hier_tick)

    def _stop_live(self):
        self.device.stop_live()
        if self._hier_job:
            self.after_cancel(self._hier_job)
            self._hier_job = None

    def _on_live_frame(self, img):
        if self._frame_pending:
            return
        self._frame_pending = True
        try:
            self.after(0, self._consume_frame)
        except Exception:
            self._frame_pending = False

    def _consume_frame(self):
        """One picture at a time. Do not queue a new frame until this one is drawn."""
        try:
            img = self.device.last_screenshot
            if img is None:
                return
            self.phone_view.set_frame(img)
            if self.root_element is None and self.device.last_hierarchy is not None:
                self._apply_hierarchy(self.device.last_hierarchy)
            elif self.root_element is not None:
                scales = self._display_scales(self.root_element)
                if scales != self._mapped_scale:
                    self._apply_hierarchy(self.root_element)
            now = time.monotonic()
            if now - getattr(self, "_raised_at", 0) > 2:
                self._raised_at = now
                self._raise_controls()
        finally:
            self._frame_pending = False

    def _hier_tick(self):
        if (
            self.mirror_on
            and not self.device.demo_mode
            and not self._hier_busy
            and not self._pause_hier
            and self.device.device is not None
        ):
            self._hier_busy = True
            threading.Thread(target=self._hier_worker, daemon=True).start()
        if self.mirror_on:
            self._hier_job = self.after(2000, self._hier_tick)

    def _hier_worker(self):
        try:
            if self._pause_hier or not self.device.device:
                return
            serial = self.device.serial
            xml = self.device._dump_xml()
            if not xml or self._pause_hier or self.device.serial != serial:
                return
            xml_hash = hashlib.sha1(
                xml.encode("utf-8", "replace") if isinstance(xml, str) else xml
            ).hexdigest()
            if xml_hash == self._xml_hash:
                return
            from core.hierarchy import parse_hierarchy_xml
            root = parse_hierarchy_xml(xml)
            sig = self._tree_sig(root)
            self._xml_hash = xml_hash
            if sig == self._tree_sig_val:
                return
            self.device.last_hierarchy = root
            self.after(0, lambda r=root, s=serial: self._apply_if_same_device(r, s))
        except Exception:
            pass
        finally:
            self._hier_busy = False

    def _apply_if_same_device(self, root, serial: str):
        if self.device.serial != serial:
            return
        self._apply_hierarchy(root)

    def _tree_sig(self, root: UIElement) -> str:
        digest = hashlib.sha1()
        for el in flatten_elements(root):
            raw = getattr(el, "_raw_bounds", None) or el.bounds
            l, t, r, b = raw
            digest.update(
                f"{el.class_name}|{el.resource_id}|{el.text}|{el.content_desc}|"
                f"{l},{t},{r},{b}|{int(el.clickable)}\n".encode("utf-8", "replace")
            )
        return digest.hexdigest()

    def _raise_controls(self):
        """Keep CTk's rounded canvases behind the mirror and the buttons."""
        for frame in (self, self.mirror_frame, self.phone_view, self.bar, self.header, self._bar_inner, self.url_bar, self.slot_bar):
            bg = getattr(frame, "_canvas", None)
            if bg is None or bg is self.phone_view.canvas:
                continue
            try:
                bg.lower()
            except Exception:
                pass
        for widget in self._guard_widgets():
            try:
                widget.lift()
            except Exception:
                pass
        try:
            self.phone_view.canvas.lift()
        except Exception:
            pass
        for widget in self._guard_widgets():
            try:
                widget.lift()
            except Exception:
                pass
        self._bind_mirror_clicks()

    def _guard_widgets(self):
        return (
            self.add_btn, self.run_btn, self.reset_btn, self.apps_btn, self.close_btn,
            *self.year_btns.values(), self.url_entry, *self.stream_btns,
        )

    def _keep_clicks_on_mirror(self):
        self._raise_controls()

    def _owned_by(self, widget, owner) -> bool:
        while widget is not None:
            if widget == owner:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _inside(self, widget, x_root: int, y_root: int) -> bool:
        try:
            left, top = widget.winfo_rootx(), widget.winfo_rooty()
            return left <= x_root <= left + widget.winfo_width() and top <= y_root <= top + widget.winfo_height()
        except Exception:
            return False

    def _bind_mirror_clicks(self):
        """The click path that selected elements: screen position to the picture."""
        def press(event):
            widget = getattr(event, "widget", None)
            if widget is self.overlay:
                return
            buttons = self._guard_widgets()
            if widget is not None and any(self._owned_by(widget, btn) for btn in buttons):
                return
            canvas = self.phone_view.canvas
            on_mirror = widget is canvas or self._owned_by(widget, self.mirror_frame) or self._owned_by(widget, self.phone_view)
            over = False
            try:
                over = self._inside(canvas, event.x_root, event.y_root)
            except Exception:
                over = False
            if not over and widget is not None:
                try:
                    over = self._inside(canvas, event.x + widget.winfo_rootx(), event.y + widget.winfo_rooty())
                except Exception:
                    over = False
            if not on_mirror and not over:
                return
            if widget is not canvas:
                try:
                    event.x = float(event.x_root) - canvas.winfo_rootx()
                    event.y = float(event.y_root) - canvas.winfo_rooty()
                    event.widget = canvas
                except Exception:
                    return
            self.phone_view._on_press(event)

        def release(event):
            try:
                x_root, y_root = event.x_root, event.y_root
            except Exception:
                return
            if self._inside(self.add_btn, x_root, y_root) and not self._owned_by(event.widget, self.add_btn):
                self._quick_rule()
                return "break"
            if self._inside(self.run_btn, x_root, y_root) and not self._owned_by(event.widget, self.run_btn):
                self._toggle_rules()
                return "break"
            if self._inside(self.reset_btn, x_root, y_root):
                self._reset_rules()
                return "break"
            if self._inside(self.apps_btn, x_root, y_root):
                self._close_apps()
                return "break"
            for year, btn in self.year_btns.items():
                if self._inside(btn, x_root, y_root) and not self._owned_by(event.widget, btn):
                    self._pick_year(year)
                    return "break"
            if self._inside(self.close_btn, x_root, y_root) and not self._owned_by(event.widget, self.close_btn):
                self.destroy()
                return "break"
            for i, btn in enumerate(self.stream_btns):
                if self._inside(btn, x_root, y_root):
                    self._streamer_click(i)
                    return "break"

        self._press_select = press
        if not getattr(self, "_press_bound", False):
            self.bind_all("<Button-1>", press)
            self.bind_all("<ButtonRelease-1>", release)
            self.bind_class("Canvas", "<Button-1>", press, add="+")
            self._press_bound = True

        skip = set(self._guard_widgets())

        def walk(widget):
            for child in widget.winfo_children():
                if child in skip or any(self._owned_by(child, btn) for btn in skip):
                    continue
                try:
                    if child.winfo_class() == "Canvas" and child is not self.phone_view.canvas:
                        child.bind("<Button-1>", press)
                except Exception:
                    pass
                walk(child)

        walk(self)
        try:
            self.phone_view.canvas.bind("<Button-1>", self.phone_view._on_press)
        except Exception:
            pass

    def _display_scales(self, root: UIElement):
        """Map dump pixels onto the screenshot using the root box, one scale for both axes."""
        full = getattr(self.device, "display_full_size", None) or (0, 0)
        capture = getattr(self.device, "capture_size", None) or full
        px = float(getattr(self.device, "px_scale", 1) or 1)
        raw = getattr(root, "_raw_bounds", None) or root.bounds
        root_w = max(1, int(raw[2] - raw[0]))
        cap_w = int(capture[0] or full[0] or 0)
        if root_w > 50 and cap_w > 50:
            scale = px * (cap_w / float(root_w))
            return (scale, scale)
        return (px, px)

    def _apply_hierarchy(self, root: UIElement):
        sig = self._tree_sig(root)
        scales = self._display_scales(root)
        if sig == self._tree_sig_val and scales == self._mapped_scale:
            self._keep_clicks_on_mirror()
            return
        self._tree_sig_val = sig
        self._mapped_scale = scales
        sx, sy = scales
        view_h = 10 ** 9
        if self.device.last_screenshot is not None:
            view_h = self.device.last_screenshot.size[1]
        visible = []
        for el in flatten_elements(root):
            raw = getattr(el, "_raw_bounds", None)
            if raw is None:
                el._raw_bounds = el.bounds
                raw = el.bounds
            l, t, r, b = raw
            el.bounds = (
                int(round(l * sx)), int(round(t * sy)),
                int(round(r * sx)), int(round(b * sy)),
            )
            if el.bounds[1] >= view_h:
                continue
            key = (
                el.resource_id or "",
                el.text or "",
                el.content_desc or "",
                el.class_name or "",
            )
            if key in self._forgotten:
                continue
            visible.append(el)
        self.root_element = root
        self.tagger.apply_tags_to_tree(
            root,
            family=getattr(self.device, "family", None),
            serial=self.device.serial,
        )
        self.phone_view.set_elements(visible)
        self._drop_huge_selection()
        self._keep_clicks_on_mirror()

    def _drop_huge_selection(self):
        """A page-sized box is never a selection, even if a rule points at it."""
        bounds = None
        if self.current_element is not None:
            bounds = self.current_element.bounds
        elif getattr(self.phone_view, "locked_bounds", None):
            bounds = self.phone_view.locked_bounds
        shot = getattr(self.device, "last_screenshot", None)
        if not bounds or shot is None:
            return
        iw, ih = shot.size
        if iw < 20 or ih < 20:
            return
        l, t, r, b = bounds
        w, h = r - l, b - t
        if w * h > iw * ih * 0.18 or h > ih * 0.42 or w > iw * 0.9:
            self.current_element = None
            self.phone_view.clear_selection()

    def _on_element_selected(self, el: UIElement):
        if el is None:
            self.current_element = None
            self._show_overlay("Cleared")
            return
        self.current_element = el
        self._train_until = time.monotonic() + 12
        raw = getattr(el, "_raw_bounds", None) or el.bounds
        kind = (el.class_name or "Element").split(".")[-1]
        ident = (el.resource_id.split("/")[-1] if el.resource_id else "") or el.text or el.content_desc or "no id"
        shown = el.text or el.content_desc or ""
        self._show_overlay(
            f"{kind}\n{ident}\n{shown}\n[{raw[0]},{raw[1]}][{raw[2]},{raw[3]}]",
            ms=4000,
        )

    def _show_overlay(self, text: str, ms: int = 1600):
        self.overlay.configure(text=text or "")
        self.overlay.place(relx=0.5, rely=0.9, anchor="center")
        self.overlay.lift()
        if self._overlay_job:
            self.after_cancel(self._overlay_job)
        self._overlay_job = self.after(ms, self.overlay.place_forget)

    def _forget_current(self):
        if not self.current_element:
            self._show_overlay("Select element")
            return
        key = (
            self.current_element.resource_id or "",
            self.current_element.text or "",
            self.current_element.content_desc or "",
            self.current_element.class_name or "",
        )
        self._forgotten.add(key)
        if getattr(self.current_element, "tag", None):
            self.tagger.remove_tag(self.current_element.tag)
        self.current_element = None
        self.phone_view.clear_selection()
        self._tree_sig_val = None
        if self.root_element is not None:
            self._apply_hierarchy(self.root_element)
        self._show_overlay("Forgotten")

    def _quick_rule(self):
        if not self.current_element:
            self._show_overlay("Select element")
            return
        name = str(random.randint(1000, 9999))
        family = list(getattr(self.device, "family", []) or [])
        if self.device.serial and not family:
            family = list(self.device.identity(self.device.serial).get("family") or [])
            self.device.family = family
        raw = getattr(self.current_element, "_raw_bounds", None) or self.current_element.bounds
        root_raw = getattr(self.root_element, "_raw_bounds", None) if self.root_element else None
        if root_raw:
            sw, sh = max(int(root_raw[2]), 1), max(int(root_raw[3]), 1)
        else:
            cap = getattr(self.device, "capture_size", None) or (1, 1)
            sw, sh = max(int(cap[0]), 1), max(int(cap[1]), 1)
        norm = [
            ((raw[0] + raw[2]) / 2) / sw,
            ((raw[1] + raw[3]) / 2) / sh,
        ]
        self.current_element._trained_serial = self.device.serial or ""
        self.tagger.add_tag(
            name, self.current_element, family=family, norm=norm, screen=[sw, sh]
        )
        self.current_element.tag = name
        self.phone_view.set_selected(self.current_element)
        self.phone_view._rebuild_tag_boxes()
        self.rules.add_rule({
            "name": f"Click {name}",
            "enabled": True,
            "family": family,
            "serial": self.device.serial or "",
            "conditions": [{"type": "element_visible", "tag": name}],
            "actions": [{"type": "click", "tag": name}],
        })
        raw = getattr(self.current_element, "_raw_bounds", None) or self.current_element.bounds
        n = len(self._all_rules())
        controllers = len(self._controllers)
        log(
            f"ADD {name} rules={n} {self.current_element.class_name} "
            f"id={self.current_element.resource_id or '-'} "
            f"text={self.current_element.text or '-'} "
            f"bounds={list(raw)}"
        )
        if any(self._year_on.values()):
            for ctl in self._controllers.values():
                ctl.poke()
            self._show_overlay(f"Rule {n} · clicking {controllers}")
        else:
            self._show_overlay(f"Rule {n} saved · press START")

    def _reset_rules(self):
        count = self.rules.clear()
        self._year_on = {"2024": False, "2025": False}
        self._paint_run_btn()
        self._paint_years()
        if self._rules_job:
            try:
                self.after_cancel(self._rules_job)
            except Exception:
                pass
            self._rules_job = None
        self.tagger.tags = {}
        self.tagger.save()
        self._held = {}
        self.current_element = None
        self.phone_view.clear_selection()
        self._tree_sig_val = None
        if self.root_element is not None:
            self._apply_hierarchy(self.root_element)
        self._show_overlay(f"Cleared {count}")
        log(f"RESET {count}")

    def _close_apps(self):
        self._show_overlay("Closing")
        threading.Thread(target=self._close_apps_worker, daemon=True).start()

    def _close_apps_worker(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from core.phone_controller import _CLOSE
        from core.panda import shell
        self._sync_controllers()
        try:
            serials = [serial for serial, state in self._adb_states() if state == "device"]
        except Exception:
            serials = []
        if not serials:
            serials = list(self._controllers)
        def one(serial):
            if shell(serial, _CLOSE, timeout=12):
                log(f"{serial} apps closed")
                return
            try:
                self._adb_call(serial, ["shell", _CLOSE], timeout=20)
                log(f"{serial} apps closed")
            except Exception as exc:
                log(f"{serial} close failed {exc}")
        if serials:
            pool = ThreadPoolExecutor(max_workers=8)
            futs = [pool.submit(one, serial) for serial in serials]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception:
                    pass
            pool.shutdown(wait=False)
        self.after(0, lambda n=len(serials): self._show_overlay(f"Closed {n}"))

    def _any_rules(self) -> bool:
        return any(self._year_on.values())

    def _toggle_rules(self):
        running = any(self._year_on.values())
        self._year_on = {"2024": not running, "2025": not running}
        self._paint_run_btn()
        self._paint_years()
        if running:
            self._show_overlay("Stopped")
            self._paint_controllers(len(self._controllers), False)
            log("STOP")
            if self._rules_job:
                self.after_cancel(self._rules_job)
                self._rules_job = None
            return
        try:
            self._sync_controllers()
        except Exception:
            pass
        self._show_overlay(f"Clicking {len(self._controllers)}")
        log(f"START controllers={len(self._controllers)} rules={len(self._all_rules())}")
        if self._rules_job is None:
            self._rules_tick()

    def _rules_tick(self):
        if not self._any_rules():
            return
        try:
            self._sync_controllers()
        except Exception:
            pass
        for ctl in list(self._controllers.values()):
            ctl.poke()
        self._paint_controllers(len(self._controllers), True)
        self._rules_job = self.after(1200, self._rules_tick)

    def _rules_round(self):
        from concurrent.futures import wait
        hits = 0
        count = 0
        try:
            running = {year for year, on in self._year_on.items() if on}
            serials = self._serials_for_years(running)
            count = len(serials)
            with self._busy_lock:
                free = [s for s in serials if s not in self._busy_serials]
                self._busy_serials.update(free)
            if free:
                try:
                    futs = [self._pool.submit(self._fire_device, s) for s in free]
                except Exception:
                    with self._busy_lock:
                        self._busy_serials.difference_update(free)
                    raise
                done, _pending = wait(futs, timeout=12)
                for fut in done:
                    try:
                        hits += fut.result()
                    except Exception:
                        continue
        except Exception as exc:
            log(f"scan failed {exc}")
            hits = 0
        try:
            self.after(0, lambda h=hits, n=count: self._rules_round_done(h, n))
        except Exception:
            self._rule_busy = False

    def _adb_bin(self) -> str:
        import shutil
        return shutil.which("adb") or "adb"

    def _adb_flags(self) -> dict:
        import sys
        return {"creationflags": 0x08000000} if sys.platform == "win32" else {}

    def _adb_host(self, args, timeout: float = 20):
        from core.adb import call
        return call(list(args), timeout=timeout)

    def _adb_call(self, serial: str, args, timeout: float = 12):
        import subprocess
        try:
            return self._adb_host(["-s", serial, *args], timeout=timeout)
        except subprocess.TimeoutExpired:
            self._adb_bad.add(serial)
            self._why_set(serial, "adb timed out")
            raise
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", "replace").strip() or f"adb exit {exc.returncode}"
            self._adb_bad.add(serial)
            self._why_set(serial, err)
            raise
        except Exception as exc:
            self._adb_bad.add(serial)
            self._why_set(serial, str(exc) or "adb failed")
            raise

    def _all_rules(self):
        rules = []
        for entry in (self.rules.apps or {}).values():
            rules.extend((entry or {}).get("rules") or [])
        if not rules:
            rules = list(self.rules.rules)
        return [rule for rule in rules if rule.get("enabled", True)]

    def _tap_serial(self, serial: str, x: int, y: int):
        try:
            self._adb_call(serial, ["shell", "input", "tap", str(int(x)), str(int(y))], timeout=5)
        except Exception:
            from core.panda import shell
            if not shell(serial, f"input tap {int(x)} {int(y)}", timeout=4):
                raise
        log(f"{serial} tap {int(x)} {int(y)}")

    def _adb_states(self):
        rows = []
        seen = set()
        try:
            from core.adb import devices
            for serial, state in devices():
                if serial not in seen:
                    rows.append((serial, state))
                    seen.add(serial)
        except Exception as exc:
            log(f"adb list failed {exc}")
        try:
            from core.panda import phones
            for phone in phones():
                serial = phone.get("serial") or ""
                if serial and serial not in seen:
                    rows.append((serial, "device"))
                    seen.add(serial)
        except Exception:
            pass
        return rows

    def _why_set(self, serial: str, text: str):
        reason = " ".join(str(text or "").split())[:180]
        self._why[serial] = reason
        ctl = self._controllers.get(serial)
        if ctl is not None:
            ctl.reason = reason

    def _write_why(self):
        from core.paths import app_dir
        try:
            rows = self._adb_states()
        except Exception as exc:
            rows = []
            log(f"why list failed {exc}")
        seen = set()
        lines = ["serial\tyear\tstate\treason"]
        for serial, state in rows:
            seen.add(serial)
            ctl = self._controllers.get(serial)
            year = (ctl.year if ctl else "") or "-"
            reason = self._why.get(serial) or (ctl.reason if ctl else "") or ""
            if state != "device":
                reason = (state + " " + reason).strip()
            lines.append(f"{serial}\t{year}\t{state}\t{reason or 'connected'}")
        for serial, ctl in self._controllers.items():
            if serial in seen:
                continue
            lines.append(f"{serial}\t{ctl.year or '-'}\tmissing\t{self._why.get(serial) or ctl.reason or 'not in adb'}")
        path = app_dir() / "data" / "why.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:
            log(f"why write failed {exc}")
            return
        bad = [line.split("\t")[0][-4:] for line in lines[1:] if line.split("\t")[-1] not in ("connected", "on stream", "tap landed", "watching")]
        if bad:
            self.after(0, lambda n=len(bad): self._show_overlay(f"{n} phones need a look"))
        log(f"why file {path}")

    def _adb_serials(self):
        return [serial for serial, state in self._adb_states() if state == "device"]

    def _adb_refresh_tick(self):
        if not self._adb_refresh_busy:
            self._adb_refresh_busy = True
            threading.Thread(target=self._adb_refresh, daemon=True).start()
        self.after(60000, self._adb_refresh_tick)

    def _adb_alive(self, serial: str) -> bool:
        try:
            out = self._adb_host(["-s", serial, "get-state"], timeout=4)
            return out.decode("utf-8", "replace").strip() == "device"
        except Exception:
            return False

    def _adb_refresh(self):
        """Re-read the phones Panda already has. Do not restart adb."""
        try:
            self._sync_controllers()
        except Exception as exc:
            log(f"adb refresh failed {exc}")
        self._adb_refresh_busy = False

    def _xml_from_dump(self, data: bytes) -> str:
        text = data.decode("utf-8", "replace")
        start = text.find("<hierarchy")
        if start < 0:
            start = text.find("<?xml")
        if start < 0:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            picked = next((ln for ln in lines if "ERROR" in ln or "Exception" in ln), lines[-1] if lines else "empty")
            raise RuntimeError(" ".join(picked.split())[:80])
        return text[start:]

    def _dump_serial(self, serial: str) -> str:
        """Read the screen without /sdcard. That folder does not exist on these phones."""
        import subprocess
        if not hasattr(self, "_dump_slots"):
            self._dump_slots = threading.Semaphore(4)
        last = "no hierarchy"
        self._dump_slots.acquire()
        try:
            tries = [
                ["-s", serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
                ["-s", serial, "shell", "uiautomator", "dump", "/data/local/tmp/pei.xml"],
                ["-s", serial, "shell", "uiautomator", "dump", "/storage/emulated/0/pei.xml"],
            ]
            for args in tries:
                try:
                    data = self._adb_host(args, timeout=22)
                except subprocess.TimeoutExpired:
                    last = "timeout"
                    continue
                except subprocess.CalledProcessError as exc:
                    blob = (exc.output or b"") + b"\n" + (exc.stderr or b"")
                    try:
                        return self._xml_from_dump(blob)
                    except RuntimeError as err:
                        last = str(err)
                    continue
                path = args[-1]
                if path != "/dev/tty":
                    try:
                        note = data.decode("utf-8", "replace")
                    except Exception:
                        note = ""
                    if "ERROR" in note or "Exception" in note:
                        last = " ".join(note.split())[:80]
                        continue
                    try:
                        data = self._adb_host(["-s", serial, "exec-out", "cat", path], timeout=8)
                    except subprocess.CalledProcessError as exc:
                        last = " ".join(((exc.stderr or exc.output or b"dump failed").decode("utf-8", "replace")).split())[:80]
                        continue
                    except subprocess.TimeoutExpired:
                        last = "timeout"
                        continue
                try:
                    return self._xml_from_dump(data)
                except RuntimeError as err:
                    last = str(err)
            if serial and serial == getattr(self.device, "serial", "") and getattr(self.device, "device", None):
                try:
                    xml = self.device._dump_xml()
                    if xml:
                        return self._xml_from_dump(xml.encode("utf-8"))
                except Exception as exc:
                    last = " ".join(str(exc).split())[:80] or last
        finally:
            self._dump_slots.release()
        raise RuntimeError(last)

    def _rules_round_done(self, hits: int, count: int):
        self._rule_busy = False
        if not self._any_rules():
            return
        if count == 0:
            self._show_overlay("No devices")
        elif hits:
            self._show_overlay(f"{hits} clicks")
        log(f"scan phones={count} clicks={hits}")
        self._rules_job = self.after(1200, self._rules_tick)

    def _fire_device(self, serial: str) -> int:
        return 0


def main():
    app = CompactController()
    app.mainloop()
