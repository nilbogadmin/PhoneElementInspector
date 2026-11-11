"""UI Element model and helpers."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
import json


@dataclass
class UIElement:
    """Represents a single UI element from the phone hierarchy."""
    index: int = 0
    text: str = ""
    resource_id: str = ""
    class_name: str = ""
    package: str = ""
    content_desc: str = ""
    checkable: bool = False
    checked: bool = False
    clickable: bool = False
    enabled: bool = True
    focusable: bool = False
    focused: bool = False
    scrollable: bool = False
    long_clickable: bool = False
    password: bool = False
    selected: bool = False
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    children: List["UIElement"] = field(default_factory=list)

    # User-defined
    tag: Optional[str] = None
    notes: str = ""
    ignored: bool = False

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    @property
    def center(self) -> tuple[int, int]:
        return (
            (self.bounds[0] + self.bounds[2]) // 2,
            (self.bounds[1] + self.bounds[3]) // 2,
        )

    @property
    def display_name(self) -> str:
        if self.tag:
            return f"[{self.tag}]"
        if self.text:
            return self.text[:40] + ("…" if len(self.text) > 40 else "")
        if self.content_desc:
            return self.content_desc[:40]
        if self.resource_id:
            return self.resource_id.split("/")[-1]
        return self.class_name.split(".")[-1] or "Element"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["children"] = [c.to_dict() for c in self.children]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UIElement":
        children_data = data.pop("children", [])
        el = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        el.children = [cls.from_dict(c) for c in children_data]
        return el

    def matches_selector(self, selector: Dict[str, str]) -> bool:
        """Exact match only. Empty selector never matches."""
        if not selector:
            return False
        for key, value in selector.items():
            if not value:
                continue
            if key == "bounds":
                # stored as "l,t,r,b"
                try:
                    parts = [int(x) for x in str(value).split(",")]
                    if len(parts) == 4 and tuple(parts) != self.bounds:
                        return False
                except Exception:
                    return False
                continue
            actual = getattr(self, key, None)
            if actual is None:
                return False
            if str(actual) != str(value):
                return False
        return True


def flatten_elements(root: UIElement) -> List[UIElement]:
    """Return a flat list of all elements (depth-first)."""
    result = [root]
    for child in root.children:
        result.extend(flatten_elements(child))
    return result


def pick_element(elements, ix: int, iy: int, img_w: int, img_h: int, slop: int = 0):
    """Smallest element under the point. Skip the outer screen frame only."""
    best = None
    best_area = None
    limit_w = img_w * 0.96
    limit_h = img_h * 0.96
    for el in elements:
        if getattr(el, "ignored", False):
            continue
        l, t, r, b = el.bounds
        w = r - l
        h = b - t
        if w < 2 or h < 2:
            continue
        # The window itself. Full-width buttons and rows are still selectable.
        if w >= limit_w and h >= limit_h:
            continue
        if ix < l - slop or ix > r + slop or iy < t - slop or iy > b + slop:
            continue
        area = w * h
        if best is None or area < best_area:
            best = el
            best_area = area
    return best
