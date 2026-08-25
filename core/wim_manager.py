"""Mount/unmount/commit/discard orchestration for WIM images.

Includes orphaned-mount detection and resolution so a crashed prior run
doesn't leave DISM's mount table (and the underlying .wim) in an
inconsistent state - see `resolve_orphaned_mounts`. UI-agnostic: orphan
resolution and the mount lifecycle are driven by plain function calls and a
resolver callback, never `input()` or a Qt dialog directly, so both a CLI
and the future GUI can use the same core logic.
"""

from __future__ import annotations

import ctypes
import enum
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from core import dism_runner
from core.errors import AdminRequiredError, DismError, OrphanResolutionAborted

logger = logging.getLogger("wct.wim_manager")


def is_admin() -> bool:
    """Return True if the current process has Administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def require_admin() -> None:
    """Raise AdminRequiredError if not running elevated.

    DISM's /Mount-Wim and /Unmount-Wim fail (or behave inconsistently)
    without Administrator rights. Call this before any mount/unmount so the
    caller gets one clear, specific error instead of a raw DISM failure.
    """
    if not is_admin():
        raise AdminRequiredError(
            "Administrator privileges are required for DISM mount/unmount "
            "operations. Re-run from an elevated shell."
        )


@dataclass(frozen=True)
class MountedImage:
    mount_dir: str
    image_file: str
    image_index: str
    status: str


class OrphanAction(enum.Enum):
    COMMIT = "commit"
    DISCARD = "discard"
    ABORT = "abort"


OrphanResolver = Callable[[MountedImage], OrphanAction]


def get_mounted_wim_info() -> list[MountedImage]:
    """Wrap `dism /Get-MountedWimInfo` and return parsed mounted images."""
    output = dism_runner.run(["/Get-MountedWimInfo"])
    images = []
    for block in dism_runner.parse_blocks(output):
        if "Mount Dir" not in block:
            continue
        images.append(
            MountedImage(
                mount_dir=block.get("Mount Dir", ""),
                image_file=block.get("Image File", ""),
                image_index=block.get("Image Index", ""),
                status=block.get("Status", ""),
            )
        )
    return images


def resolve_orphaned_mounts(resolver: OrphanResolver) -> list[MountedImage]:
    """Detect any existing mounted images and resolve each via `resolver`.

    `resolver(image)` decides COMMIT/DISCARD/ABORT per image, keeping this
    function UI-agnostic - a CLI can implement it with `input()`, a GUI
    with a modal dialog. Must be called before a fresh `mount_wim()` so a
    leftover mount from a previous crashed run is handled explicitly
    instead of causing a confusing DISM failure later.

    Returns the list of images that were found (and resolved).
    """
    mounted = get_mounted_wim_info()
    for image in mounted:
        logger.warning(
            "Found existing mounted image: %s (file=%s, index=%s, status=%s)",
            image.mount_dir,
            image.image_file,
            image.image_index,
            image.status,
        )
        action = resolver(image)
        if action is OrphanAction.ABORT:
            logger.info("Orphaned mount resolution aborted by caller.")
            raise OrphanResolutionAborted(image)

        flag = "/Commit" if action is OrphanAction.COMMIT else "/Discard"
        logger.info("Resolving orphaned mount %s with %s", image.mount_dir, flag)
        dism_runner.run(["/Unmount-Wim", f"/MountDir:{image.mount_dir}", flag])
    return mounted


def mount_wim(wim_path: str, index: int, mount_dir: str, *, read_only: bool = False) -> None:
    """Mount `wim_path` at index `index` into `mount_dir`, creating it if needed."""
    require_admin()
    Path(mount_dir).mkdir(parents=True, exist_ok=True)
    args = [
        "/Mount-Wim",
        f"/WimFile:{wim_path}",
        f"/Index:{index}",
        f"/MountDir:{mount_dir}",
    ]
    if read_only:
        args.append("/ReadOnly")
    logger.info(
        "Mounting %s (index %d) at %s%s",
        wim_path,
        index,
        mount_dir,
        " [read-only]" if read_only else "",
    )
    dism_runner.run(args)
    logger.info("Mount succeeded.")


def unmount_wim(mount_dir: str, *, commit: bool) -> None:
    """Unmount `mount_dir`, committing or discarding the changes made to it."""
    require_admin()
    flag = "/Commit" if commit else "/Discard"
    logger.info("Unmounting %s (%s)", mount_dir, flag)
    dism_runner.run(["/Unmount-Wim", f"/MountDir:{mount_dir}", flag])
    logger.info("Unmount succeeded.")


@contextmanager
def mounted_wim(
    wim_path: str,
    index: int,
    mount_dir: str,
    *,
    read_only: bool = False,
) -> Iterator[str]:
    """Mount `wim_path` for the duration of the `with` block.

    On a clean exit, commits the changes - unless `read_only`, which is
    always discarded, since a read-only mount can't be serviced and DISM
    would reject a commit on it anyway. On any exception raised inside the
    block, discards instead, so a failure partway through customization
    never leaves an unwanted partial change committed to the image.

    If the recovery discard itself also fails, the mount is left in place
    (not retried blindly) and logged clearly - resolve it on the next run
    via `resolve_orphaned_mounts`.
    """
    mount_wim(wim_path, index, mount_dir, read_only=read_only)
    finished = False
    try:
        yield mount_dir
        unmount_wim(mount_dir, commit=not read_only)
        finished = True
    finally:
        if not finished:
            logger.warning(
                "Exception while image was mounted; discarding changes to leave a clean state."
            )
            try:
                unmount_wim(mount_dir, commit=False)
            except DismError:
                logger.exception(
                    "Automatic discard failed after an error; %s may still be mounted. "
                    "Run resolve_orphaned_mounts() or "
                    "'dism /Unmount-Wim /MountDir:%s /Discard' manually.",
                    mount_dir,
                    mount_dir,
                )
