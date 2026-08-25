"""Stage 0 CLI PoC: mount an install.wim via DISM, remove one provisioned
Appx package, commit, and unmount.

LEGITIMACY NOTE: this tool is intended for IT engineers preparing customized
corporate Windows images from licensed, officially obtained ISO/WIM sources
(removing preinstalled apps/telemetry, automating deployment). It does not
touch Windows activation/licensing and must not be used to do so.

Requires: Windows 10/11, DISM.exe (built-in), and an elevated (Administrator)
shell. This is a throwaway proof of concept for Stage 0 of the project plan -
not production code. It will be generalized into core/wim_manager.py etc. in
Stage 1 once this is confirmed to work reliably against a real image.

Example (run from an elevated PowerShell / cmd):
    python mount_and_debloat_poc.py ^
        --wim C:\\images\\install.wim --index 1 ^
        --mount-dir C:\\mount ^
        --package-match MicrosoftEdge

Discard instead of commit (leaves the .wim untouched):
    python mount_and_debloat_poc.py --wim ... --index 1 --mount-dir ... --discard

Only check for / clean up an orphaned mount, without doing anything else:
    python mount_and_debloat_poc.py --check-orphans-only
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "poc.log"

logger = logging.getLogger("wim_poc")


def setup_logging() -> None:
    """Configure logging to both the console and poc.log."""
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


class DismError(RuntimeError):
    """Raised when a DISM invocation returns a non-zero exit code."""

    def __init__(self, args: list[str], returncode: int, output: str) -> None:
        self.args = args
        self.returncode = returncode
        self.output = output
        super().__init__(
            f"DISM command failed (exit {returncode}): {' '.join(args)}\n{output}"
        )


def is_admin() -> bool:
    """Return True if the current process has Administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - non-Windows / unexpected failure
        return False


