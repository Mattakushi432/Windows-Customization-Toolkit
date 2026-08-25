import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from gui.blocking import run_blocking


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QCoreApplication.instance() or QApplication([])


def test_run_blocking_returns_result_of_successful_call() -> None:
    def slow_success() -> int:
        time.sleep(0.05)
        return 42

    result = run_blocking(None, slow_success, label="Testing...")

    assert result == 42


def test_run_blocking_reraises_exception_on_caller_thread() -> None:
    def slow_failure() -> None:
        time.sleep(0.05)
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_blocking(None, slow_failure, label="Testing...")


def test_run_blocking_does_not_block_qt_event_loop_forever() -> None:
    # Regression guard: run_blocking must actually return control, not
    # deadlock the calling thread's event loop against the worker thread.
    start = time.time()
    run_blocking(None, lambda: time.sleep(0.05), label="Testing...")
    elapsed = time.time() - start

    assert elapsed < 5.0
