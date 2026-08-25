r"""Silent software/agent installation staged for first boot.

Copies installer files into the mounted image and appends commands to
`SetupComplete.cmd`, which Windows Setup runs automatically - once, as
SYSTEM - at the very end of the specialize pass on first boot, before any
user reaches the desktop. No `autounattend.xml` wiring is needed for this
file to run; Windows looks for it at a fixed path
(`%WINDIR%\Setup\Scripts\SetupComplete.cmd`).

Design choice - embedding into the WIM directly (this module) vs. the
classic `$OEM$` ISO-source-tree mechanism (`sources\$OEM$\$1\...` on the
install media, copied to disk by Setup before SetupComplete.cmd runs): this
project edits `install.wim` directly (see core.wim_manager), and
`SetupComplete.cmd` is looked up by the same fixed path regardless of which
mechanism placed the files there. Since the image is already mounted,
writing straight into `<mount>\Windows\Setup\Scripts\...` is simpler and
one moving part fewer than also assembling a parallel `$OEM$` tree on the
ISO's source files - so that legacy mechanism isn't implemented here.

Clean separation of *when* each step runs:
  - `stage_installer()` / `add_setup_complete_commands()` run NOW, while
    the image is mounted, during image preparation (this process, on the
    machine building the image).
  - Everything written into `SetupComplete.cmd` runs LATER, once, at first
    boot of the INSTALLED system - none of it executes during the build.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger("wct.software_injector")

_SCRIPTS_RELATIVE_DIR = r"Windows\Setup\Scripts"
_SETUP_COMPLETE_FILENAME = "SetupComplete.cmd"
_INSTALLERS_RELATIVE_DIR = r"Windows\Setup\Scripts\Installers"


def scripts_dir(mount_dir: str) -> Path:
    return Path(mount_dir) / _SCRIPTS_RELATIVE_DIR


def installers_dir(mount_dir: str) -> Path:
    return Path(mount_dir) / _INSTALLERS_RELATIVE_DIR


def setup_complete_path(mount_dir: str) -> Path:
    return scripts_dir(mount_dir) / _SETUP_COMPLETE_FILENAME


def _to_crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def stage_installer(mount_dir: str, installer_path: str | Path, *, dest_name: str | None = None) -> str:
    """Copy a local installer file into the image, to be run at first boot.

    Runs NOW, while the image is mounted (this is the "files copied into
    the WIM during image build" half of the split described in the module
    docstring - nothing here executes the installer).

    Returns the path the installer will have on the INSTALLED system (a
    real `C:\\...` path, not the mount directory), for building the
    SetupComplete.cmd command line.
    """
    installer_path = Path(installer_path)
    if not installer_path.is_file():
        raise FileNotFoundError(f"Installer not found: {installer_path}")

    dest_name = dest_name or installer_path.name
    dest_dir = installers_dir(mount_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / dest_name

    logger.info("Staging installer %s -> %s", installer_path, dest_path)
    shutil.copy2(installer_path, dest_path)

    return str(Path("C:\\") / _INSTALLERS_RELATIVE_DIR / dest_name)


def add_setup_complete_commands(mount_dir: str, commands: list[str]) -> None:
    """Append shell commands to `SetupComplete.cmd`, run once at first boot.

    Creates the file (with a standard `@echo off` + logging header) if it
    doesn't exist yet, otherwise appends - so multiple customization steps
    (several `stage_installer` + install-command pairs) can all contribute
    to the same script without clobbering each other. Each command is
    expected to be a single logical command line (e.g. one
    `start /wait "" installer.exe /S`); split multi-step logic into
    separate list entries rather than embedding newlines in one string.
    """
    scripts_dir(mount_dir).mkdir(parents=True, exist_ok=True)
    script_path = setup_complete_path(mount_dir)

    if not script_path.exists():
        header = _to_crlf(
            "@echo off\n"
            "setlocal EnableExtensions\n"
            'set "WCT_LOG=%WINDIR%\\Setup\\Scripts\\SetupComplete.log"\n'
            'echo [%date% %time%] SetupComplete.cmd started >> "%WCT_LOG%"\n'
        )
        script_path.write_text(header, encoding="utf-8", newline="")
        logger.info("Created %s", script_path)

    with script_path.open("a", encoding="utf-8", newline="") as f:
        for command in commands:
            stripped = command.strip()
            if not stripped:
                continue
            if len(stripped.splitlines()) > 1:
                raise ValueError(
                    "add_setup_complete_commands() expects one command per list "
                    f"entry, got multiple lines: {command!r}. Pass each command "
                    "as a separate list entry instead."
                )
            f.write(_to_crlf(f'echo [%date% %time%] Running: {stripped} >> "%WCT_LOG%"\n'))
            f.write(_to_crlf(stripped + "\n"))

    logger.info("Appended %d command(s) to %s", len(commands), script_path)


def stage_silent_install(
    mount_dir: str,
    installer_path: str | Path,
    silent_args: str,
    *,
    dest_name: str | None = None,
) -> None:
    """Copy `installer_path` into the image and queue its silent install for first boot.

    Convenience wrapper combining `stage_installer` +
    `add_setup_complete_commands` for the common "copy one installer, run
    it silently once" case (e.g. a Zabbix agent MSI). For anything more
    involved - multiple files, specific ordering, cleanup after install -
    call the two primitives directly instead.
    """
    installed_path = stage_installer(mount_dir, installer_path, dest_name=dest_name)
    command = f'start /wait "" "{installed_path}" {silent_args}'
    add_setup_complete_commands(mount_dir, [command])
