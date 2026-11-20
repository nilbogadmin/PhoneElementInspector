"""Plain adb devices. Does not restart the server and does not replace the adb already on PATH."""

from __future__ import annotations
import shutil
import subprocess
import sys


def adb_bin() -> str:
    return shutil.which("adb") or "adb"


def devices() -> list:
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    out = subprocess.check_output(
        [adb_bin(), "devices"],
        timeout=8,
        stderr=subprocess.DEVNULL,
        **flags,
    )
    rows = []
    for line in out.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows
