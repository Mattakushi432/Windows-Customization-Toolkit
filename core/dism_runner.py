"""Low-level wrapper around dism.exe: process invocation and shared output
parsing.

Every other core module (wim_manager, image_info, appx_cleaner) goes
through `run()` here rather than calling subprocess directly, so there is
exactly one place that knows how to invoke DISM safely and one place to
mock in tests.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from core.errors import DismError

logger = logging.getLogger("wct.dism")

_DISM_EXE = "dism.exe"


def find_dism() -> str:
    """Locate dism.exe, raising a clear error if it's not on PATH.

    DISM ships with every Windows 10/11 install (System32\\dism.exe), so a
    missing binary almost always means this isn't running on Windows, or
    PATH has been stripped in a locked-down shell - worth a clear message
    rather than a confusing FileNotFoundError from subprocess itself.
    """
    path = shutil.which(_DISM_EXE)
    if path is None:
        raise FileNotFoundError(
            "dism.exe was not found on PATH. This tool requires Windows 10/11 "
            "with the built-in DISM utility available."
        )
    return path


def run(args: list[str], *, timeout: float | None = None) -> str:
    """Run dism.exe with an argument list and return combined stdout+stderr.

    Always uses an argument list (never a concatenated shell string) so
    paths, package names, and usernames can never be interpreted as shell
    syntax - this is the injection-safety boundary for every DISM call in
    the project.
    """
    dism_path = find_dism()
    full_args = [dism_path, *args]
    logger.debug("Running: %s", " ".join(full_args))
    proc = subprocess.run(
        full_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    logger.debug("DISM output:\n%s", output)
    if proc.returncode != 0:
        raise DismError(full_args, proc.returncode, output)
    return output


def parse_blocks(output: str) -> list[dict[str, str]]:
    """Parse DISM's blank-line-separated 'Key : Value' blocks into dicts.

    Shared by /Get-MountedWimInfo, /Get-ProvisionedAppxPackages and
    /Get-WimInfo, which all emit this same textual format. Lines without a
    colon (banner text, "The operation completed successfully.") are
    skipped rather than raising, since DISM always prints a header/footer
    around the data blocks we actually care about.
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        current[key] = value
    if current:
        blocks.append(current)
    return blocks
