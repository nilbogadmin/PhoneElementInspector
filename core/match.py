"""Match a saved element on every phone of the same model, and nowhere else."""

from __future__ import annotations
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from .element import UIElement, flatten_elements

_YEAR = re.compile(r"20\d{2}")
_XT = re.compile(r"xt\s*(\d{4})", re.I)


def family_aliases(*parts: str) -> List[str]:
    """Names that mean the same phone.

    A Moto G 2024 can report itself as 'moto g 5G - 2024', 'XT2417-1', or a
    codename. Carrier twins share at least one of those, and a 2025 does not.
    """
    text = " ".join(p for p in parts if p).lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    aliases = set()
    year = _YEAR.search(text)
    year_s = year.group(0) if year else ""
    if "stylus" in text:
        line = "stylus"
    elif "power" in text:
        line = "power"
    elif "play" in text:
        line = "play"
    elif "moto g" in text or re.search(r"\bmoto ?g\b", text):
        line = "g"
    else:
        line = ""
    if year_s and line:
        aliases.add(f"moto {line} {year_s}")
    elif year_s and "moto" in text:
        aliases.add(f"moto {year_s}")
    xt = _XT.search(text)
    if xt:
        aliases.add(f"xt{xt.group(1)}")
    for raw in parts:
        token = re.sub(r"\s+", " ", (raw or "").strip().lower())
        if not token or " " in token or token.startswith("xt") or "moto" in token:
            continue
        if len(token) > 2:
            aliases.add(token)
    if not aliases and text:
        aliases.add(text)
    return sorted(aliases)


def same_family(rule_family, device_family) -> bool:
    """A rule only runs on phones of the same model.

    No family means it is not allowed to run on every connected phone.
    """
    rule = _as_set(rule_family)
    device = _as_set(device_family)
    if not rule or not device:
        return False
    return bool(rule & device)


def _as_set(value) -> set:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    return {str(v) for v in value if v}


def tap_xy(el: UIElement, root: UIElement, wm_sizes: Sequence[Tuple[int, int]] = ()) -> Tuple[int, int]:
    """Center of this element in the coordinate space `input tap` expects."""
    els = flatten_elements(root)
    hw = max((_box(e)[2] for e in els), default=1) or 1
    hh = max((_box(e)[3] for e in els), default=1) or 1
    tw, th = hw, hh
    best = None
    best_score = None
    for wm in wm_sizes or ():
        if not wm or wm[0] < 50 or wm[1] < 50:
            continue
        sx = wm[0] / float(hw)
        sy = wm[1] / float(hh)
        uniform = abs(sx - sy) / max(sx, sy, 1e-6)
        # Equal aspect: stay closest to the dump so a 1:1 size wins over a scaled copy.
        score = (uniform, abs(sx - 1.0) + abs(sy - 1.0))
        if best is None or score < best_score:
            best = (int(wm[0]), int(wm[1]))
            best_score = score
    if best is not None and best_score is not None and best_score[0] < 0.04:
        if abs(best[0] - hw) >= 4 or abs(best[1] - hh) >= 8:
            tw, th = best
    raw = _box(el)
    return int(round((raw[0] + raw[2]) / 2.0)), int(round((raw[1] + raw[3]) / 2.0))


def _box(el: UIElement):
    return getattr(el, "_raw_bounds", None) or el.bounds


def screen_size(root: UIElement) -> Tuple[int, int]:
    """Full dump space. Root height alone cuts off the app and blocks the tap."""
    els = flatten_elements(root)
    width = max((_box(e)[2] for e in els), default=1) or 1
    height = max((_box(e)[3] for e in els), default=1) or 1
    return int(width), int(height)


def in_top_half(el: UIElement, root: UIElement) -> bool:
    """The bottom half of the phone is never a click target."""
    _, height = screen_size(root)
    raw = _box(el)
    cy = (raw[1] + raw[3]) / 2.0
    return cy < height * 0.52


def same_box(saved_bounds, saved_screen, live_bounds, live_screen, tol: float = 0.035) -> bool:
    """True only when both boxes are the same element on the screen.

    Edges are compared as a fraction of the screen, so a 2024 twin still
    matches, and a text label sitting inside the control does not.
    """
    try:
        sw, sh = float(saved_screen[0]), float(saved_screen[1])
        lw, lh = float(live_screen[0]), float(live_screen[1])
    except (TypeError, IndexError, ValueError):
        return False
    if min(sw, sh, lw, lh) < 50:
        return False
    saved = (
        saved_bounds[0] / sw, saved_bounds[1] / sh,
        saved_bounds[2] / sw, saved_bounds[3] / sh,
    )
    live = (
        live_bounds[0] / lw, live_bounds[1] / lh,
        live_bounds[2] / lw, live_bounds[3] / lh,
    )
    return all(abs(a - b) <= tol for a, b in zip(saved, live))


def exact_element(saved: dict, el: UIElement, root: UIElement) -> bool:
    """Same class, id, text, description, and box. A neighbor does not qualify."""
    if not saved or not in_top_half(el, root):
        return False
    if (el.class_name or "") != (saved.get("class_name") or ""):
        return False
    if (el.resource_id or "") != (saved.get("resource_id") or ""):
        return False
    if (el.text or "") != (saved.get("text") or ""):
        return False
    if (el.content_desc or "") != (saved.get("content_desc") or ""):
        return False
    raw = _box(el)
    return same_box(saved.get("bounds") or (), saved.get("screen") or (), raw, screen_size(root), tol=0.02)


def clicks_for_device(tagger, rules: Iterable[dict], root: UIElement, family, wm_sizes=(), serial=None, hold=None):
    """New taps only. A tag already on screen is not tapped again."""
    held = set(hold or ())
    taps: List[Tuple[int, int]] = []
    seen: List[str] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        actions = rule.get("actions") or []
        if not any(a.get("type") == "click" for a in actions):
            continue
        for cond in rule.get("conditions") or []:
            if cond.get("type") != "element_visible":
                continue
            tag = cond.get("tag") or ""
            el = tagger.find_for_click(root, tag, family=family, serial=serial)
            if el is None or not in_top_half(el, root):
                continue
            if tag:
                seen.append(tag)
            if tag in held:
                continue
            x, y = tap_xy(el, root, wm_sizes)
            if any(abs(x - a) <= 24 and abs(y - b) <= 24 for a, b in taps):
                continue
            taps.append((x, y))
    return taps, seen