def run_dism(args: list[str]) -> str:
    """Run dism.exe with the given argument list and return combined output.

    Uses an argument list (never shell=True with a concatenated string) so
    paths and package names cannot be interpreted as shell syntax.
    """
    full_args = ["dism.exe", *args]
    logger.debug("Running: %s", " ".join(full_args))
    proc = subprocess.run(
        full_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    logger.debug("DISM output:\n%s", output)
    if proc.returncode != 0:
        raise DismError(full_args, proc.returncode, output)
    return output


def parse_dism_blocks(output: str) -> list[dict[str, str]]:
    """Parse DISM's blank-line-separated 'Key : Value' blocks into dicts.

    Both /Get-MountedWimInfo and /Get-ProvisionedAppxPackages use this
    format, so this parser is shared between them.
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


@dataclass
class MountedImage:
    mount_dir: str
    image_file: str
    image_index: str
    status: str


def get_mounted_wim_info() -> list[MountedImage]:
    """Wrap `dism /Get-MountedWimInfo` and return parsed mounted images."""
    output = run_dism(["/Get-MountedWimInfo"])
    images = []
    for block in parse_dism_blocks(output):
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


def handle_orphaned_mounts(mount_dir: str) -> None:
    """Detect any existing mounted images and let the user resolve them.

    This must run BEFORE attempting a fresh /Mount-Wim: a leftover mount
    from a previous crashed run will otherwise cause a confusing DISM
    failure instead of a clear recovery prompt.
    """
    mounted = get_mounted_wim_info()
    if not mounted:
        logger.info("No orphaned mount points found.")
        return

    for image in mounted:
        logger.warning(
            "Found existing mounted image: %s (file=%s, index=%s, status=%s)",
            image.mount_dir,
            image.image_file,
            image.image_index,
            image.status,
        )
        print(
            f"\nAn existing DISM mount was found:\n"
            f"  Mount Dir : {image.mount_dir}\n"
            f"  Image File: {image.image_file}\n"
            f"  Status    : {image.status}\n"
        )
        choice = ""
        while choice not in ("c", "d", "a"):
            choice = input(
                "Resolve it: [c]ommit, [d]iscard, [a]bort script? "
            ).strip().lower()

        if choice == "a":
            logger.info("User aborted due to orphaned mount.")
            raise SystemExit(1)

        flag = "/Commit" if choice == "c" else "/Discard"
        logger.info("Resolving orphaned mount %s with %s", image.mount_dir, flag)
        try:
            run_dism(["/Unmount-Wim", f"/MountDir:{image.mount_dir}", flag])
        except DismError:
            logger.exception(
                "Failed to resolve orphaned mount %s automatically. "
                "You may need to run 'dism /Cleanup-Mountpoints' manually.",
                image.mount_dir,
            )
            raise


def mount_wim(wim_path: str, index: int, mount_dir: str) -> None:
    Path(mount_dir).mkdir(parents=True, exist_ok=True)
    logger.info("Mounting %s (index %d) at %s", wim_path, index, mount_dir)
    run_dism(
        [
            "/Mount-Wim",
            f"/WimFile:{wim_path}",
            f"/Index:{index}",
            f"/MountDir:{mount_dir}",
        ]
    )
    logger.info("Mount succeeded.")


def get_provisioned_appx_packages(mount_dir: str) -> list[str]:
    output = run_dism(["/Image:" + mount_dir, "/Get-ProvisionedAppxPackages"])
    packages = []
    for block in parse_dism_blocks(output):
        name = block.get("PackageName") or block.get("Packagename")
        if name:
            packages.append(name)
    logger.info("Found %d provisioned Appx packages.", len(packages))
    return packages


def remove_provisioned_appx_package(mount_dir: str, package_name: str) -> None:
    logger.info("Removing provisioned package: %s", package_name)
    run_dism(
        [
            "/Image:" + mount_dir,
            "/Remove-ProvisionedAppxPackage",
            f"/PackageName:{package_name}",
        ]
    )
    logger.info("Package removed.")


def unmount_wim(mount_dir: str, commit: bool) -> None:
    flag = "/Commit" if commit else "/Discard"
    logger.info("Unmounting %s (%s)", mount_dir, flag)
    run_dism(["/Unmount-Wim", f"/MountDir:{mount_dir}", flag])
    logger.info("Unmount succeeded.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wim", help="Path to install.wim")
    parser.add_argument("--index", type=int, default=1, help="Image index to mount")
    parser.add_argument("--mount-dir", help="Directory to mount the image into")
    parser.add_argument(
        "--package-match",
        default="MicrosoftEdge",
        help="Substring to match against provisioned package names "
        "(default: MicrosoftEdge)",
    )
    parser.add_argument(
        "--discard",
        action="store_true",
        help="Discard changes on unmount instead of committing them",
    )
    parser.add_argument(
        "--check-orphans-only",
        action="store_true",
        help="Only check for and resolve orphaned mount points, then exit",
    )
    args = parser.parse_args(argv)

    if not args.check_orphans_only and (not args.wim or not args.mount_dir):
        parser.error("--wim and --mount-dir are required unless --check-orphans-only")

    return args


def main(argv: list[str]) -> int:
    setup_logging()
    args = parse_args(argv)

    if not is_admin():
        logger.error(
            "This script must be run from an elevated (Administrator) shell. "
            "DISM mount/unmount operations require Administrator privileges."
        )
        return 1

    if args.check_orphans_only:
        handle_orphaned_mounts(mount_dir="")
        return 0

    wim_path = str(Path(args.wim).resolve())
    mount_dir = str(Path(args.mount_dir).resolve())

    handle_orphaned_mounts(mount_dir)

    mounted = False
    try:
        mount_wim(wim_path, args.index, mount_dir)
        mounted = True

        packages = get_provisioned_appx_packages(mount_dir)
        matches = [p for p in packages if args.package_match.lower() in p.lower()]

        if not matches:
            logger.warning(
                "No provisioned package matched '%s'. Available packages:\n%s",
                args.package_match,
                "\n".join(packages),
            )
        else:
            for package_name in matches:
                remove_provisioned_appx_package(mount_dir, package_name)

        unmount_wim(mount_dir, commit=not args.discard)
        mounted = False

    except Exception:
        logger.exception("PoC run failed.")
        if mounted:
            logger.warning("Attempting to discard the mount to leave a clean state.")
            try:
                unmount_wim(mount_dir, commit=False)
            except DismError:
                logger.exception(
                    "Automatic discard also failed. Run 'dism /Get-MountedWimInfo' "
                    "and 'dism /Unmount-Wim /MountDir:%s /Discard' manually.",
                    mount_dir,
                )
        return 1

    logger.info("PoC run completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
