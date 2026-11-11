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
        if element.class_name:
            selector["class_name"] = element.class_name
        # Always include bounds so identical-looking elements stay distinct
        selector["bounds"] = f"{element.bounds[0]},{element.bounds[1]},{element.bounds[2]},{element.bounds[3]}"

        self.tags[tag_name] = {
            "selector": selector,
            "notes": notes,
            "bounds_example": list(element.bounds),
            "display_name": element.display_name,
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

    def apply_tags_to_tree(self, root: UIElement) -> None:
        """Walk the tree and attach existing tags to matching elements."""
        for el in flatten_elements(root):
            for tag_name, data in self.tags.items():
                selector = data.get("selector", {})
                if el.matches_selector(selector):
                    el.tag = tag_name
                    el.notes = data.get("notes", "")
                    break

    def find_for_click(self, root: UIElement, tag_name: str) -> Optional[UIElement]:
        """Match a tag on any device. Bounds are not required so other phones still hit."""
        data = self.tags.get(tag_name) or {}
        selector = dict(data.get("selector") or {})
        selector.pop("bounds", None)
        if not any(selector.values()):
            return None
        matches = [el for el in flatten_elements(root) if el.matches_selector(selector)]
        # Drop full-screen borders
        matches = [
            el for el in matches
            if el.width > 0 and el.height > 0
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        norm = data.get("norm")
        all_els = flatten_elements(root)
        max_r = max((e.bounds[2] for e in all_els), default=1) or 1
        max_b = max((e.bounds[3] for e in all_els), default=1) or 1
        if norm and len(norm) == 2:
            nx, ny = float(norm[0]), float(norm[1])
            def dist(el: UIElement) -> float:
                cx, cy = el.center
                return (cx / max_r - nx) ** 2 + (cy / max_b - ny) ** 2
            return min(matches, key=dist)
        return min(matches, key=lambda el: el.width * el.height)

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
