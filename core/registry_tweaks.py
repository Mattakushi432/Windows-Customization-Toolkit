"""Offline registry hive customization for a mounted WIM image.

Applies a user-supplied `.reg` file to one of the image's offline registry
hives via `reg load` / `reg import` / `reg unload`.

Scope note (see win-iso-customizer-prompt.md section 8): this only imports
already-authored `.reg` files - there is deliberately no in-app registry
editor here, and no built-in catalog of individual tweaks to keep.

The non-obvious part: `reg load` cannot load a hive onto a key path that
already exists, so an offline hive is always loaded under a *temporary* key
name (e.g. `HKLM\\WCT_OFFLINE_HIVE`), never at its "real" path like
`HKLM\\SOFTWARE`. But a `.reg` file exported from a live system references
its real root - `HKEY_LOCAL_MACHINE\\SOFTWARE\\...` or
`HKEY_CURRENT_USER\\...` - which doesn't exist offline. `import_reg_file()`
rewrites those roots onto the temporary key before importing, and refuses
to import a file whose root doesn't match the hive you asked to target,
since that mismatch would otherwise silently do nothing.
"""

from __future__ import annotations

import enum
import logging
import re
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("wct.registry_tweaks")

_REG_EXE = "reg.exe"
_KEY_LINE_RE = re.compile(r"^\[(-?)([^\]]+)\]\s*$")


class RegistryError(RuntimeError):
    """Raised when a reg.exe load/import/unload/query call fails."""


class OfflineHive(enum.Enum):
    """Offline registry hives reachable inside a mounted WIM."""

    SOFTWARE = "SOFTWARE"
    SYSTEM = "SYSTEM"
    DEFAULT_USER_PROFILE = "DEFAULT_USER_PROFILE"
    """NTUSER.DAT for the Default profile - tweaks here apply to every NEW
    user profile created on the installed system (the standard corporate-
    imaging way to set per-user defaults for all future users)."""
    USERS_DEFAULT = "USERS_DEFAULT"
    """System32\\config\\default - loads as HKEY_USERS\\.DEFAULT on a
    running system; used by the logon screen / System account context
    before any user has logged on."""


_HIVE_RELATIVE_PATH: dict[OfflineHive, str] = {
    OfflineHive.SOFTWARE: r"Windows\System32\config\SOFTWARE",
    OfflineHive.SYSTEM: r"Windows\System32\config\SYSTEM",
    OfflineHive.DEFAULT_USER_PROFILE: r"Users\Default\NTUSER.DAT",
    OfflineHive.USERS_DEFAULT: r"Windows\System32\config\default",
}

# The root prefix a .reg file authored against a *live* system would use
# for each hive - needed to rewrite the file onto the temporary key the
# offline hive gets loaded under.
_SOURCE_ROOT_PREFIX: dict[OfflineHive, str] = {
    OfflineHive.SOFTWARE: "HKEY_LOCAL_MACHINE\\SOFTWARE",
    OfflineHive.SYSTEM: "HKEY_LOCAL_MACHINE\\SYSTEM",
    OfflineHive.DEFAULT_USER_PROFILE: "HKEY_CURRENT_USER",
    OfflineHive.USERS_DEFAULT: "HKEY_USERS\\.DEFAULT",
}


def hive_path(mount_dir: str, hive: OfflineHive) -> Path:
    """Path to `hive`'s file inside the mounted image."""
    return Path(mount_dir) / _HIVE_RELATIVE_PATH[hive]


