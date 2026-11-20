"""Find the adb next to the app and list every phone it sees."""

from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path


def adb_bin() -> str:
    names = ("adb.exe", "adb") if sys.platform == "win32" else ("adb",)
    folders = []
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        folders.append(here)
        folders.append(Path(getattr(sys, "_MEIPASS", here)))
    folders.append(Path(__file__).resolve().parent.parent)
    for folder in folders:
        for name in names:
            path = folder / name
            if path.is_file():
                os.environ["ADBUTILS_ADB_PATH"] = str(path)
                return str(path)
    found = shutil.which("adb") or shutil.which("adb.exe") or "adb"
    if "WindowsApps" in found.replace("/", "\\"):
        found = "adb"
    os.environ["ADBUTILS_ADB_PATH"] = found
    return found


def _run(args, timeout: float = 20) -> subprocess.CompletedProcess:
    exe = adb_bin()
    kwargs = {
        "timeout": timeout,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "cwd": str(Path(exe).resolve().parent) if Path(exe).is_file() else None,
    }
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
    return subprocess.run([exe, *args], **kwargs)


def devices() -> list:
    """Every phone from `adb devices`, including offline and unauthorized."""
    from .log import log

    done = _run(["devices"], 20)
    out = (done.stdout or b"").decode("utf-8", "replace")
    err = (done.stderr or b"").decode("utf-8", "replace")
    if "List of devices" not in out:
        _run(["start-server"], 20)
        done = _run(["devices"], 20)
        out = (done.stdout or b"").decode("utf-8", "replace")
        err = (done.stderr or b"").decode("utf-8", "replace")
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    if not rows:
        log(f"adb empty bin={adb_bin()} out={out[:160]!r} err={err[:160]!r}")
    return rows
