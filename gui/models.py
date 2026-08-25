"""Shared data structures passed between wizard pages and the pipeline worker.

Plain dataclasses with no PySide6 dependency, so they're usable (and
testable) independently of a running Qt application.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.appx_cleaner import ProvisionedPackage
from core.image_info import WimImageInfo
from core.registry_tweaks import OfflineHive
from core.unattend_generator import UnattendConfig


@dataclass
class RegTweak:
    reg_file_path: str
    hive: OfflineHive


@dataclass
class InstallerStep:
    installer_path: str
    silent_args: str
    dest_name: str | None = None


@dataclass
class WizardState:
    """Everything collected across wizard pages - the pipeline's full input."""

    iso_path: str = ""
    work_dir: str = ""
    output_iso_path: str = ""

    source_dir: str = ""  # populated once ISO extraction has run
    available_editions: list[WimImageInfo] = field(default_factory=list)
    selected_index: int | None = None

    available_appx: list[ProvisionedPackage] = field(default_factory=list)
    selected_appx_patterns: list[str] = field(default_factory=list)

    reg_tweaks: list[RegTweak] = field(default_factory=list)
    installers: list[InstallerStep] = field(default_factory=list)

    unattend: UnattendConfig = field(default_factory=UnattendConfig)
    iso_strategy_name: str | None = None  # None = auto-pick in core.iso_builder
