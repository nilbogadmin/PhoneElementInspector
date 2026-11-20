"""ADB helper. The exe next to the app is used first."""

from __future__ import annotations
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_GATE = threading.Semaphore(8)
_seen = -1


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
                return str(path)
    return shutil.which("adb") or shutil.which("adb.exe") or "adb"


def _flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _run(args, timeout: float, gate: bool) -> subprocess.CompletedProcess:
    exe = adb_bin()
    cwd = str(Path(exe).resolve().parent) if Path(exe).is_file() else None

    def once():
        return subprocess.run(
            [exe, *args],
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            **_flags(),
        )

    if not gate:
        return once()
    with _GATE:
        return once()


def call(args, timeout: float = 20) -> bytes:
    done = _run(list(args), timeout, gate=True)
    if done.returncode != 0:
        err = (done.stderr or b"").decode("utf-8", "replace").strip()
        raise subprocess.CalledProcessError(done.returncode, done.args, output=done.stdout, stderr=done.stderr)
    return done.stdout or b""


def devices() -> list:
    """Every serial from `adb devices`."""
    from .log import log

    try:
        done = _run(["devices"], 20, gate=False)
    except Exception as exc:
        log(f"adb devices failed {exc} bin={adb_bin()}")
        done = None
    out = (done.stdout if done is not None else b"") or b""
    err = (done.stderr if done is not None else b"") or b""
    if b"List of devices" not in out:
        try:
            _run(["start-server"], 20, gate=False)
            done = _run(["devices"], 20, gate=False)
            out = done.stdout or b""
            err = done.stderr or b""
        except Exception as exc:
            log(f"adb start failed {exc} bin={adb_bin()}")
    if b"List of devices" not in out:
        log(f"adb bad output bin={adb_bin()} out={out[:180]!r} err={err[:180]!r}")
        return []
    rows = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    if len(rows) != _seen:
        log(f"adb {adb_bin()} sees {len(rows)}")
        globals()["_seen"] = len(rows)
    return rows
