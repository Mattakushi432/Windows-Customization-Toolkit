"""Provisioned Appx package enumeration and removal.

Driven by a configurable pattern list loaded from JSON (see
config/appx_debloat_list.json) rather than a hardcoded blocklist, so the
set of removed packages can be updated without a code release.

Note: not every commonly "debloated" app is a provisioned Appx package.
Modern (Chromium) Edge and OneDrive, for example, are Win32 installs, not
provisioned Appx packages, and cannot be removed via
`/Remove-ProvisionedAppxPackage` - they need a different approach (offline
registry tweak or an uninstall command run at first boot), which belongs in
a later customization module, not here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from core import dism_runner

logger = logging.getLogger("wct.appx_cleaner")

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ProvisionedPackage:
    package_name: str
    display_name: str
    publisher_id: str


def get_provisioned_appx_packages(mount_dir: str) -> list[ProvisionedPackage]:
    """List provisioned Appx packages actually present in the mounted image.

    Always query the live image rather than assuming a fixed set - editions
    differ in which packages they ship, so a UI should only offer removal
    of packages confirmed present here.
    """
    output = dism_runner.run(["/Image:" + mount_dir, "/Get-ProvisionedAppxPackages"])
    packages = []
    for block in dism_runner.parse_blocks(output):
        name = block.get("PackageName") or block.get("Packagename")
        if not name:
            continue
        packages.append(
            ProvisionedPackage(
                package_name=name,
                display_name=block.get("DisplayName", ""),
                publisher_id=block.get("PublisherId", ""),
            )
        )
    logger.info("Found %d provisioned Appx packages.", len(packages))
    return packages


def load_debloat_patterns(config_path: str | Path) -> list[str]:
    """Load the list of package-name/display-name substrings to remove.

    Kept as external JSON (not hardcoded) so the debloat list can be
    updated without a code change.
    """
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    patterns = data.get("packages", [])
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError(f"{config_path}: 'packages' must be a list of strings")
    return patterns


def select_packages_to_remove(
    available: Iterable[ProvisionedPackage],
    patterns: Iterable[str],
) -> list[ProvisionedPackage]:
    """Match configured patterns against packages actually present.

    Matching is a case-insensitive substring match against both
    PackageName and DisplayName, so a short pattern like "XboxApp" matches
    the full versioned package name without needing an exact string.
    """
    lowered_patterns = [p.lower() for p in patterns]
    selected: list[ProvisionedPackage] = []
    seen: set[str] = set()
    for package in available:
        if package.package_name in seen:
            continue
        haystack = f"{package.package_name} {package.display_name}".lower()
        if any(pattern in haystack for pattern in lowered_patterns):
            selected.append(package)
            seen.add(package.package_name)
    return selected


def remove_provisioned_appx_package(mount_dir: str, package_name: str) -> None:
    logger.info("Removing provisioned package: %s", package_name)
    dism_runner.run(
        ["/Image:" + mount_dir, "/Remove-ProvisionedAppxPackage", f"/PackageName:{package_name}"]
    )
    logger.info("Package removed: %s", package_name)


def remove_packages(
    mount_dir: str,
    packages: list[ProvisionedPackage],
    *,
    progress_callback: ProgressCallback | None = None,
) -> list[str]:
    """Remove each package in `packages`, reporting progress via callback.

    `progress_callback(done, total, package_name)` is invoked after each
    removal so a GUI can drive a progress bar without `core` depending on
    any UI framework. Stops and re-raises on the first failure - a partial
    debloat pass on a still-mounted image is recoverable by the caller,
    e.g. via `wim_manager.mounted_wim`'s discard-on-exception behavior.
    """
    removed: list[str] = []
    total = len(packages)
    for done, package in enumerate(packages, start=1):
        remove_provisioned_appx_package(mount_dir, package.package_name)
        removed.append(package.package_name)
        if progress_callback is not None:
            progress_callback(done, total, package.package_name)
    return removed
