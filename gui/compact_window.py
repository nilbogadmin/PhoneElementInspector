"""
Small bottom-right style controller.
Control bar and phone mirror share the same width.
Mirror only shows when exactly one device is connected.
"""
from __future__ import annotations
import random
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
import customtkinter as ctk
from tkinter import messagebox
from typing import Optional

from core.device import DeviceConnector
from core.tagger import Tagger
from core.rules import RulesEngine
from core.element import UIElement, flatten_elements
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

BAR_W = 900
BAR_H = 84
DROP_H = 48
HEAD_H = 42
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
        self._hier_busy = False
        self._detect_busy = False
        self._pause_hier = False
        self._tree_sig_val = None
        self._xml_hash = None
        self._mapped_scale = None
        self._dev_sig = None
        self._overlay_job = None

        self._build_ui()
        self._place_center()
        self.after(200, self._initial_load)
        self._start_detect()
        self._bind_drag()

    def _build_ui(self):
        btn_font = ctk.CTkFont(family="Poppins Condensed", size=22, weight="bold")
        menu_font = ctk.CTkFont(family="Poppins Condensed", size=18, weight="bold")

        self.header = ctk.CTkFrame(self, height=HEAD_H, fg_color=SURFACE, corner_radius=12)
        self.header.pack(fill="x", side="top", pady=(0, 4))
        self.header.pack_propagate(False)
        self.drag_zone = ctk.CTkLabel(self.header, text="", fg_color="transparent")
        self.drag_zone.pack(side="left", fill="both", expand=True)
        self.close_btn = ctk.CTkButton(
            self.header, text="X", width=54, height=32, corner_radius=8,
            fg_color=RED, hover_color="#d32b22", command=self._close, font=btn_font,
        )
        self.close_btn.pack(side="right", padx=6, pady=4)

        self.mirror_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=16)

        self.device_menu = ctk.CTkOptionMenu(
            self.mirror_frame,
            values=["WAITING"],
            command=self._on_device_pick,
            height=40,
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

        self.overlay = ctk.CTkLabel(
            self.mirror_frame, text="", text_color=LABEL,
            font=ctk.CTkFont(family="Poppins Condensed", size=18, weight="bold"),
            fg_color="#1c1c1e", corner_radius=6, justify="center",
        )

        self.bar = ctk.CTkFrame(self, height=BAR_H, fg_color=SURFACE, corner_radius=16)
        self.bar.pack(fill="x", side="bottom", pady=(4, 0))
        self.bar.pack_propagate(False)

        self._bar_inner = ctk.CTkFrame(self.bar, fg_color="transparent")
        self._bar_inner.pack(fill="both", expand=True, padx=8, pady=6)

        btn_w, btn_h = 200, 60
        self.add_btn = ctk.CTkButton(
            self._bar_inner, text="ADD", width=btn_w, height=btn_h, corner_radius=10,
            fg_color=GREEN, hover_color="#28b34a",
            command=self._quick_rule, font=btn_font,
        )
        self.add_btn.pack(side="left", padx=6)

        self.run_btn = ctk.CTkButton(
            self._bar_inner, text="START", width=btn_w, height=btn_h, corner_radius=10,
            fg_color=BLUE, hover_color="#0066cc",
            command=self._toggle_rules, font=btn_font,
        )
        self.run_btn.pack(side="left", padx=6)

    def _window_height(self) -> int:
        mirror_h = int(BAR_W * PHONE_ASPECT)
        return mirror_h + BAR_H + DROP_H + HEAD_H + 28

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
        self._win_w = BAR_W
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

    def _close(self):
        self._rules_running = False
        self._pause_hier = True
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

    def _detect_worker(self):
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
        labels = []
        mapping: dict[str, str] = {}
        used = {}
        for serial, model in pairs:
            label = model
            used[label] = used.get(label, 0) + 1
            if used[label] > 1 or label in mapping:
                label = f"{model} {serial[-4:]}"
            mapping[label] = serial
            labels.append(label)
        self._serial_by_label = mapping
        if not labels:
            if self.device_menu.get() != "WAITING":
                self.device_menu.configure(values=["WAITING"])
                self.device_menu.set("WAITING")
            return
        current = self.device_menu.get()
        self.device_menu.configure(values=labels)
        if current in labels:
            self.device_menu.set(current)
            return
        keep = next((lb for lb, s in mapping.items() if s == self.device.serial), labels[0])
        self.device_menu.set(keep)
        if self.device.serial != mapping.get(keep):
            self._on_device_pick(keep)

    def _on_device_pick(self, label: str):
        serial = self._serial_by_label.get(label)
        if not serial or serial == self.device.serial:
            return
        self._pause_hier = True
        self._tree_sig_val = None
        self._mapped_scale = None
        self._xml_hash = None
        self.current_element = None
        self.phone_view.clear_selection()
        self._show_overlay(label)
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

    def _after_switch(self):
        self._pause_hier = False
        self._show_current()

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
        """Picture only. Element boxes stay until the hierarchy actually changes."""
        self._frame_pending = False
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
        self._raise_controls()

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
            xml = self.device.device.dump_hierarchy()
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
        for frame in (self, self.mirror_frame, self.phone_view, self.bar, self.header, self._bar_inner):
            bg = getattr(frame, "_canvas", None)
            if bg is None or bg is self.phone_view.canvas:
                continue
            try:
                bg.lower()
            except Exception:
                pass
        for widget in (self.add_btn, self.run_btn, self.close_btn, self.device_menu):
            try:
                widget.lift()
            except Exception:
                pass
        try:
            self.phone_view.canvas.lift()
        except Exception:
            pass
        for widget in (self.add_btn, self.run_btn, self.close_btn, self.device_menu):
            try:
                widget.lift()
            except Exception:
                pass
        self._bind_mirror_clicks()

    def _keep_clicks_on_mirror(self):
        self._raise_controls()

    def _owned_by(self, widget, owner) -> bool:
        while widget is not None:
            if widget == owner:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _over(self, widget, event) -> bool:
        """Hit test using one coordinate space. Do not mix x_root with winfo."""
        try:
            src = event.widget
            x = float(event.x) + src.winfo_rootx()
            y = float(event.y) + src.winfo_rooty()
            left, top = widget.winfo_rootx(), widget.winfo_rooty()
            return left <= x <= left + widget.winfo_width() and top <= y <= top + widget.winfo_height()
        except Exception:
            return False

    def _bind_mirror_clicks(self):
        """Any press on the mirror selects the element under it."""
        def press(event):
            for widget in (self.add_btn, self.run_btn, self.close_btn, self.device_menu):
                if self._over(widget, event):
                    return
            canvas = self.phone_view.canvas
            if not self._over(canvas, event):
                return
            src = getattr(event, "widget", None)
            try:
                if src is canvas:
                    x, y = float(event.x), float(event.y)
                else:
                    x = float(event.x) + src.winfo_rootx() - canvas.winfo_rootx()
                    y = float(event.y) + src.winfo_rooty() - canvas.winfo_rooty()
            except Exception:
                return
            event.x, event.y = x, y
            self.phone_view._on_press(event)
            if self.current_element is None and self.phone_view.elements:
                self._show_overlay("No element here")

        def release(event):
            if self._over(self.add_btn, event) and not self._owned_by(event.widget, self.add_btn):
                self._quick_rule()
                return "break"
            if self._over(self.run_btn, event) and not self._owned_by(event.widget, self.run_btn):
                self._toggle_rules()
                return "break"
            if self._over(self.close_btn, event) and not self._owned_by(event.widget, self.close_btn):
                self.destroy()
                return "break"

        if not getattr(self, "_press_bound", False):
            self.bind_all("<Button-1>", press)
            self.bind_all("<ButtonRelease-1>", release)
            self._press_bound = True
            self._release_bound = True

    def _display_scales(self, root: UIElement):
        """One scale for both axes, so a short button is never stretched tall."""
        full = getattr(self.device, "display_full_size", None) or (0, 0)
        capture = getattr(self.device, "capture_size", None) or full
        px = float(getattr(self.device, "px_scale", 1) or 1)
        space_w = 0
        for el in flatten_elements(root):
            raw = getattr(el, "_raw_bounds", None) or el.bounds
            space_w = max(space_w, int(raw[2]))
        cap_w = int(capture[0] or full[0] or 0)
        if space_w > 50 and cap_w > 50:
            scale = px * (cap_w / float(space_w))
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
        self._keep_clicks_on_mirror()

    def _on_element_selected(self, el: UIElement):
        self.current_element = el
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
            messagebox.showinfo("Add", "Select an element on the mirror first")
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
        from core.match import clicks_for_device
        family = self.device.identity(serial).get("family") or []
        wm = self.device.wm_for(serial)
        taps = clicks_for_device(
            self.tagger, self.rules.rules, root, family, wm, serial=serial
        )
        for x, y in taps:
            try:
                d.shell(f"input tap {x} {y}")
            except Exception:
                pass
        return len(taps)


def main():
    app = CompactController()
    app.mainloop()
