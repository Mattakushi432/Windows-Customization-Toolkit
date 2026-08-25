"""GUI entry point. Run with `python -m gui.main`.

DISM mount/unmount and offline registry hive load/unload require
Administrator privileges. Per win-iso-customizer-prompt.md section 2,
elevation is requested via the packaged `.exe`'s manifest
(`requireAdministrator`, added in Stage 6 / PyInstaller packaging) - not
via a `runas` trick from inside Python. Run from source (as here), this
only warns if not elevated rather than attempting to self-elevate.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from core import wim_manager
from core.logging_config import setup_logging
from gui.wizard import CustomizerWizard


def main() -> int:
    setup_logging()
    logger = logging.getLogger("wct.gui.main")

    app = QApplication(sys.argv)

    if not wim_manager.is_admin():
        logger.warning(
            "Not running elevated - DISM/registry operations will fail until "
            "the app is run as Administrator."
        )
        QMessageBox.warning(
            None,
            "Administrator privileges required",
            "This tool needs Administrator privileges for DISM and registry "
            "operations. You can browse the wizard, but the build step will "
            "fail until you re-launch this app as Administrator.",
        )

    wizard = CustomizerWizard()
    wizard.resize(800, 600)
    wizard.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
