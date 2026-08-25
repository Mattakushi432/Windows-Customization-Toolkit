"""Shared structured logging setup for the toolkit's core.

Kept separate from any UI output: the GUI subscribes to log records (e.g.
via a `logging.Handler` that emits a Qt signal) or tails the log file,
rather than this module writing to a UI widget directly - so `core/` stays
free of UI dependencies.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".windows-customization-toolkit" / "logs"


def setup_logging(
    log_dir: Path | str = DEFAULT_LOG_DIR,
    *,
    file_level: int = logging.DEBUG,
    console_level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure the 'wct' logger tree with a rotating file handler.

    Safe to call more than once (e.g. from both a CLI entry point and
    tests) - existing handlers on the 'wct' logger are removed first so
    repeated calls don't duplicate log lines.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("wct")
    logger.setLevel(logging.DEBUG)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "toolkit.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger
