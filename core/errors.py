"""Shared exception types for the core package."""

from __future__ import annotations

from typing import Any


class DismError(RuntimeError):
    """Raised when a DISM invocation returns a non-zero exit code.

    Carries the raw args/returncode/output so a caller (CLI or GUI) can
    translate common DISM error codes into a user-friendly message instead
    of showing a raw stack trace.
    """

    def __init__(self, args: list[str], returncode: int, output: str) -> None:
        self.args = args
        self.returncode = returncode
        self.output = output
        super().__init__(
            f"DISM command failed (exit {returncode}): {' '.join(args)}\n{output}"
        )


class AdminRequiredError(RuntimeError):
    """Raised when an operation needs Administrator privileges but doesn't have them."""


class OrphanResolutionAborted(RuntimeError):
    """Raised when the caller chooses to abort instead of resolving an orphaned mount."""

    def __init__(self, image: Any) -> None:
        self.image = image
        super().__init__(f"Aborted while resolving orphaned mount at {image.mount_dir!r}")
