"""Main window – live screen, equal columns, rule actions."""

from __future__ import annotations
import customtkinter as ctk
from tkinter import messagebox, simpledialog
from typing import Optional, Tuple
from pathlib import Path

from core.device import DeviceConnector
from core.tagger import Tagger
from core.rules import RulesEngine
from core.element import UIElement, flatten_elements
from core.paths import app_dir
from gui.phone_view import PhoneView


BG = "#000000"
SURFACE = "#1c1c1e"
SURFACE2 = "#2c2c2e"
SURFACE3 = "#3a3a3c"
BLUE = "#0a84ff"
BLUE_PRESS = "#0066cc"
GREEN = "#30d158"
RED = "#ff453a"
LABEL = "#ffffff"
SECONDARY = "#98989f"
TERTIARY = "#636366"

# Live refresh interval (ms)
LIVE_INTERVAL_MS = 800


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Element Inspector")
        self.geometry("1000x800")
        self.minsize(700, 500)
        self.configure(fg_color=BG)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        data = app_dir() / "data"
        data.mkdir(parents=True, exist_ok=True)
        self.device = DeviceConnector()
        self.tagger = Tagger(storage_path=data / "tags.json")
        self.rules = RulesEngine(tagger=self.tagger, storage_path=data / "rules.json")
        self.rules.set_click_callback(lambda x, y: self.device.click(x, y))

        self.current_element: Optional[UIElement] = None
        self.root_element: Optional[UIElement] = None
        self.current_package = "demo"
        self._live = True
        self._live_job = None
        self._busy = False

        self._build_ui()
        self.after(120, self._initial_load)

    def _build_ui(self):
        top = ctk.CTkFrame(self, height=40, fg_color=SURFACE, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        bar = ctk.CTkFrame(top, fg_color="transparent")
        bar.pack(fill="both", expand=True, padx=10, pady=5)

        self.live_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            bar, text="Live", variable=self.live_var, command=self._toggle_live,
            progress_color=GREEN, width=60, font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 8))

        self.app_label = ctk.CTkLabel(bar, text="", text_color=SECONDARY, font=ctk.CTkFont(size=12))
        self.app_label.pack(side="left", padx=8)

        self.status_label = ctk.CTkLabel(bar, text="", text_color=TERTIARY, font=ctk.CTkFont(size=11), anchor="e")
        self.status_label.pack(side="left", fill="x", expand=True)

        self._btn(bar, "Run Rules", self.run_rules, w=88, fg=GREEN, hover="#28b34a").pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=8)
        body.grid_columnconfigure(0, weight=1, uniform="col")
        body.grid_columnconfigure(1, weight=1, uniform="col")
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        self.phone_view = PhoneView(
            left,
            on_element_selected=self.on_element_selected,
            on_crop_changed=self._on_crop_changed,
        )
        self.phone_view.pack(fill="both", expand=True, padx=10, pady=10)

        right = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self.tabs = ctk.CTkTabview(
            right, fg_color=SURFACE, corner_radius=10,
            segmented_button_fg_color=SURFACE2,
            segmented_button_selected_color=BLUE,
            segmented_button_selected_hover_color=BLUE_PRESS,
            segmented_button_unselected_color=SURFACE2,
            segmented_button_unselected_hover_color=SURFACE3,
            text_color=LABEL,
        )
        self.tabs.pack(fill="both", expand=True, padx=6, pady=6)

        self.tab_el = self.tabs.add("Element")
        self.tab_tags = self.tabs.add("Tags")
        self.tab_rules = self.tabs.add("Rules")
        self.tab_log = self.tabs.add("Log")

        self._build_element()
        self._build_tags()
        self._build_rules()
        self._build_log()

    def _btn(self, parent, text, cmd, w=80, fg=None, hover=None):
        return ctk.CTkButton(
            parent, text=text, command=cmd, width=w, height=26, corner_radius=7,
            fg_color=fg or SURFACE2, hover_color=hover or SURFACE3,
            text_color=LABEL, font=ctk.CTkFont(size=12),
        )

    def _build_element(self):
        f = self.tab_el
        self.el_info = ctk.CTkTextbox(
            f, height=140, font=ctk.CTkFont(family="Menlo", size=12),
            fg_color=SURFACE2, text_color=LABEL, corner_radius=8, border_width=0,
        )
        self.el_info.pack(fill="x", padx=6, pady=6)
        self.el_info.insert("1.0", "Select an element…")
        self.el_info.configure(state="disabled")

        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)
        self._btn(row, "Tag", self.tag_current, w=64, fg=BLUE, hover=BLUE_PRESS).pack(side="left", padx=2)
        self._btn(row, "Ignore", self.ignore_current, w=64, fg=RED, hover="#cc2f28").pack(side="left", padx=2)
        self._btn(row, "Click", self.click_current, w=64).pack(side="left", padx=2)
        self._btn(row, "Reset focus", self._reset_crop, w=88).pack(side="right", padx=2)

        ob = ctk.CTkFrame(f, fg_color="transparent")
        ob.pack(fill="x", padx=6, pady=4)
        self.outline_var = ctk.StringVar(value="all")
        for txt, val in [("All", "all"), ("Tagged", "tagged"), ("Selected", "selected")]:
            ctk.CTkRadioButton(
                ob, text=txt, variable=self.outline_var, value=val,
                command=self._update_outlines, fg_color=BLUE, hover_color=BLUE_PRESS,
                text_color=SECONDARY, font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=6)

        self.el_list = ctk.CTkScrollableFrame(f, fg_color=SURFACE2, corner_radius=8)
        self.el_list.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_tags(self):
        f = self.tab_tags
        self.tags_list = ctk.CTkScrollableFrame(f, fg_color=SURFACE2, corner_radius=8)
        self.tags_list.pack(fill="both", expand=True, padx=6, pady=6)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=4)
        self._btn(row, "Refresh", self.refresh_tags_list, w=72).pack(side="left", padx=2)
        self._btn(row, "Delete", self.delete_selected_tag, w=64, fg=RED, hover="#cc2f28").pack(side="left", padx=2)
        self.selected_tag_name = None

    def _build_rules(self):
        f = self.tab_rules
        ctk.CTkLabel(
            f, text="Auto-runs live when element appears   ·   saved per app",
            text_color=TERTIARY, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self.rules_list = ctk.CTkScrollableFrame(f, fg_color=SURFACE2, corner_radius=8, height=150)
        self.rules_list.pack(fill="both", expand=True, padx=6, pady=4)

        creator = ctk.CTkFrame(f, fg_color=SURFACE2, corner_radius=8)
        creator.pack(fill="x", padx=6, pady=6)

        def ent(ph=""):
            e = ctk.CTkEntry(creator, width=190, height=26, corner_radius=7,
                             fg_color=SURFACE3, border_width=0, text_color=LABEL,
                             placeholder_text=ph)
            return e

        ctk.CTkLabel(creator, text="Name", text_color=TERTIARY, font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 1))
        self.rule_name_entry = ent("optional")
        self.rule_name_entry.grid(row=0, column=1, padx=4, pady=(6, 1), sticky="w")

        ctk.CTkLabel(creator, text="If this tag is showing", text_color=TERTIARY, font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w", padx=8)
        self.rule_tag_entry = ent("tag name")
        self.rule_tag_entry.grid(row=1, column=1, padx=4, pady=1, sticky="w")

        ctk.CTkLabel(creator, text="Then", text_color=TERTIARY, font=ctk.CTkFont(size=11)).grid(row=2, column=0, sticky="w", padx=8)
        self.rule_action_var = ctk.StringVar(value="click it")
        ctk.CTkOptionMenu(
            creator, variable=self.rule_action_var, width=190, height=26, corner_radius=7,
            values=[
                "click it",
                "do nothing",
                "notify",
                "log",
                "extract text",
                "wait",
                "screenshot",
            ],
            fg_color=SURFACE3, button_color=BLUE, button_hover_color=BLUE_PRESS,
            dropdown_fg_color=SURFACE2, text_color=LABEL,
        ).grid(row=2, column=1, padx=4, pady=1, sticky="w")

        ctk.CTkLabel(creator, text="Message / value", text_color=TERTIARY, font=ctk.CTkFont(size=11)).grid(row=3, column=0, sticky="w", padx=8)
        self.rule_value_entry = ent("optional")
        self.rule_value_entry.grid(row=3, column=1, padx=4, pady=1, sticky="w")

        self._btn(creator, "Add rule", self.add_quick_rule, w=90, fg=BLUE, hover=BLUE_PRESS).grid(
            row=4, column=0, columnspan=2, pady=8)

    def _build_log(self):
        f = self.tab_log
        self.log_box = ctk.CTkTextbox(
            f, font=ctk.CTkFont(family="Menlo", size=12),
            fg_color=SURFACE2, text_color=SECONDARY, corner_radius=8, border_width=0,
        )
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- live loop ----

    def _toggle_live(self):
        self._live = self.live_var.get()
        if self._live:
            self._schedule_live()
            self.log("Live on")
        else:
            if self._live_job:
                self.after_cancel(self._live_job)
                self._live_job = None
            self.log("Live off")

    def _schedule_live(self):
        if self._live_job:
            self.after_cancel(self._live_job)
        self._live_job = self.after(LIVE_INTERVAL_MS, self._live_tick)

    def _live_tick(self):
        self._live_job = None
        if not self._live:
            return
        if not self._busy:
            self._busy = True
            try:
                self.device.refresh()
                self._apply_data(quiet=True)
                # Auto-run rules: fire when a condition becomes true (element appears)
                if self.root_element and self.rules.rules:
                    results = self.rules.run_all(self.root_element, edge_only=True)
                    for line in results:
                        self.log(line)
            except Exception as e:
                self.status_label.configure(text=str(e)[:60])
            finally:
                self._busy = False
        self._schedule_live()

    def log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _detect_package(self, root: UIElement) -> str:
        for el in flatten_elements(root):
            if el.package:
                return el.package
        return "unknown"

    def _initial_load(self):
        status = self.device.connect()
        self.status_label.configure(text=status)
        self.log(status)
        self._apply_data()
        if self._live:
            self._schedule_live()

    def _apply_data(self, quiet: bool = False):
        root = self.device.get_hierarchy()
        screenshot = self.device.get_screenshot()
        if not root or not screenshot:
            if not quiet:
                self.log("No data")
            return

        # Preserve selection tag across refreshes
        prev_tag = self.current_element.tag if self.current_element and self.current_element.tag else None

        self.root_element = root
        package = self._detect_package(root)
        if package != self.current_package:
            self.current_package = package
            self.rules.switch_app(package)
            self.app_label.configure(text=package)
            if not quiet:
                self.log(f"App: {package}")

        self.tagger.apply_tags_to_tree(root)
        self.phone_view.set_data(
            screenshot, root,
            saved_crop=self.rules.get_crop(),
            preserve_selection=quiet,
        )

        if prev_tag:
            for el in flatten_elements(root):
                if el.tag == prev_tag and not el.ignored:
                    self.on_element_selected(el)
                    break

        self._populate_element_list()
        if not quiet:
            self.refresh_tags_list()
            self.refresh_rules_list()
            n = sum(1 for e in flatten_elements(root) if not e.ignored)
            self.status_label.configure(text=f"{n} elements in focus")

    def _update_outlines(self):
        m = self.outline_var.get()
        if m == "all":
            self.phone_view.set_outline_mode(True, False)
        elif m == "tagged":
            self.phone_view.set_outline_mode(False, True)
        else:
            self.phone_view.set_outline_mode(False, False)

    def _reset_crop(self):
        self.phone_view.reset_crop()
        self.rules.set_crop(self.phone_view.get_crop())
        self._populate_element_list()

    def _on_crop_changed(self, crop: Tuple[int, int, int, int]):
        self.rules.set_crop(crop)
        self._populate_element_list()

    def on_element_selected(self, el: UIElement):
        self.current_element = el
        self.phone_view.set_selected(el)
        info = (
            f"Tag          {el.tag or '—'}\n"
            f"Text         {el.text or '—'}\n"
            f"Resource ID  {el.resource_id or '—'}\n"
            f"Content-Desc {el.content_desc or '—'}\n"
            f"Class        {el.class_name}\n"
            f"Bounds       {el.bounds}\n"
            f"Clickable    {el.clickable}   Enabled {el.enabled}\n"
            f"Size         {el.width}×{el.height}   Center {el.center}"
        )
        self.el_info.configure(state="normal")
        self.el_info.delete("1.0", "end")
        self.el_info.insert("1.0", info)
        self.el_info.configure(state="disabled")

    def _populate_element_list(self):
        for w in self.el_list.winfo_children():
            w.destroy()
        if not self.root_element:
            return
        for el in flatten_elements(self.root_element):
            if el.ignored:
                continue
            label = f"● {el.tag} — {el.display_name}" if el.tag else el.display_name
            ctk.CTkButton(
                self.el_list, text=label[:60], anchor="w", height=26, corner_radius=5,
                fg_color="transparent", hover_color=SURFACE3, text_color=LABEL,
                font=ctk.CTkFont(size=12),
                command=lambda e=el: self.on_element_selected(e),
            ).pack(fill="x", pady=0, padx=1)

    def tag_current(self):
        if not self.current_element:
            return
        name = simpledialog.askstring("Tag", "Tag name:", parent=self)
        if not name:
            return
        name = name.strip()
        self.tagger.add_tag(name, self.current_element)
        self.current_element.tag = name
        self.on_element_selected(self.current_element)
        self._populate_element_list()
        self.refresh_tags_list()
        self.phone_view._redraw()

    def ignore_current(self):
        if not self.current_element:
            return
        self.current_element.ignored = True
        self.current_element = None
        self.phone_view.set_selected(None)
        self._populate_element_list()
        self.phone_view._redraw()

    def click_current(self):
        if not self.current_element:
            return
        self.log(self.device.click(*self.current_element.center))

    def refresh_tags_list(self):
        for w in self.tags_list.winfo_children():
            w.destroy()
        self.selected_tag_name = None
        for name in self.tagger.list_tags():
            ctk.CTkButton(
                self.tags_list, text=f"●  {name}", anchor="w", height=28, corner_radius=5,
                fg_color="transparent", hover_color=SURFACE3, text_color=LABEL,
                command=lambda n=name: setattr(self, "selected_tag_name", n),
            ).pack(fill="x", pady=0, padx=1)

    def delete_selected_tag(self):
        if not self.selected_tag_name:
            return
        self.tagger.remove_tag(self.selected_tag_name)
        self.selected_tag_name = None
        self.refresh_tags_list()
        if self.root_element:
            self.tagger.apply_tags_to_tree(self.root_element)
            self._populate_element_list()
            self.phone_view._redraw()

    def add_quick_rule(self):
        name = self.rule_name_entry.get().strip()
        tag = self.rule_tag_entry.get().strip()
        if not tag:
            messagebox.showwarning("Tag required", "Enter the tag that should be showing.")
            return

        label = self.rule_action_var.get()
        value = self.rule_value_entry.get().strip()

        # Map UI labels → action types
        mapping = {
            "click it": "click",
            "do nothing": "do_nothing",
            "notify": "notify",
            "log": "log",
            "extract text": "extract_text",
            "wait": "wait",
            "screenshot": "screenshot",
        }
        atype = mapping.get(label, "do_nothing")
        if not name:
            name = f"If {tag} → {label}"

        action = {"type": atype}
        if atype == "click":
            action["tag"] = tag
        elif atype in ("notify", "log"):
            action["message"] = value or name
        elif atype == "extract_text":
            action["tag"] = tag
        elif atype == "wait":
            try:
                action["seconds"] = float(value or 1)
            except ValueError:
                action["seconds"] = 1.0

        self.rules.add_rule({
            "name": name,
            "enabled": True,
            "conditions": [{"type": "element_visible", "tag": tag}],
            "logic": "AND",
            "actions": [action],
        })
        self.rules.last_matched.clear()  # allow new rule to fire on next appear
        self.log(f"Rule: if {tag} showing → {label}  (auto-runs live)")
        self.refresh_rules_list()
        self.rule_name_entry.delete(0, "end")
        self.rule_tag_entry.delete(0, "end")
        self.rule_value_entry.delete(0, "end")

    def refresh_rules_list(self):
        for w in self.rules_list.winfo_children():
            w.destroy()
        for i, rule in enumerate(self.rules.rules):
            cond = rule.get("conditions", [{}])
            tag = cond[0].get("tag", "?") if cond else "?"
            acts = rule.get("actions", [{}])
            atype = acts[0].get("type", "?") if acts else "?"
            nice = {
                "click": "click it",
                "do_nothing": "do nothing",
                "extract_text": "extract text",
            }.get(atype, atype)
            text = f"if {tag} → {nice}"
            row = ctk.CTkFrame(self.rules_list, fg_color="transparent")
            row.pack(fill="x", pady=0)
            ctk.CTkLabel(row, text=text, anchor="w", text_color=LABEL,
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=4, fill="x", expand=True)
            ctk.CTkButton(
                row, text="×", width=26, height=24, corner_radius=5,
                fg_color=SURFACE3, hover_color=RED, text_color=LABEL,
                command=lambda idx=i: self._delete_rule(idx),
            ).pack(side="right", padx=2)

    def _delete_rule(self, index: int):
        self.rules.remove_rule(index)
        self.rules.last_matched.clear()
        self.refresh_rules_list()

    def run_rules(self):
        if not self.root_element:
            return
        results = self.rules.run_all(self.root_element)
        if not results:
            self.log(f"No rules fired ({self.current_package})")
        else:
            for line in results:
                self.log(line)
