"""Ask the adb already running for its phone list. Never start or kill the server."""

from __future__ import annotations
import shutil
import subprocess
import sys


def adb_bin() -> str:
    return shutil.which("adb") or "adb"


def _run(args, timeout: float = 15) -> subprocess.CompletedProcess:
    kwargs = {
        "args": [adb_bin(), *args],
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
        kwargs["creationflags"] = 0x08000000
    return subprocess.run(**kwargs)


def devices() -> list:
    done = _run(["devices"])
    text = (done.stdout or b"").decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "offline", "unauthorized", "authorizing"):
            rows.append((parts[0], parts[1]))
    if rows:
        return rows
    # One more try without the no-window flag. A windowed exe sometimes drops the first capture.
    done = subprocess.run(
        [adb_bin(), "devices"],
        timeout=15,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    text = (done.stdout or b"").decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "offline", "unauthorized", "authorizing"):
            rows.append((parts[0], parts[1]))
    return rows
