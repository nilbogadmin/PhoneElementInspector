"""One thread per phone. It retries that phone until the command lands."""

from __future__ import annotations
import threading
import time

from .log import log

_CLOSE = (
    "for p in $(pm list packages -3 | cut -d: -f2); do am force-stop $p; done; "
    "ids=$(dumpsys activity recents | grep -oE '#[0-9]+ type=|taskId=[0-9]+' | grep -oE '[0-9]+' | sort -u); "
    "for id in $ids; do cmd activity remove-task $id; am stack remove $id; done; "
    "am kill-all; input keyevent 3"
)


class PhoneController:
    def __init__(self, app, serial: str):
        self.app = app
        self.serial = serial
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.year = ""
        self.want_close = 0
        self.did_close = 0
        self.stream_token = -1
        self.full_at = 0.0
        self.fails = 0
        self.rounds = 0
        self.reason = "starting"
        threading.Thread(target=self._run, name=f"phone-{serial[-6:]}", daemon=True).start()

    def poke(self):
        self.wake.set()

    def request_close(self):
        with self.lock:
            self.want_close += 1
        self.poke()

    def _run(self):
        while not self.stop.is_set():
            self.wake.wait(1.0)
            self.wake.clear()
            if self.stop.is_set():
                return
            try:
                self._pass()
            except Exception as exc:
                log(f"{self.serial} controller {exc}")
                self._reconnect()
                self.stop.wait(1.5)

    def _pass(self):
        app = self.app
        serial = self.serial
        year = app._phone_year(serial) or self.year
        if year not in ("2024", "2025"):
            if not app._adb_alive(serial):
                app._why_set(serial, "adb down")
                self._reconnect()
            else:
                app._why_set(serial, "reading model")
            return
        self.year = year
        with self.lock:
            close_n = self.want_close
        if close_n != self.did_close:
            self._close_apps()
            self.did_close = close_n
        token = app._year_token.get(year, 0)
        url = app._year_url.get(year) or ""
        if url and token != self.stream_token:
            if app._stream_is_open(serial, url):
                self.stream_token = token
                self._note_ok()
                app._why_set(serial, "on stream")
                app._mark_stream(serial, year, True)
                log(f"{serial} stream ok")
            elif app._deliver_stream(serial, url):
                self.stream_token = token
                app._phone_url = getattr(app, "_phone_url", {})
                app._phone_url[serial] = url
                self._note_ok()
                app._why_set(serial, "on stream")
                app._mark_stream(serial, year, True)
                log(f"{serial} stream ok")
            else:
                app._mark_stream(serial, year, False)
                app._why_set(serial, f"stream not open try {self.fails + 1}/5")
                log(f"{serial} stream again")
                self._note_fail(url)
        if app._year_on.get(year):
            result = app._click_look(serial)
            live = app._year_url.get(year) or ""
            if result == "miss":
                app._why_set(serial, f"tap still there try {self.fails + 1}/5")
                self._note_fail(live)
            elif result == "hit":
                app._why_set(serial, "tap landed")
                self._note_ok()
        now = time.monotonic()
        if now - self.full_at > 300:
            self.full_at = now
            self._fullscreen()

    def _note_ok(self):
        self.fails = 0
        self.rounds = 0

    def _note_fail(self, url: str):
        """Five look-and-click tries, then home and the stream. Three of those, then kill the app."""
        self.fails += 1
        log(f"{self.serial} try {self.fails}/5")
        if self.fails < 5:
            return
        self.fails = 0
        self.rounds += 1
        if self.rounds <= 3:
            log(f"{self.serial} whatnot home then stream {self.rounds}/3")
            self._whatnot_home()
            self.stop.wait(1.2)
            if url:
                try:
                    self.app._open_on_phone(self.serial, url)
                except Exception as exc:
                    log(f"{self.serial} stream reload failed {exc}")
            return
        log(f"{self.serial} reload app")
        self.rounds = 0
        self._reload_app(url)

    def _whatnot_home(self):
        try:
            self.app._adb_call(
                self.serial,
                ["shell", "am", "start", "-a", "android.intent.action.MAIN",
                 "-c", "android.intent.category.LAUNCHER", "-p", "com.whatnot_mobile"],
                timeout=8,
            )
        except Exception as exc:
            log(f"{self.serial} whatnot home failed {exc}")

    def _reload_app(self, url: str):
        try:
            self.app._adb_call(self.serial, ["shell", "am", "force-stop", "com.whatnot_mobile"], timeout=6)
        except Exception as exc:
            log(f"{self.serial} force-stop failed {exc}")
        self.stop.wait(1.0)
        if not url:
            return
        try:
            self.app._open_on_phone(self.serial, url)
        except Exception as exc:
            log(f"{self.serial} app reload failed {exc}")

    def _close_apps(self):
        while not self.stop.is_set():
            try:
                self.app._adb_call(self.serial, ["shell", _CLOSE], timeout=30)
                log(f"{self.serial} apps closed")
                return
            except Exception as exc:
                log(f"{self.serial} close again {exc}")
                self._reconnect()
                self.stop.wait(2)

    def _fullscreen(self):
        want = "immersive.full=com.whatnot_mobile"
        try:
            cur = self.app._adb_call(self.serial, ["shell", "settings", "get", "global", "policy_control"], timeout=4)
            if want in cur.decode("utf-8", "replace"):
                return
            self.app._adb_call(self.serial, ["shell", "settings", "put", "global", "policy_control", want], timeout=4)
            log(f"{self.serial} fullscreen on")
        except Exception as exc:
            log(f"{self.serial} fullscreen again {exc}")

    def _reconnect(self):
        try:
            self.app._adb_host(["-s", self.serial, "reconnect"], timeout=8)
            log(f"{self.serial} reconnect")
        except Exception as exc:
            log(f"{self.serial} reconnect failed {exc}")
