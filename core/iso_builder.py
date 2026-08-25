"""Rebuilding an extracted Windows ISO source tree into a bootable .iso.

Assumes `source_dir` is a *complete* extracted Windows ISO source tree
(`boot\\`, `efi\\`, `sources\\` with the customized `install.wim` already
copied back in, etc.) - not just the WIM. Producing that tree (extracting
the original ISO, swapping in the modified `install.wim`) is a separate
concern handled by the pipeline that calls this module.

Strategy pattern rationale (see win-iso-customizer-prompt.md section 2):
building a bootable hybrid (BIOS + UEFI) ISO needs an external tool, and
there is no single one that's both always available and always the best
choice:

- `OscdimgIsoBuilder` wraps `oscdimg.exe` from the Windows ADK's
  "Deployment Tools" component. This is the tool Microsoft's own Windows
  Setup media is built with, so its output is the most reliably compatible
  choice - but the ADK is a separate ~1-2 GB install most machines won't
  have.
- `XorrisoIsoBuilder` wraps `xorriso` (from libisoburn) in its
  mkisofs-compatible mode, as an open-source fallback for machines without
  the ADK (e.g. a Linux/WSL build agent). NOTE: the project spec mentions
  "wimlib-imagex" as the open-source option - that's inaccurate and worth
  flagging explicitly: wimlib only creates/edits `.wim` files (this
  project already uses DISM for that), it has no ISO/UDF-authoring
  capability at all. xorriso is the real open-source equivalent to
  oscdimg for this job.

`build_iso()` picks whichever strategy is available (preferring oscdimg),
or a caller can pass one explicitly. Both strategies probe for their tool
at call time and raise a clear, actionable `IsoBuildError` - never a raw
`FileNotFoundError` from subprocess - if it's missing.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("wct.iso_builder")

_BIOS_BOOT_SECTOR_RELATIVE = r"boot\etfsboot.com"
_EFI_BOOT_IMAGE_RELATIVE = r"efi\microsoft\boot\efisys.bin"

_MIN_PLAUSIBLE_ISO_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


class IsoBuildError(RuntimeError):
    """Raised when the ISO-building tool is missing, fails, or its output looks wrong."""


def _require_boot_files(source_dir: Path) -> tuple[Path, Path]:
    """Locate the BIOS and UEFI El Torito boot images inside `source_dir`.

    Both come from the original Windows ISO and must already be present in
    the extracted source tree - this module does not create them.
    """
    bios_boot = source_dir / _BIOS_BOOT_SECTOR_RELATIVE
    efi_boot = source_dir / _EFI_BOOT_IMAGE_RELATIVE
    missing = [str(p) for p in (bios_boot, efi_boot) if not p.is_file()]
    if missing:
        raise IsoBuildError(
            "source_dir doesn't look like a complete extracted Windows ISO "
            "source tree - missing: " + ", ".join(missing) + ". iso_builder "
            "expects the full ISO contents (boot/, efi/, sources/ with the "
            "modified install.wim already copied back in), not just the WIM."
        )
    return bios_boot, efi_boot


def _run_subprocess(args: list[str], *, tool_name: str) -> str:
    logger.debug("Running: %s", " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + (proc.stderr or "")
    logger.debug("%s output:\n%s", tool_name, output)
    if proc.returncode != 0:
        raise IsoBuildError(f"{tool_name} failed (exit {proc.returncode}):\n{output}")
    return output


class IsoBuilderStrategy(ABC):
    """Common interface for turning a Windows ISO source tree into a bootable .iso.

    Concrete strategies differ only in which external tool they shell out
    to, so the rest of the pipeline can swap between them (or auto-select
    one) without caring which is actually installed.
    """

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this strategy's required external tool can be found."""

    @abstractmethod
    def unavailable_reason(self) -> str:
        """Human-readable explanation of why `is_available()` is False, and how to fix it."""

    @abstractmethod
    def build(self, source_dir: str | Path, output_iso_path: str | Path, *, volume_label: str) -> Path:
        """Build a bootable hybrid ISO from `source_dir` into `output_iso_path`."""


