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

        raw = getattr(element, "_raw_bounds", None) or element.bounds
        self.tags[tag_name] = {
            "selector": selector,
            "notes": notes,
            "bounds_example": [int(v) for v in raw],
            "display_name": element.display_name,
            "family": list(family) if family else [],
            "serial": getattr(element, "_trained_serial", None) or "",
            "norm": list(norm) if norm else None,
            "screen": list(screen) if screen else None,
            "exact": {
                "class_name": element.class_name or "",
                "resource_id": element.resource_id or "",
                "text": element.text or "",
                "content_desc": element.content_desc or "",
                "bounds": [int(v) for v in raw],
                "screen": list(screen) if screen else [1, 1],
            },
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
        """One memorized element. Anything that is not that exact box is ignored."""
        from .match import exact_element

        data = self.tags.get(tag_name) or {}
        exact = data.get("exact")
        if not exact:
            return None
        hits = [el for el in flatten_elements(root) if exact_element(exact, el, root)]
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        screen = exact.get("screen") or [1, 1]
        box = exact.get("bounds") or [0, 0, 0, 0]
        sw, sh = max(float(screen[0]), 1.0), max(float(screen[1]), 1.0)
        nx = (box[0] + box[2]) / 2.0 / sw
        ny = (box[1] + box[3]) / 2.0 / sh
        live_w, live_h = 1, 1
        from .match import screen_size, _box
        live_w, live_h = screen_size(root)

        def dist(el: UIElement) -> float:
            raw = _box(el)
            cx = (raw[0] + raw[2]) / 2.0 / live_w
            cy = (raw[1] + raw[3]) / 2.0 / live_h
            return (cx - nx) ** 2 + (cy - ny) ** 2

        return min(hits, key=dist)

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
