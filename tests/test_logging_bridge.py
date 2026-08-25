import logging

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from gui.logging_bridge import QtLogHandler


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QCoreApplication.instance() or QApplication([])


def test_qt_log_handler_emits_signal_with_formatted_message_and_level() -> None:
    handler = QtLogHandler()
    received: list[tuple[str, int]] = []
    handler.log_record.connect(lambda msg, level: received.append((msg, level)))

    logger = logging.getLogger("test.logging_bridge.one")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.info("hello from test")
    finally:
        logger.removeHandler(handler)

    assert len(received) == 1
    message, levelno = received[0]
    assert "hello from test" in message
    assert levelno == logging.INFO


def test_qt_log_handler_respects_level_filter() -> None:
    handler = QtLogHandler(level=logging.WARNING)
    received: list[str] = []
    handler.log_record.connect(lambda msg, level: received.append(msg))

    logger = logging.getLogger("test.logging_bridge.two")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.info("should be filtered out")
        logger.warning("should come through")
    finally:
        logger.removeHandler(handler)

    assert len(received) == 1
    assert "should come through" in received[0]


def test_qt_log_handler_survives_formatter_exception() -> None:
    handler = QtLogHandler()
    received: list[str] = []
    handler.log_record.connect(lambda msg, level: received.append(msg))

    class BoomFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise ValueError("formatter boom")

    handler.setFormatter(BoomFormatter())
    logger = logging.getLogger("test.logging_bridge.three")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.info("raw message")
    finally:
        logger.removeHandler(handler)

    assert received == ["raw message"]
