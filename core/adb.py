"""ADB helper. Device listing never waits behind shell commands."""

from __future__ import annotations
import shutil
import subprocess
import sys
import threading

_GATE = threading.Semaphore(8)


def adb_bin() -> str:
    return shutil.which("adb") or "adb"


def _flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _run(args, timeout: float, gate: bool) -> bytes:
    def once():
        return subprocess.check_output(
            [adb_bin(), *args],
            timeout=timeout,
            stderr=subprocess.DEVNULL,
            **_flags(),
        )

    if not gate:
        return once()
    with _GATE:
        return once()


def call(args, timeout: float = 20) -> bytes:
    return _run(list(args), timeout, gate=True)


def devices() -> list:
    """Every serial from `adb devices`. Does not restart a server that is already up."""
    try:
        out = _run(["devices"], 20, gate=False)
    except Exception:
        try:
            _run(["start-server"], 20, gate=False)
        except Exception:
            pass
        out = _run(["devices"], 20, gate=False)
    rows = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows
