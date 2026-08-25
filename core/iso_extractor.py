"""Extracting an ISO's file tree into a working directory.

Uses Windows' built-in `Mount-DiskImage` / `Dismount-DiskImage` PowerShell
cmdlets - the same mechanism Explorer uses when you double-click an `.iso`
- so no extra tool (7-Zip, etc.) is required. This bridges "the user picked
an ISO file" to "there's a `sources\\install.wim` in a working directory
`core.wim_manager` can mount" and "there's a full ISO source tree
`core.iso_builder` can rebuild".

Not part of the original module list in win-iso-customizer-prompt.md's
Stage 1 plan - added here because it's the missing link the GUI's first
wizard step needs: without it, "select an ISO" can't reach "mount
install.wim".
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("wct.iso_extractor")

_POWERSHELL_EXE = "powershell.exe"
_ROBOCOPY_EXE = "robocopy.exe"

_MOUNT_SCRIPT = """
param([Parameter(Mandatory=$true)][string]$IsoPath)
$ErrorActionPreference = "Stop"
$image = Mount-DiskImage -ImagePath $IsoPath -PassThru
$volume = $image | Get-Volume
Write-Output $volume.DriveLetter
"""

_DISMOUNT_SCRIPT = """
param([Parameter(Mandatory=$true)][string]$IsoPath)
$ErrorActionPreference = "Stop"
Dismount-DiskImage -ImagePath $IsoPath | Out-Null
"""


class IsoExtractionError(RuntimeError):
    """Raised when mounting, copying, or dismounting an ISO fails."""


def _run_powershell_script(script: str, args: list[str]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(script)
        script_path = f.name
    try:
        full_args = [
            _POWERSHELL_EXE,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
            *args,
        ]
        logger.debug("Running: %s", " ".join(full_args))
        proc = subprocess.run(full_args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise IsoExtractionError(
                f"PowerShell script failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
            )
        return (proc.stdout or "").strip()
    finally:
        Path(script_path).unlink(missing_ok=True)


def mount_iso(iso_path: str | Path) -> str:
    """Mount `iso_path` as a drive, returning its drive letter (e.g. `'D'`)."""
    iso_path = Path(iso_path)
    if not iso_path.is_file():
        raise FileNotFoundError(f"ISO not found: {iso_path}")

    drive_letter = _run_powershell_script(_MOUNT_SCRIPT, [str(iso_path)])
    if not drive_letter:
        raise IsoExtractionError(f"Mount-DiskImage did not report a drive letter for {iso_path}")

    logger.info("Mounted %s as %s:\\", iso_path, drive_letter)
    return drive_letter


def dismount_iso(iso_path: str | Path) -> None:
    _run_powershell_script(_DISMOUNT_SCRIPT, [str(iso_path)])
    logger.info("Dismounted %s", iso_path)


def _robocopy_mirror(source_dir: Path, dest_dir: Path) -> None:
    """Copy a directory tree with robocopy.

    robocopy exit codes are a bitmask: 0-7 all indicate some form of
    success (files copied, or nothing needed copying); 8 or above means a
    real failure. Plain `shutil.copytree` is avoided here because ISO
    volumes commonly contain read-only files and long paths that trip it
    up more often than robocopy, which is built for exactly this.
    """
    args = [
        _ROBOCOPY_EXE,
        str(source_dir),
        str(dest_dir),
        "/E",
        "/R:2",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
    ]
    logger.debug("Running: %s", " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode >= 8:
        raise IsoExtractionError(
            f"robocopy failed copying {source_dir} -> {dest_dir} (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    logger.debug("robocopy exit code %d (0-7 is success)", proc.returncode)


def extract_iso(iso_path: str | Path, dest_dir: str | Path) -> Path:
    """Mount `iso_path` and copy its full contents into `dest_dir`.

    Always dismounts afterward, even if the copy fails, so a failed
    extraction doesn't leave the ISO mounted as a phantom drive letter.
    """
    iso_path = Path(iso_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    drive_letter = mount_iso(iso_path)
    try:
        source_root = Path(f"{drive_letter}:\\")
        logger.info("Copying %s -> %s", source_root, dest_dir)
        _robocopy_mirror(source_root, dest_dir)
    finally:
        dismount_iso(iso_path)

    return dest_dir
