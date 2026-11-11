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
    hw = max((e.bounds[2] for e in els), default=1) or 1
    hh = max((e.bounds[3] for e in els), default=1) or 1
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
    cx, cy = el.center
    return int(round(cx / hw * tw)), int(round(cy / hh * th))


def screen_size(root: UIElement) -> Tuple[int, int]:
    els = flatten_elements(root)
    width = max((e.bounds[2] for e in els), default=1) or 1
    height = max((e.bounds[3] for e in els), default=1) or 1
    return width, height


def in_top_half(el: UIElement, root: UIElement) -> bool:
    """The bottom half of the phone is never a click target."""
    _, height = screen_size(root)
    cy = (el.bounds[1] + el.bounds[3]) / 2.0
    return cy < height * 0.5


def clicks_for_device(tagger, rules: Iterable[dict], root: UIElement, family, wm_sizes=()) -> List[Tuple[int, int]]:
    """Taps for elements the user added. Nothing else, and never the bottom half."""
    taps: List[Tuple[int, int]] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        actions = rule.get("actions") or []
        if not any(a.get("type") == "click" for a in actions):
            continue
        if not same_family(rule.get("family"), family):
            continue
        for cond in rule.get("conditions") or []:
            if cond.get("type") != "element_visible":
                continue
            el = tagger.find_for_click(root, cond.get("tag"), family=family)
            if el is None or not in_top_half(el, root):
                continue
            taps.append(tap_xy(el, root, wm_sizes))
    return taps
