#!/usr/bin/env python3
"""Launcher for AI Agent Desktop.

Supports in-process "restart" by re-execing the current Python command line when the
Qt event loop exits with the restart sentinel code.
"""

from __future__ import annotations

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.app import Application
from src.app_handlers.bus_app_lifecycle import RESTART_EXIT_CODE


def main() -> int:
    app = Application()
    code = int(app.run() or 0)

    if code == RESTART_EXIT_CODE:
        # Replace the current process with a fresh instance.
        # Works whether launched via run.bat or directly via python run.py.
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            return code

    return code


if __name__ == "__main__":
    sys.exit(main())
