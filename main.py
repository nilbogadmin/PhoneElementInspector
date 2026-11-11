#!/usr/bin/env python3
"""Phone Element Inspector. Double-click the Windows exe, or run this file."""

import os
import shutil
import sys
from pathlib import Path


def _boot() -> None:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        bundled = Path(getattr(sys, "_MEIPASS", root))
        sys.path.insert(0, str(bundled))
        for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            src = bundled / name
            dst = root / name
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
        os.environ["PATH"] = str(root) + os.pathsep + os.environ.get("PATH", "")
        (root / "data").mkdir(exist_ok=True)
        for name in ("tags.json", "rules.json"):
            src = bundled / "data" / name
            dst = root / "data" / name
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
    else:
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))


_boot()

from gui.compact_window import CompactController


def main():
    app = CompactController()
    app.mainloop()


if __name__ == "__main__":
    main()
