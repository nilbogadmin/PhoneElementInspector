"""
Small bottom-right style controller.
Control bar and phone mirror share the same width.
Mirror only shows when exactly one device is connected.
"""
from __future__ import annotations
import random
import threading
from concurrent.futures import ThreadPoolExecutor
import customtkinter as ctk
from tkinter import messagebox
from typing import Optional
from pathlib import Path

from core.device import DeviceConnector
from core.tagger import Tagger
from core.rules import RulesEngine
from core.element import UIElement
from core.paths import app_dir
from gui.phone_view import PhoneView

BG = "#121214"
SURFACE = "#1c1c1e"
SURFACE2 = "#2c2c2e"
BLUE = "#0a84ff"
GREEN = "#30d158"
RED = "#ff453a"
SECONDARY = "#98989f"
LABEL = "#ffffff"

BAR_W = 300
BAR_H = 48
DROP_H = 36
PHONE_ASPECT = (19.5 / 9) * 0.5   # half height for top-half only


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
        self.rules.set_click_callback(lambda x, y: self.device.click(x, y))

        self.mirror_on = False
        self.current_element = None
        self.root_element = None
        self._forgotten = set()  # (resource_id, text, bounds) tuples to keep ignored
        self.root_element: Optional[UIElement] = None
        self._rules_job = None
        self._rules_running = False
        self._detect_job = None
        self._hier_job = None
        self._frame_pending = False
        self._drag_x = 0
        self._drag_y = 0
        self._serial_by_label: dict[str, str] = {}
        self._u2_cache: dict = {}
        self._u2_lock = threading.Lock()
        self._rule_busy = False
        self._pool = ThreadPoolExecutor(max_workers=24, thread_name_prefix="rules")

        self._build_ui()
        self._place_bottom_right()
        self.after(200, self._initial_load)
        self._start_detect()
        self._bind_drag()

    def _build_ui(self):
        # Phone mirror (always visible)
        self.mirror_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=16)

        menu_font = ctk.CTkFont(family="Poppins Condensed", size=12, weight="bold")
        self.device_menu = ctk.CTkOptionMenu(
            self.mirror_frame,
            values=["WAITING"],
            command=self._on_device_pick,
            height=28,
            fg_color=SURFACE2,
            button_color=SURFACE2,
            button_hover_color="#3a3a3c",
            dropdown_fg_color=SURFACE,
            font=menu_font,
        )
        self.device_menu.pack(fill="x", padx=8, pady=(8, 0))
        self.device_menu.set("WAITING")

        self.phone_view = PhoneView(
            self.mirror_frame,
            on_element_selected=self._on_element_selected,
            on_crop_changed=None,
        )
        self.phone_view.pack(fill="both", expand=True, padx=6, pady=6)
        self.phone_view.set_outline_mode(show_all=False, only_tagged=True)

        # Overlay info (one message at a time, centered on mirror)
        self.overlay = ctk.CTkLabel(
            self.mirror_frame, text="", text_color=LABEL,
            font=ctk.CTkFont(family="Poppins Condensed", size=13, weight="bold"),
            fg_color="#1c1c1e", corner_radius=6
        )
        self.overlay.place(relx=0.5, rely=0.92, anchor="center")
        self.overlay.lower()

        # Control bar – all buttons same size, no icons, all caps
        self.bar = ctk.CTkFrame(self, height=BAR_H, fg_color=SURFACE, corner_radius=16)
        self.bar.pack(fill="x", side="bottom", pady=(4, 0))
        self.bar.pack_propagate(False)

        inner = ctk.CTkFrame(self.bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=8, pady=6)

        btn_font = ctk.CTkFont(family="Poppins Condensed", size=11, weight="bold")
        btn_w, btn_h = 62, 26

        self.add_btn = ctk.CTkButton(inner, text="ADD", width=btn_w, height=btn_h, corner_radius=6,
                                     fg_color=GREEN, hover_color="#28b34a",
                                     command=self._quick_rule, font=btn_font)
        self.add_btn.pack(side="left", padx=2)

        self.run_btn = ctk.CTkButton(inner, text="START", width=btn_w, height=btn_h, corner_radius=6,
                                     fg_color=BLUE, hover_color="#0066cc",
                                     command=self._toggle_rules, font=btn_font)
        self.run_btn.pack(side="left", padx=2)

    def _place_bottom_right(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        mirror_h = int(BAR_W * PHONE_ASPECT)
        total_h = mirror_h + BAR_H + DROP_H + 12
        x = sw - BAR_W - 16
        y = sh - total_h - 48
        self.geometry(f"{BAR_W}x{total_h}+{x}+{y}")
        self.mirror_on = True
        self.mirror_frame.pack(fill="both", expand=True, before=self.bar)
        self._start_live()

    def _apply_geometry(self):
        # Keep current x/y, only change size
        geo = self.geometry()
        # parse +x+y if present
        if "+" in geo:
            pos = "+" + geo.split("+", 1)[1]
        else:
            pos = ""
        if self.mirror_on:
            mirror_h = int(BAR_W * PHONE_ASPECT)
            total_h = mirror_h + BAR_H + DROP_H + 12
            self.geometry(f"{BAR_W}x{total_h}{pos}")
            self.mirror_frame.pack(fill="both", expand=True, before=self.bar)
            self._start_live()
            # Re-anchor bottom so it grows upward
            self.update_idletasks()
            sh = self.winfo_screenheight()
            y = sh - total_h - 48
            x = self.winfo_x()
            self.geometry(f"{BAR_W}x{total_h}+{x}+{y}")
        else:
            self.geometry(f"{BAR_W}x{BAR_H}{pos}")
            self.mirror_frame.pack_forget()
            self._stop_live()

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
        self.bar.bind("<Button-1>", self._start_drag)
        self.bar.bind("<B1-Motion>", self._do_drag)

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _do_drag(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"+{x}+{y}")

    def _initial_load(self):
        msg = self.device.connect()
        self._show_overlay(msg.split("·")[0].strip() if "·" in msg else msg)
        self._refresh_view()

    def _start_detect(self):
        self._detect_job = self.after(2000, self._detect_tick)

    def _detect_tick(self):
        try:
            pairs = self.device.list_devices()
        except Exception:
            pairs = []
        labels = []
        mapping: dict[str, str] = {}
        used = {}
        for serial, model in pairs:
            label = model
            used[label] = used.get(label, 0) + 1
            if used[label] > 1:
                label = f"{model} {serial[-4:]}"
            # first one keeps the plain name; fix collision by rewriting if needed
            if label in mapping:
                label = f"{model} {serial[-4:]}"
            mapping[label] = serial
            labels.append(label)
        self._serial_by_label = mapping
        if not labels:
            self.device_menu.configure(values=["WAITING"])
            self.device_menu.set("WAITING")
        else:
            current = self.device_menu.get()
            self.device_menu.configure(values=labels)
            if current in labels:
                self.device_menu.set(current)
            else:
                # keep the same serial if it is still connected
                keep = next((lb for lb, s in mapping.items() if s == self.device.serial), labels[0])
                self.device_menu.set(keep)
                if self.device.serial != mapping.get(keep):
                    self._on_device_pick(keep)
        self._detect_job = self.after(2000, self._detect_tick)

    def _on_device_pick(self, label: str):
        serial = self._serial_by_label.get(label)
        if not serial or serial == self.device.serial:
            return
        try:
            self.device.select_serial(serial)
        except Exception as e:
            self._show_overlay(str(e)[:30])
            return
        self.current_element = None
        self._selected_key = None
        self.phone_view.locked_bounds = None
        self.phone_view.selected = None
        try:
            self.phone_view._draw_locked_outline()
        except Exception:
            pass
        self._show_overlay(label)

    def _start_live(self):
        self.device.start_live(self._on_live_frame)
        self._hier_job = self.after(4000, self._hier_tick)

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
        self._frame_pending = False
        self._refresh_view()

    def _hier_tick(self):
        if self.mirror_on and not self.device.demo_mode:
            try:
                self.device._refresh_real()
            except Exception:
                pass
        if self.mirror_on:
            self._hier_job = self.after(4000, self._hier_tick)

    def _refresh_view(self):
        if not self.device.last_screenshot or not self.device.last_hierarchy:
            return
        self.root_element = self.device.last_hierarchy
        self.tagger.apply_tags_to_tree(self.root_element)

        # Crop image to top 50%. Scale hierarchy bounds to this image once.
        img = self.device.last_screenshot
        w, h = img.size
        half_h = max(1, h // 2)
        top_half = img.crop((0, 0, w, half_h))

        from core.element import flatten_elements
        els = flatten_elements(self.root_element)
        scaled_to = getattr(self.root_element, "_scaled_to", None)
        if scaled_to != (w, h) and els:
            max_r = max((e.bounds[2] for e in els), default=w) or w
            max_b = max((e.bounds[3] for e in els), default=h) or h
            if max_r > w * 1.15 or max_b > h * 1.15:
                sx = w / max_r
                sy = h / max_b
                for el in els:
                    l, t, r, b = el.bounds
                    el.bounds = (int(l * sx), int(t * sy), int(r * sx), int(b * sy))
            self.root_element._scaled_to = (w, h)

        for el in els:
            l, t, r, b = el.bounds
            if t >= half_h:
                el.ignored = True
                continue
            key = (
                el.resource_id or "",
                el.text or "",
                el.content_desc or "",
                el.class_name or "",
            )
            if key in self._forgotten:
                el.ignored = True

        self.phone_view.set_data(
            top_half,
            self.root_element,
            saved_crop=None,
            preserve_selection=True,
        )

    def _on_element_selected(self, el: UIElement):
        self.current_element = el
        # Include bounds so empty elements don't all match
        self._selected_key = (
            el.resource_id or "",
            el.text or "",
            el.content_desc or "",
            el.class_name or "",
            str(el.bounds),
        )
        self.phone_view.set_selected(el)

    def _show_overlay(self, text: str):
        self.overlay.configure(text=text.upper())
        self.overlay.lift()
        self.after(1800, lambda: self.overlay.configure(text=""))

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
        self.phone_view.set_selected(None)
        self._show_overlay("Forgotten")
        self._refresh_view()

    def _quick_rule(self):
        if not self.current_element:
            messagebox.showinfo("Add", "Select an element on the mirror first")
            return
        name = str(random.randint(1000, 9999))
        self.tagger.add_tag(name, self.current_element)
        self.current_element.tag = name
        img = self.phone_view.base_image
        if img is not None:
            iw, ih = img.size
            l, t, r, b = self.current_element.bounds
            self.tagger.tags[name]["norm"] = [
                ((l + r) / 2) / max(iw, 1),
                ((t + b) / 2) / max(ih * 2, 1),
            ]
            self.tagger.save()
        self.phone_view.set_selected(self.current_element)
        self.rules.add_rule({
            "name": f"Click {name}",
            "enabled": True,
            "conditions": [{"type": "element_visible", "tag": name}],
            "actions": [{"type": "click", "tag": name}],
        })
        self._show_overlay(f"Added {name}")

    def _toggle_rules(self):
        if self._rules_running:
            self._stop_rules()
        else:
            self._start_rules()

    def _start_rules(self):
        self._rules_running = True
        self.run_btn.configure(text="STOP", fg_color=RED)
        self._show_overlay("Running")
        self._rules_tick()

    def _stop_rules(self):
        self._rules_running = False
        self.run_btn.configure(text="START", fg_color=BLUE)
        if self._rules_job:
            self.after_cancel(self._rules_job)
            self._rules_job = None
        self._show_overlay("Stopped")

    def _rules_tick(self):
        if not self._rules_running:
            return
        if self._rule_busy:
            self._rules_job = self.after(200, self._rules_tick)
            return
        self._rule_busy = True
        threading.Thread(target=self._rules_round, daemon=True).start()

    def _rules_round(self):
        hits = 0
        count = 0
        try:
            import adbutils
            serials = [d.serial for d in adbutils.adb.device_list()]
            count = len(serials)
            if serials:
                futs = [self._pool.submit(self._fire_device, s) for s in serials]
                for fut in futs:
                    try:
                        hits += fut.result(timeout=25)
                    except Exception:
                        continue
        except Exception:
            hits = 0
        try:
            self.after(0, lambda h=hits, n=count: self._rules_round_done(h, n))
        except Exception:
            self._rule_busy = False

    def _rules_round_done(self, hits: int, count: int):
        self._rule_busy = False
        if not self._rules_running:
            return
        if count == 0:
            self._show_overlay("No devices")
        elif hits:
            self._show_overlay(f"{hits} clicks")
        self._rules_job = self.after(200, self._rules_tick)

    def _fire_device(self, serial: str) -> int:
        import uiautomator2 as u2
        from core.hierarchy import parse_hierarchy_xml
        with self._u2_lock:
            d = self._u2_cache.get(serial)
        if d is None:
            d = u2.connect(serial)
            with self._u2_lock:
                self._u2_cache[serial] = d
        try:
            xml = d.dump_hierarchy()
        except Exception:
            with self._u2_lock:
                self._u2_cache.pop(serial, None)
            return 0
        root = parse_hierarchy_xml(xml)
        taps = []
        for rule in list(self.rules.rules):
            if not rule.get("enabled", True):
                continue
            for cond in rule.get("conditions", []):
                if cond.get("type") != "element_visible":
                    continue
                el = self.tagger.find_for_click(root, cond.get("tag"))
                if el is None:
                    continue
                x, y = el.center
                taps.append((int(x), int(y)))
        for x, y in taps:
            try:
                d.shell(f"input tap {x} {y}")
            except Exception:
                pass
        return len(taps)


def main():
    app = CompactController()
    app.mainloop()
