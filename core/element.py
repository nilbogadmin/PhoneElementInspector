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


def fit_scales(capture_size, display_full, root_bounds, element_bounds, wm_sizes=()):
    """Map hierarchy pixels onto the shrunken screenshot.

    The screenshot and the UI dump are not always the same resolution.
    Moto G (2024) is 720x1612 and Moto G (2025) is 720x1604, and `wm size`
    can add a second override. This picks the coordinate space that matches
    the picture, then returns (scale_x, scale_y) into display_full.
    """
    cw, ch = int(capture_size[0] or 0), int(capture_size[1] or 0)
    dw, dh = int(display_full[0] or 0), int(display_full[1] or 0)
    if dw < 2 or dh < 2:
        return (1.0, 1.0)

    max_r = 0
    max_b = 0
    for b in element_bounds or ():
        if not b:
            continue
        max_r = max(max_r, int(b[2]))
        max_b = max(max_b, int(b[3]))
    extent = (max_r, max_b)
    if root_bounds:
        root_wh = (
            max(0, int(root_bounds[2] - root_bounds[0])),
            max(0, int(root_bounds[3] - root_bounds[1])),
        )
    else:
        root_wh = (0, 0)

    cap_aspect = (cw / float(ch)) if ch else 0.45

    def aspect_ok(w, h) -> bool:
        if w < 80 or h < 80 or cap_aspect <= 0:
            return False
        return abs((w / float(h)) - cap_aspect) / cap_aspect <= 0.12

    space = None
    if aspect_ok(*extent):
        space = extent
    elif aspect_ok(*root_wh):
        space = root_wh
    else:
        for wm in wm_sizes or ():
            if wm and aspect_ok(int(wm[0]), int(wm[1])):
                space = (int(wm[0]), int(wm[1]))
                break
    if space is None:
        if extent[0] > 80 and extent[1] > 80:
            space = extent
        elif cw > 80 and ch > 80:
            space = (cw, ch)
        else:
            space = (dw, dh)
    return (dw / float(space[0]), dh / float(space[1]))


def flatten_elements(root: UIElement) -> List[UIElement]:
    """Return a flat list of all elements (depth-first)."""
    result = [root]
    for child in root.children:
        result.extend(flatten_elements(child))
    return result


def pick_element(elements, ix: int, iy: int, img_w: int, img_h: int, slop: int = 0, clickable_only: bool = False):
    """Smallest clickable element whose own box contains the point.

    Non-clickable nodes are ignored. The point has to sit inside the real
    border. Nothing nearby is stretched or borrowed to fill the click.
    """
    best = None
    best_area = None
    pad = max(0, int(slop))

    for el in elements:
        if getattr(el, "ignored", False):
            continue
        if clickable_only and not getattr(el, "clickable", False):
            continue
        l, t, r, b = el.bounds
        w = r - l
        h = b - t
        if w < 2 or h < 2:
            continue
        if ix < l - pad or ix > r + pad or iy < t - pad or iy > b + pad:
            continue
        area = w * h
        if best is None or area < best_area:
            best = el
            best_area = area
    return best
