"""Farm that runs on the demo screen. No phones and no ADB server."""

from __future__ import annotations

import copy
from pathlib import Path

from .element import flatten_elements, pick_element
from .hierarchy import load_hierarchy_from_file
from .match import clicks_for_device, model_year, trainer_picks
from .rules import RulesEngine
from .tagger import Tagger

DEMO_XML = Path(__file__).resolve().parent.parent / "demo" / "sample_hierarchy.xml"
SCREEN = (1080, 2400)


def device_serials(rows):
    """Every connected phone. Unauthorized rows stay out. Nothing is capped."""
    return [serial for serial, state in rows if state == "device"]


def load_demo():
    return load_hierarchy_from_file(DEMO_XML)


def _by_id(root, suffix: str):
    for el in flatten_elements(root):
        if (el.resource_id or "").endswith(suffix):
            return el
    raise KeyError(suffix)


def save_rule(tagger: Tagger, rules: RulesEngine, element, name: str):
    element._raw_bounds = tuple(element.bounds)
    tagger.add_tag(name, element, screen=list(SCREEN))
    rules.add_rule({
        "name": f"Click {name}",
        "enabled": True,
        "conditions": [{"type": "element_visible", "tag": name}],
        "actions": [{"type": "click", "tag": name}],
    })


def one_tap(tagger, rules, root, serial: str):
    """The single tap this phone is allowed to send, or nothing."""
    taps, seen = clicks_for_device(tagger, rules.rules, root, [], (), serial=serial, hold=None)
    pairs = list(zip(seen, taps))
    if not pairs:
        return None
    return pairs[0]


def run_checks(tmp: Path) -> list:
    """Return a list of failures. Empty means the sandbox farm behaved."""
    failures = []

    def check(name, ok):
        if not ok:
            failures.append(name)

    root = load_demo()
    tagger = Tagger(tmp / "tags.json")
    rules = RulesEngine(tagger, tmp / "rules.json")
    transfer = _by_id(root, "btn_transfer")
    save_rule(tagger, rules, transfer, "1001")

    # Selection: the button, not the full-screen frame and not the label on a card.
    elements = flatten_elements(root)
    img_w, img_h = 1080, 1200
    cx = (transfer.bounds[0] + transfer.bounds[2]) // 2
    cy = (transfer.bounds[1] + transfer.bounds[3]) // 2
    picked = pick_element(elements, cx, cy, img_w, img_h)
    check("selects transfer", picked is transfer)
    label = _by_id(root, "balance_label")
    lx = (label.bounds[0] + label.bounds[2]) // 2
    ly = (label.bounds[1] + label.bounds[3]) // 2
    card = pick_element(elements, lx, ly, img_w, img_h)
    check("selects the card not the page", card is not None and card.resource_id.endswith("balance_card"))
    page = pick_element(elements, 10, 10, img_w, img_h)
    check("ignores the full screen box", page is None or (page.width * page.height) < img_w * img_h * 0.35)

    # One rule, every phone, only that element, never the bottom half.
    phones = []
    for i in range(24):
        year = "2024" if i < 16 else "2025"
        screen = copy.deepcopy(root)
        if i >= 20:
            # Bottom-nav only still has the top button in this demo tree.
            # Move the saved button into the bottom half so it must be ignored.
            moved = _by_id(screen, "btn_transfer")
            moved.bounds = (60, 1800, 520, 1940)
            moved._raw_bounds = moved.bounds
        phones.append((f"phone-{i:02d}", year, screen))
    check("no device cap", len(device_serials([(s, "device") for s, _, _ in phones] + [("nope", "unauthorized")])) == 24)
    check("first trainer of each year", trainer_picks([(s, y) for s, y, _ in phones]) == [("phone-00", "2024"), ("phone-16", "2025")])
    check("market year", model_year("moto g 5G - 2024", "XT2417") == "2024")
    check("xt year", model_year("XT2513-1") == "2025")

    taps = {}
    for serial, _year, screen in phones:
        taps[serial] = one_tap(tagger, rules, screen, serial)
    top = [serial for serial, hit in taps.items() if hit]
    check("taps every top-half phone", len(top) == 20)
    check("skips the bottom half", all(taps[f"phone-{i:02d}"] is None for i in range(20, 24)))
    check("tap is the transfer center", all(hit[0] == "1001" and hit[1] == (290, 650) for hit in taps.values() if hit))

    # Caption can change. A different id must not be tapped.
    changed = copy.deepcopy(root)
    node = _by_id(changed, "btn_transfer")
    node.text = "Send"
    hit = one_tap(tagger, rules, changed, "changed")
    check("text change still matches the id", hit is not None and hit[0] == "1001")
    only_other = copy.deepcopy(root)
    _by_id(only_other, "btn_transfer").resource_id = "com.demo.bank:id/something_else"
    check("different id is not tapped", one_tap(tagger, rules, only_other, "other") is None)

    # Reset removes every package's rules.
    rules.switch_app("com.whatnot_mobile")
    rules.add_rule({"name": "extra", "enabled": True, "conditions": [], "actions": []})
    cleared = rules.clear()
    check("reset clears every package", cleared == 2 and rules.rules == [] and "com.whatnot_mobile" not in rules.apps)
    return failures


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        failures = run_checks(Path(tmp))
    if failures:
        print("FAILED")
        for name in failures:
            print(" -", name)
        raise SystemExit(1)
    print("sandbox farm ok")


if __name__ == "__main__":
    main()