class OscdimgIsoBuilder(IsoBuilderStrategy):
    """Builds the ISO with `oscdimg.exe` from the Windows ADK Deployment Tools."""

    name = "oscdimg"

    _COMMON_INSTALL_PATHS = [
        r"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Deployment Tools\amd64\Oscdimg\oscdimg.exe",
        r"C:\Program Files\Windows Kits\10\Assessment and Deployment Kit\Deployment Tools\amd64\Oscdimg\oscdimg.exe",
    ]

    def _find_oscdimg(self) -> str | None:
        found = shutil.which("oscdimg.exe") or shutil.which("oscdimg")
        if found:
            return found
        for candidate in self._COMMON_INSTALL_PATHS:
            if Path(candidate).is_file():
                return candidate
        return None

    def is_available(self) -> bool:
        return self._find_oscdimg() is not None

    def unavailable_reason(self) -> str:
        return (
            "oscdimg.exe was not found on PATH or in the default Windows ADK "
            "install location. Install the 'Deployment Tools' component of the "
            "Windows ADK, or use XorrisoIsoBuilder instead."
        )

    def build(self, source_dir: str | Path, output_iso_path: str | Path, *, volume_label: str = "WCT_CUSTOM") -> Path:
        source_dir = Path(source_dir)
        output_iso_path = Path(output_iso_path)
        oscdimg_path = self._find_oscdimg()
        if oscdimg_path is None:
            raise IsoBuildError(self.unavailable_reason())

        bios_boot, efi_boot = _require_boot_files(source_dir)
        output_iso_path.parent.mkdir(parents=True, exist_ok=True)

        args = [
            oscdimg_path,
            "-m",
            "-o",
            "-u2",
            "-udfver102",
            f"-l{volume_label}",
            f"-bootdata:2#p0,e,b{bios_boot}#pEF,e,b{efi_boot}",
            str(source_dir),
            str(output_iso_path),
        ]
        logger.info("Building ISO with oscdimg: %s -> %s", source_dir, output_iso_path)
        _run_subprocess(args, tool_name="oscdimg")
        logger.info("ISO built: %s", output_iso_path)
        return output_iso_path


class XorrisoIsoBuilder(IsoBuilderStrategy):
    """Open-source fallback: builds the ISO with xorriso's mkisofs-compatible mode.

    xorriso isn't bundled with Windows and has no first-party Windows
    installer; getting it usually means installing it inside WSL, via
    MSYS2, or from a community build. Intended mainly for non-ADK build
    environments (e.g. a Linux/WSL CI agent) - prefer `OscdimgIsoBuilder`
    on a normal Windows desktop where the ADK is available.
    """

    name = "xorriso"

    def _find_xorriso(self) -> str | None:
        return shutil.which("xorriso")

    def is_available(self) -> bool:
        return self._find_xorriso() is not None

    def unavailable_reason(self) -> str:
        return (
            "xorriso was not found on PATH. Install it (e.g. in WSL/Debian: "
            "'apt install xorriso', or via MSYS2: 'pacman -S xorriso'), or use "
            "OscdimgIsoBuilder instead if the Windows ADK is installed."
        )

    def build(self, source_dir: str | Path, output_iso_path: str | Path, *, volume_label: str = "WCT_CUSTOM") -> Path:
        source_dir = Path(source_dir)
        output_iso_path = Path(output_iso_path)
        xorriso_path = self._find_xorriso()
        if xorriso_path is None:
            raise IsoBuildError(self.unavailable_reason())

        bios_boot, efi_boot = _require_boot_files(source_dir)
        output_iso_path.parent.mkdir(parents=True, exist_ok=True)

        bios_boot_rel = bios_boot.relative_to(source_dir).as_posix()
        efi_boot_rel = efi_boot.relative_to(source_dir).as_posix()

        args = [
            xorriso_path,
            "-as", "mkisofs",
            "-iso-level", "4",
            "-V", volume_label,
            "-eltorito-boot", bios_boot_rel,
            "-no-emul-boot", "-boot-load-size", "8", "-hide", "boot.catalog",
            "-eltorito-alt-boot",
            "-e", efi_boot_rel,
            "-no-emul-boot",
            "-isohybrid-gpt-basdat",
            "-o", str(output_iso_path),
            str(source_dir),
        ]
        logger.info("Building ISO with xorriso: %s -> %s", source_dir, output_iso_path)
        _run_subprocess(args, tool_name="xorriso")
        logger.info("ISO built: %s", output_iso_path)
        return output_iso_path


