"""PyInstaller entry point.

Equivalent to `python -m gui.main`, but as a plain script - PyInstaller's
`Analysis` needs a real script file to target, not a `-m` module
invocation. See packaging/pyinstaller.spec.
"""

from gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())
