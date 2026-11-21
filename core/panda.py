"""Talk to the Panda mirror already running on this PC. Never starts a second adb."""

from __future__ import annotations
import base64
import json
import os
import socket
import time

_CACHE = {"at": 0.0, "phones": None}


def _connect(timeout: float = 0.6):
    sock = socket.create_connection(("127.0.0.1", 22222), timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        (
            "GET / HTTP/1.1\r\n"
            "Host: 127.0.0.1:22222\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        sock.close()
        raise RuntimeError("panda is not answering")
    return sock


def _send(sock, text: str):
    data = text.encode()
    n = len(data)
    mask = os.urandom(4)
    header = bytearray([0x81])
    if n < 126:
        header.append(0x80 | n)
    else:
        header.append(0x80 | 126)
        header += n.to_bytes(2, "big")
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _recv(sock) -> str:
    def exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("panda closed")
            buf += chunk
        return buf

    b0, b1 = exact(2)
    ln = b1 & 0x7F
    if ln == 126:
        ln = int.from_bytes(exact(2), "big")
    elif ln == 127:
        ln = int.from_bytes(exact(8), "big")
    if b1 & 0x80:
        exact(4)
    payload = exact(ln)
    if (b0 & 0x0F) != 1:
        return ""
    return payload.decode("utf-8", "replace")


def ask(payload: dict, timeout: float = 1.2):
    sock = _connect(min(timeout, 0.8))
    try:
        sock.settimeout(timeout)
        _send(sock, json.dumps(payload))
        return json.loads(_recv(sock) or "{}")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def phones() -> list:
    now = time.monotonic()
    if _CACHE["phones"] is not None and now - _CACHE["at"] < 1.0:
        return list(_CACHE["phones"])
    try:
        msg = ask({"action": "list", "devices": "all"}, timeout=1.2)
    except Exception:
        return []
    rows = msg.get("data") or []
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        serial = str(row.get("onlySerial") or row.get("serial") or "").strip()
        if not serial:
            continue
        found.append({
            "serial": serial,
            "model": str(row.get("model") or row.get("name") or ""),
            "width": int(row.get("sourceWidth") or 0),
            "height": int(row.get("sourceHeight") or 0),
        })
    _CACHE["at"] = now
    _CACHE["phones"] = found
    return list(found)


def shell(serial: str, command: str, timeout: float = 2.0) -> bool:
    try:
        msg = ask(
            {"action": "adb", "devices": serial, "data": {"command": command}},
            timeout=timeout,
        )
    except Exception:
        return False
    code = msg.get("code")
    return code in (10000, 200, "10000", "200") or msg.get("message") == "SUCCESS"
