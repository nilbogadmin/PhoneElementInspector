"""Phone picture as a video stream, not a screenshot every frame."""

from __future__ import annotations
import socket
import subprocess
import sys
import time
from pathlib import Path

from .log import log
from .paths import resource_dir

_VERSION = "2.7"
_SCID = "e110e11"
_SOCKET = "localabstract:scrcpy_0e110e11"
_PORT = 27199
_REMOTE = "/data/local/tmp/pei-scrcpy.jar"


def _adb() -> str:
    import shutil
    return shutil.which("adb") or "adb"


def _flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _jar() -> Path:
    bundled = resource_dir() / "vendor" / "scrcpy-server.jar"
    if bundled.exists():
        return bundled
    raise FileNotFoundError("scrcpy server missing")


def _run(cmd, timeout=20):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, **_flags())


def _read(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("mirror closed")
        buf.extend(chunk)
    return bytes(buf)


def _packets(sock: socket.socket, running):
    """Dummy byte, 64-byte name, 12-byte codec header, then sized frames."""
    _read(sock, 1 + 64 + 12)
    while running():
        header = _read(sock, 12)
        size = int.from_bytes(header[8:12], "big")
        if size <= 0 or size > 5_000_000:
            raise ConnectionError("bad video packet")
        yield _read(sock, size)


def iter_mirror(serial: str, running):
    """Yield RGB frames until running() is false."""
    import av

    jar = _jar()
    adb = _adb()
    proc = None
    sock = None
    try:
        _run([adb, "-s", serial, "push", str(jar), _REMOTE])
        _run([adb, "-s", serial, "forward", "--remove", f"tcp:{_PORT}"])
        _run([adb, "-s", serial, "forward", f"tcp:{_PORT}", _SOCKET])
        proc = subprocess.Popen(
            [
                adb, "-s", serial, "shell",
                f"CLASSPATH={_REMOTE} app_process / com.genymobile.scrcpy.Server {_VERSION} "
                f"scid={_SCID} tunnel_forward=true audio=false control=false cleanup=false "
                "max_size=1080 max_fps=30 video_bit_rate=4000000 video_codec=h264 "
                "stay_awake=false power_off_on_close=false",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_flags(),
        )
        sock = _connect()
        codec = av.CodecContext.create("h264", "r")
        codec.thread_count = 1
        shown = 0.0
        for nal in _packets(sock, running):
            frames = []
            for packet in codec.parse(nal):
                frames.extend(codec.decode(packet))
            if not frames:
                continue
            now = time.monotonic()
            if now - shown < 0.05:
                continue
            shown = now
            image = frames[-1].to_image()
            if image.mode != "RGB":
                image = image.convert("RGB")
            yield image
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            _run([adb, "-s", serial, "forward", "--remove", f"tcp:{_PORT}"], timeout=5)
        except Exception:
            pass


def _connect() -> socket.socket:
    last = None
    for _ in range(25):
        try:
            sock = socket.create_connection(("127.0.0.1", _PORT), timeout=2)
            sock.settimeout(1.0)
            return sock
        except Exception as exc:
            last = exc
            time.sleep(0.12)
    log(f"mirror connect failed {last}")
    raise ConnectionError("mirror did not start")
