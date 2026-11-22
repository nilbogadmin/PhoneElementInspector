"""Every adb call waits its turn. Nothing is dropped for being past a count."""

from __future__ import annotations
import shutil
import subprocess
import sys
import threading
import time

_GATE = threading.Semaphore(16)

_BIN = {"path": "", "checked": 0.0}


def adb_bin() -> str:
    if _BIN["path"]:
        return _BIN["path"]
    now = time.monotonic()
    if now - _BIN["checked"] > 15:
        _BIN["checked"] = now
        running = _running_adb()
        if running:
            _BIN["path"] = running
            return running
    return shutil.which("adb") or "adb"


def _running_adb() -> str:
    """Use the adb.exe that is already running, so we do not restart Panda's server."""
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='adb.exe'\" | Select-Object -ExpandProperty ExecutablePath",
            ],
            timeout=5,
            stderr=subprocess.DEVNULL,
            **_flags(),
        )
    except Exception:
        return ""
    for line in out.decode("utf-8", "replace").splitlines():
        path = line.strip().strip('"')
        if path.lower().endswith("adb.exe"):
            return path
    return ""


def _flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def call(args, timeout: float = 20) -> bytes:
    with _GATE:
        return subprocess.check_output(
            [adb_bin(), *args],
            timeout=timeout,
            stderr=subprocess.PIPE,
            **_flags(),
        )


def devices() -> list:
    """Every serial the already-running adb knows about. Never starts a server."""
    out = call(["devices"], timeout=8)
    rows = []
    for line in out.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows
