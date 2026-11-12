"""Tagging system – save and load user tags for elements."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Optional
from .element import UIElement, flatten_elements


class Tagger:
    def __init__(self, storage_path: str | Path = "data/tags.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.tags: Dict[str, dict] = {}  # tag_name -> selector + metadata
        self.load()

    def load(self):
        if self.storage_path.exists():
            try:
                self.tags = json.loads(self.storage_path.read_text(encoding="utf-8"))
            except Exception:
                self.tags = {}
        else:
            self.tags = {}

    def save(self):
        self.storage_path.write_text(
            json.dumps(self.tags, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_tag(
        self,
        tag_name: str,
        element: UIElement,
        notes: str = "",
        family=None,
        norm=None,
        screen=None,
    ) -> None:
        """Create or update a tag with the most precise selector possible."""
        # Prefer unique identifiers; fall back to bounds for exactness
        selector = {}
        if element.resource_id:
            selector["resource_id"] = element.resource_id
        if element.content_desc:
            selector["content_desc"] = element.content_desc
        if element.text:
            selector["text"] = element.text
        selector["class_name"] = element.class_name
        # Bounds stay off the selector. Two phones of the same model are a few
        # pixels apart, and an exact box would miss every one but the trainer.

        self.tags[tag_name] = {
            "selector": selector,
            "notes": notes,
            "bounds_example": list(element.bounds),
            "display_name": element.display_name,
            "family": list(family) if family else [],
            "serial": getattr(element, "_trained_serial", None) or "",
            "norm": list(norm) if norm else None,
            "screen": list(screen) if screen else None,
        }
        element.tag = tag_name
        self.save()

    def remove_tag(self, tag_name: str) -> None:
        if tag_name in self.tags:
            del self.tags[tag_name]
            self.save()

    def get_tag(self, tag_name: str) -> Optional[dict]:
        return self.tags.get(tag_name)

    def list_tags(self) -> List[str]:
        return sorted(self.tags.keys())

    def apply_tags_to_tree(self, root: UIElement, family=None, serial=None) -> None:
        """Attach tags that belong on this phone. Other model years stay off."""
        for el in flatten_elements(root):
            el.tag = None
        for tag_name in list(self.tags):
            hit = self.find_for_click(root, tag_name, family=family, serial=serial)
            if hit is not None:
                hit.tag = tag_name
                hit.notes = (self.tags.get(tag_name) or {}).get("notes", "")

    def find_for_click(self, root: UIElement, tag_name: str, family=None, serial=None) -> Optional[UIElement]:
        """The added element only. Never a lookalike, and never the bottom half."""
        from .match import same_family, in_top_half

        data = self.tags.get(tag_name) or {}
        if not data or not self._allowed(data, family, serial):
            return None
        selector = dict(data.get("selector") or {})
        rid = selector.get("resource_id") or ""
        text = selector.get("text") or ""
        desc = selector.get("content_desc") or ""
        cls = selector.get("class_name") or ""
        if not (rid or text or desc or cls):
            return None
        els = [
            el for el in flatten_elements(root)
            if el.clickable and el.width > 1 and el.height > 1 and in_top_half(el, root)
        ]
        if rid:
            els = [el for el in els if el.resource_id == rid]
        else:
            if text:
                els = [el for el in els if el.text == text]
            if desc:
                els = [el for el in els if el.content_desc == desc]
            if cls:
                els = [el for el in els if el.class_name == cls]
            # No id and no label: only the same spot, never every view of that class.
            if not (text or desc) and not data.get("norm"):
                return None
        if cls and rid:
            same_cls = [el for el in els if el.class_name == cls]
            if same_cls:
                els = same_cls
        if not els:
            return None
        if len(els) == 1 and (rid or text or desc):
            return els[0]
        return self._closest(els, data, root, loose=bool(rid or text or desc))

    def _allowed(self, data, family, serial) -> bool:
        from .match import same_family

        if same_family(data.get("family"), family):
            return True
        saved = data.get("serial") or ""
        return bool(saved and serial and saved == serial)

    def _closest(self, els, data, root: UIElement, loose: bool) -> Optional[UIElement]:
        norm = data.get("norm")
        if not norm or len(norm) != 2:
            return els[0] if len(els) == 1 else None
        max_r = max(el.bounds[2] for el in flatten_elements(root)) or 1
        max_b = max(el.bounds[3] for el in flatten_elements(root)) or 1
        nx, ny = float(norm[0]), float(norm[1])
        limit = 0.12 if loose else 0.04

        def dist(el: UIElement) -> float:
            cx, cy = el.center
            return ((cx / max_r - nx) ** 2 + (cy / max_b - ny) ** 2) ** 0.5

        near = [el for el in els if dist(el) <= limit]
        if not near:
            return None
        return min(near, key=dist)

    def find_by_tag(self, root: UIElement, tag_name: str) -> Optional[UIElement]:
        for el in flatten_elements(root):
            if el.tag == tag_name:
                return el
        data = self.tags.get(tag_name)
        if not data:
            return None
        selector = data.get("selector", {})
        for el in flatten_elements(root):
            if el.matches_selector(selector):
                el.tag = tag_name
                return el
        return None