def available_strategies() -> list[IsoBuilderStrategy]:
    """List all ISO-builder strategies whose tool is actually installed."""
    return [s for s in (OscdimgIsoBuilder(), XorrisoIsoBuilder()) if s.is_available()]


def pick_default_strategy() -> IsoBuilderStrategy:
    """Pick the best available strategy, preferring oscdimg.

    oscdimg is preferred because it's the tool Windows Setup media is
    officially built with. Raises `IsoBuildError` listing why each
    strategy is unavailable if neither can be used.
    """
    oscdimg = OscdimgIsoBuilder()
    if oscdimg.is_available():
        return oscdimg

    xorriso = XorrisoIsoBuilder()
    if xorriso.is_available():
        return xorriso

    raise IsoBuildError(
        "No ISO-building tool is available.\n"
        f"- oscdimg: {oscdimg.unavailable_reason()}\n"
        f"- xorriso: {xorriso.unavailable_reason()}"
    )


def build_iso(
    source_dir: str | Path,
    output_iso_path: str | Path,
    *,
    volume_label: str = "WCT_CUSTOM",
    strategy: IsoBuilderStrategy | None = None,
) -> Path:
    """Build a bootable hybrid ISO from `source_dir`.

    Uses `strategy` if given, otherwise auto-selects via
    `pick_default_strategy()`.
    """
    strategy = strategy or pick_default_strategy()
    logger.info("Using ISO build strategy: %s", strategy.name)
    return strategy.build(source_dir, output_iso_path, volume_label=volume_label)


@dataclass(frozen=True)
class IsoVerificationResult:
    path: Path
    size_bytes: int
    sha256: str


def _sha256_file(path: Path, *, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_iso(
    iso_path: str | Path,
    *,
    compute_sha256: bool = True,
    min_size_bytes: int = _MIN_PLAUSIBLE_ISO_SIZE_BYTES,
) -> IsoVerificationResult:
    """Sanity-check a built ISO: exists, is a plausible size, and (optionally) its checksum.

    This does not validate ISO/UDF internal structure - that would need a
    dedicated ISO-parsing library, out of MVP scope. It catches the common
    failure mode of a build tool exiting 0 while having written a tiny or
    empty file, and gives a checksum worth recording in a build log or
    comparing against a known-good build.
    """
    iso_path = Path(iso_path)
    if not iso_path.is_file():
        raise IsoBuildError(f"Expected ISO not found: {iso_path}")

    size_bytes = iso_path.stat().st_size
    if size_bytes < min_size_bytes:
        raise IsoBuildError(
            f"{iso_path} is only {size_bytes} bytes - far smaller than a "
            f"plausible Windows ISO (< {min_size_bytes} byte threshold). The "
            "build likely failed silently; check the ISO builder's log output."
        )

    sha256 = _sha256_file(iso_path) if compute_sha256 else ""
    logger.info("Verified ISO %s (%d bytes)%s", iso_path, size_bytes, f", sha256={sha256}" if sha256 else "")
    return IsoVerificationResult(path=iso_path, size_bytes=size_bytes, sha256=sha256)
