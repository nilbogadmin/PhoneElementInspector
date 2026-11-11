#!/usr/bin/env python3
"""
Phone Element Inspector
-----------------------
See every UI element on a connected Android phone (or Demo mode),
outline them on a virtual phone view, tag the ones you care about,
and build simple "if this element is showing → do this" rules.
"""

import sys
from pathlib import Path

# Make sure the project root is on the path
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui.compact_window import CompactController

def main():
    app = CompactController()
    app.mainloop()


if __name__ == "__main__":
    main()
