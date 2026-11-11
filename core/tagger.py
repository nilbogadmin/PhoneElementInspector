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

    def apply_tags_to_tree(self, root: UIElement, family=None) -> None:
        """Attach tags that belong on this phone. Other model years stay off."""
        for el in flatten_elements(root):
            el.tag = None
        for tag_name in list(self.tags):
            hit = self.find_for_click(root, tag_name, family=family)
            if hit is not None:
                hit.tag = tag_name
                hit.notes = (self.tags.get(tag_name) or {}).get("notes", "")

    def find_for_click(self, root: UIElement, tag_name: str, family=None) -> Optional[UIElement]:
        """Find this tag on a phone of the same model. Pixel boxes are not compared."""
        from .match import same_family

        data = self.tags.get(tag_name) or {}
        if not same_family(data.get("family"), family):
            return None
        selector = dict(data.get("selector") or {})
        selector.pop("bounds", None)
        els = [el for el in flatten_elements(root) if el.width > 1 and el.height > 1]
        if not els:
            return None
        max_r = max(el.bounds[2] for el in els) or 1
        max_b = max(el.bounds[3] for el in els) or 1
        els = [
            el for el in els
            if not (
                el.bounds[0] <= 2 and el.bounds[1] <= 2
                and el.width >= max_r * 0.96 and el.height >= max_b * 0.90
            )
        ]
        rid = selector.get("resource_id") or ""
        text = selector.get("text") or ""
        desc = selector.get("content_desc") or ""
        cls = selector.get("class_name") or ""
        pool = els
        if rid:
            id_hits = [el for el in pool if el.resource_id == rid]
            if id_hits:
                pool = id_hits
            else:
                pool = [
                    el for el in pool
                    if (text and el.text == text) or (desc and el.content_desc == desc)
                ]
        else:
            pool = [
                el for el in pool
                if (not cls or el.class_name == cls)
                and (not text or el.text == text)
                and (not desc or el.content_desc == desc)
                and (text or desc or cls)
            ]
        if not pool:
            return None
        if text:
            named = [el for el in pool if el.text == text]
            if len(named) == 1:
                return named[0]
            if named and not rid:
                pool = named
        if len(pool) == 1:
            return pool[0]
        norm = data.get("norm")
        if norm and len(norm) == 2:
            nx, ny = float(norm[0]), float(norm[1])

            def dist(el: UIElement) -> float:
                cx, cy = el.center
                return (cx / max_r - nx) ** 2 + (cy / max_b - ny) ** 2

            return min(pool, key=dist)
        return min(pool, key=lambda el: el.width * el.height)

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
