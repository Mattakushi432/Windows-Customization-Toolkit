"""Bridges the core `wct` logger into a Qt signal for the GUI's log panel.

`core/` logs via the standard `logging` module and knows nothing about Qt
(see `core.logging_config`). This handler is the one place that crosses
that boundary: attach it to the `wct` logger and connect `log_record` to a
`QPlainTextEdit`. Because this `QObject` lives on the main thread while the
worker thread's logger calls `emit()` from a background thread, Qt
automatically marshals the signal to the main thread via a queued
connection - the log panel is safe to update from the slot without extra
locking.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class QtLogHandler(logging.Handler, QObject):
    """A `logging.Handler` that emits a Qt Signal for each record."""

    log_record = Signal(str, int)  # formatted message, levelno

    def __init__(self, level: int = logging.NOTSET) -> None:
        logging.Handler.__init__(self, level)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.log_record.emit(message, record.levelno)