def _run_reg(args: list[str]) -> str:
    logger.debug("Running: %s", " ".join([_REG_EXE, *args]))
    proc = subprocess.run(
        [_REG_EXE, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RegistryError(f"'reg {' '.join(args)}' failed (exit {proc.returncode}):\n{output}")
    return output


def is_hive_loaded(temp_key_name: str) -> bool:
    """Check whether a key of this name already exists under HKLM.

    Used to detect a hive left mounted by a previous crashed run, so
    `load_hive` can fail with a clear, actionable message instead of a raw
    `reg load` error.
    """
    result = subprocess.run(
        [_REG_EXE, "query", f"HKLM\\{temp_key_name}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def load_hive(mount_dir: str, hive: OfflineHive, temp_key_name: str) -> None:
    """Load an offline hive from the mounted image under `HKLM\\<temp_key_name>`."""
    path = hive_path(mount_dir, hive)
    if not path.exists():
        raise FileNotFoundError(f"Hive file not found: {path}")

    if is_hive_loaded(temp_key_name):
        raise RegistryError(
            f"HKLM\\{temp_key_name} is already loaded - likely left over from a "
            "previous run that didn't unload cleanly. Run "
            f"'reg unload HKLM\\{temp_key_name}' manually before retrying, or "
            "call unload_hive() directly."
        )

    logger.info("Loading %s hive (%s) as HKLM\\%s", hive.value, path, temp_key_name)
    _run_reg(["load", f"HKLM\\{temp_key_name}", str(path)])
    logger.info("Hive loaded.")


def unload_hive(temp_key_name: str, *, retries: int = 5, retry_delay_seconds: float = 1.0) -> None:
    """Unload a previously loaded hive, retrying briefly if it's still in use.

    A hive can transiently fail to unload if something still has it open
    (a lingering `reg import` handle, an AV scan). A short retry loop
    resolves the common case; if it still fails after `retries` attempts,
    this raises rather than silently leaving the hive mounted, so the
    caller finds out now instead of the next run hitting a confusing
    "already loaded" error.
    """
    last_error: RegistryError | None = None
    for attempt in range(1, retries + 1):
        try:
            _run_reg(["unload", f"HKLM\\{temp_key_name}"])
            logger.info("Unloaded HKLM\\%s", temp_key_name)
            return
        except RegistryError as exc:
            last_error = exc
            logger.warning(
                "Unload of HKLM\\%s failed (attempt %d/%d): %s",
                temp_key_name,
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                time.sleep(retry_delay_seconds)

    assert last_error is not None
    raise RegistryError(
        f"Could not unload HKLM\\{temp_key_name} after {retries} attempts. "
        "It is likely still open in another process (Registry Editor, a "
        "lingering reg.exe, an AV scan). Close anything that might be "
        f"holding it open and run 'reg unload HKLM\\{temp_key_name}' manually, "
        "or restart the machine before reusing this mount directory."
    ) from last_error


@contextmanager
def loaded_hive(mount_dir: str, hive: OfflineHive, temp_key_name: str) -> Iterator[str]:
    """Load `hive` for the duration of the `with` block, always unloading after.

    Yields the `HKLM\\<temp_key_name>` path the hive is loaded under, for
    use in further `reg.exe` calls inside the block.
    """
    load_hive(mount_dir, hive, temp_key_name)
    try:
        yield f"HKLM\\{temp_key_name}"
    finally:
        unload_hive(temp_key_name)


def _read_reg_file_text(path: Path) -> str:
    """Read a `.reg` file, detecting its encoding from a BOM.

    `regedit.exe` exports `.reg` files as UTF-16LE with a BOM by default;
    hand-written files are often plain UTF-8. Python's "utf-16" codec
    auto-detects and strips either byte-order BOM.
    """
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8")


def _rewrite_reg_file_root(reg_text: str, hive: OfflineHive, temp_key_name: str) -> str:
    """Rewrite a `.reg` file's key-path roots onto the loaded temp key.

    Raises `RegistryError` if any key-path line doesn't start with the
    root expected for `hive` (see `_SOURCE_ROOT_PREFIX`) - a mismatch here
    almost always means the wrong hive was picked for this file, which
    would otherwise import successfully into the wrong place and silently
    do nothing to the intended target.
    """
    expected_prefix = _SOURCE_ROOT_PREFIX[hive]
    new_prefix = f"HKEY_LOCAL_MACHINE\\{temp_key_name}"

    rewritten_lines: list[str] = []
    found_key_line = False
    for line in reg_text.splitlines():
        match = _KEY_LINE_RE.match(line.strip())
        if match is None:
            rewritten_lines.append(line)
            continue

        delete_marker, key_path = match.groups()
        if not key_path.upper().startswith(expected_prefix.upper()):
            raise RegistryError(
                f"This .reg file targets {key_path!r}, which doesn't start "
                f"with the expected root {expected_prefix!r} for the "
                f"{hive.value} hive. Re-export the .reg file against the "
                "right root, or pick the matching OfflineHive."
            )
        found_key_line = True
        remainder = key_path[len(expected_prefix):]
        rewritten_lines.append(f"[{delete_marker}{new_prefix}{remainder}]")

    if not found_key_line:
        raise RegistryError("This .reg file contains no key-path ([...]) lines to import.")

    return "\n".join(rewritten_lines)


def import_reg_file(
    mount_dir: str,
    hive: OfflineHive,
    reg_file_path: str | Path,
    *,
    temp_key_name: str = "WCT_OFFLINE_HIVE",
) -> None:
    """Apply a user-supplied `.reg` file to the image's offline registry.

    Loads `hive` from the mounted image, rewrites the `.reg` file's root
    paths onto the temporary key it's loaded under, imports it, and always
    unloads the hive afterward - including when the import itself fails -
    so a bad `.reg` file never leaves the hive mounted.
    """
    reg_file_path = Path(reg_file_path)
    if not reg_file_path.is_file():
        raise FileNotFoundError(f".reg file not found: {reg_file_path}")

    rewritten_text = _rewrite_reg_file_root(_read_reg_file_text(reg_file_path), hive, temp_key_name)

    with loaded_hive(mount_dir, hive, temp_key_name):
        temp_reg_path = reg_file_path.with_name(f".wct_rewritten_{reg_file_path.name}")
        temp_reg_path.write_text(rewritten_text, encoding="utf-16", newline="")
        try:
            logger.info("Importing %s into HKLM\\%s", reg_file_path.name, temp_key_name)
            _run_reg(["import", str(temp_reg_path)])
            logger.info("Import succeeded: %s", reg_file_path.name)
        finally:
            temp_reg_path.unlink(missing_ok=True)
