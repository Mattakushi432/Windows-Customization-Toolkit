"""Resolves bundled resource paths (e.g. `config/appx_debloat_list.json`)
whether running from source or from a PyInstaller-frozen executable.

PyInstaller extracts bundled `datas` to a temp directory referenced by
`sys._MEIPASS` (`--onefile`) or places them next to the executable
(`--onedir`) - neither matches a `__file__`-relative path once frozen, so
any code that needs a bundled resource must go through `resource_path()`
instead of hardcoding a path relative to its own module file. See
`packaging/pyinstaller.spec`, whose `datas` entry this must stay in sync
with.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Path to a bundled resource, correct both from source and when frozen."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]  # repo root
    return base.joinpath(*parts)
