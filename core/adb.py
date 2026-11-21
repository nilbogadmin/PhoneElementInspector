"""Every adb call waits its turn. Nothing is dropped for being past a count."""

from __future__ import annotations
import shutil
import subprocess
import sys
import threading

_GATE = threading.Semaphore(16)


def adb_bin() -> str:
    return shutil.which("adb") or "adb"


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
    """Every serial adb currently knows about. Does not restart a server that is already up."""
    try:
        out = call(["devices"], timeout=8)
    except Exception:
        try:
            call(["start-server"], timeout=15)
        except Exception:
            pass
        out = call(["devices"], timeout=8)
    rows = []
    for line in out.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows
