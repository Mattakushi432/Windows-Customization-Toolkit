"""Run a blocking core/ call on a background thread while keeping the GUI responsive.

Several wizard steps (extracting the ISO, read-only-mounting to list Appx
packages) need to finish a slow `core/` operation before the wizard can
move to the next page, but must not freeze the window while doing it.
`run_blocking()` runs the callable on a `QThread` and pumps a local
`QEventLoop` until it's done, showing an indeterminate `QProgressDialog` -
the wizard page code that calls it can stay written as ordinary sequential
code instead of manually wiring up signals for every step.

This is different from `gui.worker.PipelineWorker`, which is used for the
one long, user-facing "Build" step where a real progress bar and live log
output matter; this helper is for the shorter setup steps that just need
"don't freeze while this runs".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

T = TypeVar("T")


class _CallableRunner(QObject):
    finished = Signal(object, object)  # result, exception

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # re-raised on the caller's thread by run_blocking
            self.finished.emit(None, exc)
        else:
            self.finished.emit(result, None)


def run_blocking(parent: QWidget | None, fn: Callable[[], T], *, label: str) -> T:
    """Run `fn` on a background thread; block the caller until it's done.

    Re-raises whatever exception `fn` raised, on the calling (GUI) thread,
    so ordinary `try`/`except` around a `run_blocking()` call works.
    """
    dialog = QProgressDialog(label, None, 0, 0, parent)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setCancelButton(None)

    loop = QEventLoop()
    thread = QThread()
    runner = _CallableRunner(fn)
    runner.moveToThread(thread)

    outcome: dict[str, object] = {}

    def _on_finished(result: object, exc: object) -> None:
        outcome["result"] = result
        outcome["exc"] = exc
        thread.quit()

    runner.finished.connect(_on_finished)
    thread.started.connect(runner.run)
    thread.finished.connect(loop.quit)

    dialog.show()
    thread.start()
    loop.exec()

    dialog.close()
    thread.wait()

    exc = outcome.get("exc")
    if exc is not None:
        raise exc  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]
