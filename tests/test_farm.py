"""Sandbox farm. Proves the click rules without a phone or an ADB server."""

import tempfile
import unittest
from pathlib import Path

from core.adb import devices
from core.phone_controller import PhoneController
from core.sandbox import run_checks


class FakeApp:
    fast = True

    def __init__(self):
        self.calls = []
        self._year_on = {"2024": True, "2025": True}
        self._years = {"serial": "2024"}
        self._year_url = {"2024": "https://www.whatnot.com/live/krakenhits", "2025": ""}
        self._year_token = {"2024": 1, "2025": 0}
        self.why = {}

    def _why_set(self, serial, text):
        self.why[serial] = text

    def _adb_call(self, serial, args, timeout=8):
        self.calls.append((serial, list(args)))
        return b""

    def _adb_host(self, args, timeout=8):
        raise AssertionError("ADB host must not be bounced")

    def _open_on_phone(self, serial, url):
        self.calls.append((serial, ["open", url]))

    def _click_look(self, serial):
        self.calls.append((serial, ["look"]))
        return "idle"


class FarmTests(unittest.TestCase):
    def test_sandbox_farm(self):
        with tempfile.TemporaryDirectory() as tmp:
            failures = run_checks(Path(tmp))
        self.assertEqual(failures, [])

    def test_devices_never_starts_a_server(self):
        import core.adb as adb

        seen = []

        def fake_call(args, timeout=20):
            seen.append(list(args))
            body = "List of devices attached\n" + "".join(f"S{i}\tdevice\n" for i in range(30))
            return body.encode()

        original = adb.call
        adb.call = fake_call
        try:
            rows = devices()
        finally:
            adb.call = original
        self.assertEqual(len(rows), 30)
        self.assertEqual(seen, [["devices"]])

    def test_five_misses_go_home_then_the_fourth_cycle_reloads(self):
        app = FakeApp()
        ctl = PhoneController(app, "serial")
        try:
            for _ in range(5):
                ctl._note_fail(app._year_url["2024"])
            homes = [c for c in app.calls if c[1][:3] == ["shell", "am", "start"]]
            opens = [c for c in app.calls if c[1][:1] == ["open"]]
            stops = [c for c in app.calls if "force-stop" in c[1]]
            self.assertEqual(len(homes), 1)
            self.assertEqual(len(opens), 1)
            self.assertEqual(stops, [])
            for _ in range(15):
                ctl._note_fail(app._year_url["2024"])
            stops = [c for c in app.calls if "force-stop" in c[1]]
            self.assertEqual(len(stops), 1)
            self.assertNotIn("reconnect", " ".join(" ".join(c[1]) for c in app.calls))
        finally:
            ctl.stop.set()
            ctl.poke()

    def test_start_off_does_not_look_or_tap(self):
        app = FakeApp()
        app._year_on = {"2024": False, "2025": False}
        app._years = {}
        app._year_url = {"2024": "", "2025": ""}
        ctl = PhoneController(app, "serial")
        try:
            ctl._pass()
        finally:
            ctl.stop.set()
            ctl.poke()
        self.assertEqual(app.calls, [])


if __name__ == "__main__":
    unittest.main()
