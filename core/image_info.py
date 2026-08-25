"""Parsing of `dism /Get-WimInfo`: list editions/indices in a .wim or .esd
image, so a UI can offer edition selection for multi-index images (Windows
ISOs commonly ship Home/Pro/Education/etc. in one file).

Also provides `.esd` -> `.wim` conversion, since `.esd` uses solid
compression and cannot be mounted/serviced directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import dism_runner


@dataclass(frozen=True)
class WimImageInfo:
    index: int
    name: str
    description: str
    size_bytes: int | None


def _parse_size(raw: str) -> int | None:
    """Parse DISM's 'NNN,NNN,NNN bytes' size format into an int."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else None


def get_wim_info(image_path: str) -> list[WimImageInfo]:
    """List all indices/editions contained in a .wim or .esd file.

    DISM's `/Get-WimInfo` accepts the same `/WimFile:` switch for both
    `.wim` and `.esd` sources.
    """
    output = dism_runner.run(["/Get-WimInfo", f"/WimFile:{image_path}"])
    infos = []
    for block in dism_runner.parse_blocks(output):
        if "Index" not in block:
            continue
        infos.append(
            WimImageInfo(
                index=int(block["Index"]),
                name=block.get("Name", ""),
                description=block.get("Description", ""),
                size_bytes=_parse_size(block["Size"]) if "Size" in block else None,
            )
        )
    return infos


def is_esd(image_path: str) -> bool:
    """True if the given path looks like an .esd (compressed, non-serviceable) image."""
    return image_path.lower().endswith(".esd")


def export_esd_to_wim(
    esd_path: str,
    index: int,
    destination_wim_path: str,
    *,
    compress: str = "max",
    check_integrity: bool = True,
) -> None:
    """Convert one index of an `.esd` into a serviceable `.wim` via `/Export-Image`.

    Required before mounting: `.esd` images cannot be mounted/serviced
    directly.
    """
    args = [
        "/Export-Image",
        f"/SourceImageFile:{esd_path}",
        f"/SourceIndex:{index}",
        f"/DestinationImageFile:{destination_wim_path}",
        f"/Compress:{compress}",
    ]
    if check_integrity:
        args.append("/CheckIntegrity")
    dism_runner.run(args)
