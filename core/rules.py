"""Rules engine – per-app, with click / do-nothing actions."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from .element import UIElement
from .tagger import Tagger


ActionHandler = Callable[[dict, "RulesEngine", Optional[UIElement]], Any]


class RulesEngine:
    def __init__(self, tagger: Tagger, storage_path: str | Path = "data/rules.json"):
        self.tagger = tagger
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.apps: Dict[str, dict] = {}
        self.active_package: str = "demo"
        self.action_handlers: Dict[str, ActionHandler] = {}
        self.last_results: List[str] = []
        self.last_matched: Dict[int, bool] = {}  # rule index -> was true last tick
        self._click_callback = None  # set by UI: callable(x, y)
        self._register_default_actions()
        self.load()

    def set_click_callback(self, fn):
        self._click_callback = fn

    def load(self):
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.apps = {"demo": {"rules": data, "crop": None}}
                elif isinstance(data, dict) and "sets" in data:
                    rules = []
                    for s in data.get("sets", {}).values():
                        rules.extend(s)
                    self.apps = {"demo": {"rules": rules, "crop": None}}
                elif isinstance(data, dict) and "apps" in data:
                    self.apps = data["apps"]
                    self.active_package = data.get("active", "demo")
                else:
                    self.apps = {"demo": {"rules": [], "crop": None}}
            except Exception:
                self.apps = {"demo": {"rules": [], "crop": None}}
        else:
            self.apps = {"demo": {"rules": [], "crop": None}}
        if self.active_package not in self.apps:
            self.active_package = next(iter(self.apps), "demo")
        self.save()

    def save(self):
        payload = {"active": self.active_package, "apps": self.apps}
        self.storage_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def switch_app(self, package: str):
        package = package or "unknown"
        if package not in self.apps:
            self.apps[package] = {"rules": [], "crop": None}
        self.active_package = package
        self.save()

    @property
    def rules(self) -> List[dict]:
        return self.apps.setdefault(self.active_package, {"rules": [], "crop": None})["rules"]

    def get_crop(self) -> Optional[Tuple[int, int, int, int]]:
        c = self.apps.get(self.active_package, {}).get("crop")
        return tuple(c) if c and len(c) == 4 else None  # type: ignore

    def set_crop(self, crop: Optional[Tuple[int, int, int, int]]):
        entry = self.apps.setdefault(self.active_package, {"rules": [], "crop": None})
        entry["crop"] = list(crop) if crop else None
        self.save()

    def add_rule(self, rule: dict):
        self.rules.append(rule)
        self.save()

    def remove_rule(self, index: int):
        if 0 <= index < len(self.rules):
            del self.rules[index]
            self.save()

    def _register_default_actions(self):
        self.action_handlers = {
            "click": self._action_click,
            "do_nothing": self._action_do_nothing,
            "notify": self._action_notify,
            "log": self._action_log,
            "extract_text": self._action_extract_text,
            "wait": self._action_wait,
            "screenshot": self._action_screenshot,
        }

    def evaluate_condition(self, condition: dict, root: UIElement) -> bool:
        ctype = condition.get("type")
        tag = condition.get("tag")
        el = self.tagger.find_by_tag(root, tag) if tag else None
        if ctype == "element_visible":
            return el is not None and not getattr(el, "ignored", False)
        if ctype == "element_not_visible":
            return el is None or getattr(el, "ignored", False)
        if ctype == "element_text_contains":
            return el is not None and not el.ignored and condition.get("value", "").lower() in (el.text or "").lower()
        if ctype == "element_text_equals":
            return el is not None and not el.ignored and (el.text or "") == condition.get("value", "")
        return False

    def evaluate_rule(self, rule: dict, root: UIElement) -> bool:
        if not rule.get("enabled", True):
            return False
        conditions = rule.get("conditions", [])
        if not conditions:
            return True
        logic = rule.get("logic", "AND").upper()
        results = [self.evaluate_condition(c, root) for c in conditions]
        return any(results) if logic == "OR" else all(results)

    def run_actions(self, actions: List[dict], root: UIElement) -> List[str]:
        messages = []
        for action in actions:
            handler = self.action_handlers.get(action.get("type"))
            if handler:
                try:
                    msg = handler(action, root)
                    if msg:
                        messages.append(str(msg))
                except Exception as e:
                    messages.append(f"Action failed: {e}")
            else:
                messages.append(f"Unknown action: {action.get('type')}")
        return messages

    def run_all(self, root: UIElement, edge_only: bool = False) -> List[str]:
        """Run rules. If edge_only=True, only fire when a rule becomes true (appear)."""
        self.last_results = []
        for i, rule in enumerate(self.rules):
            matched = self.evaluate_rule(rule, root)
            was = self.last_matched.get(i, False)
            self.last_matched[i] = matched
            if not matched:
                continue
            if edge_only and was:
                continue  # still visible – already fired
            self.last_results.append(f"\u2714 {rule.get('name', 'Unnamed')}")
            self.last_results.extend(self.run_actions(rule.get("actions", []), root))
        # drop stale indices if rules were deleted
        self.last_matched = {i: self.last_matched[i] for i in self.last_matched if i < len(self.rules)}
        return self.last_results

    def _action_click(self, action, root):
        tag = action.get("tag")
        el = self.tagger.find_by_tag(root, tag) if tag else None
        if not el:
            return f"CLICK [{tag}] \u2013 not found"
        x, y = el.center
        if self._click_callback:
            self._click_callback(x, y)
        return f"CLICK [{tag}] at ({x}, {y})"

    def _action_do_nothing(self, action, root):
        return None  # silent no-op

    def _action_notify(self, action, root):
        return f"NOTIFY: {action.get('message', 'Notification')}"

    def _action_log(self, action, root):
        return f"LOG: {action.get('message', '')}"

    def _action_extract_text(self, action, root):
        el = self.tagger.find_by_tag(root, action.get("tag"))
        return f"EXTRACT [{action.get('tag')}]: {el.text if el else '<not found>'}"

    def _action_wait(self, action, root):
        return f"WAIT {float(action.get('seconds', 1.0))}s"

    def _action_screenshot(self, action, root):
        return "SCREENSHOT"
