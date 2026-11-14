"""Plain text log next to the exe: data/log.txt"""

from __future__ import annotations
import threading
from datetime import datetime

from .paths import app_dir

_lock = threading.Lock()


def log(msg: str) -> None:
    try:
        path = app_dir() / "data" / "log.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = datetime.now().strftime("%H:%M:%S ") + str(msg).replace("\n", " ") + "\n"
        with _lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            if path.stat().st_size > 400_000:
                text = path.read_text(encoding="utf-8", errors="replace")
                path.write_text(text[-180_000:], encoding="utf-8")
    except Exception:
        pass
