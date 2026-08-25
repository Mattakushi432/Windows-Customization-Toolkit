"""Save/load wizard presets as JSON.

The local user account's PASSWORD is never written to a preset file - it's
the one field this module always strips on save. A preset is reusable,
shareable configuration, not a secret store (see
win-iso-customizer-prompt.md section 3.3: password must not be stored in
plaintext in a saved preset). Re-enter the password each time you load a
preset that has a local user configured; this module deliberately doesn't
implement an encrypted-preset alternative, to keep the guarantee simple
and unconditional: the password is either typed in for this run, or it
isn't in the file at all.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from core.registry_tweaks import OfflineHive
from core.unattend_generator import LocalUserAccount, RegionalSettings, UnattendConfig
from gui.models import InstallerStep, RegTweak, WizardState

logger = logging.getLogger("wct.gui.presets")

_PRESET_FORMAT_VERSION = 1


class PresetError(ValueError):
    """Raised when a preset file is missing fields or has an unsupported format version."""


def state_to_preset_dict(state: WizardState) -> dict:
    """Convert `state` into a JSON-serializable dict, excluding the password."""
    unattend_dict = dataclasses.asdict(state.unattend)
    local_user = unattend_dict.pop("local_user")
    if local_user is not None:
        local_user.pop("password", None)
        unattend_dict["local_user"] = local_user
    else:
        unattend_dict["local_user"] = None

    return {
        "format_version": _PRESET_FORMAT_VERSION,
        "iso_path": state.iso_path,
        "work_dir": state.work_dir,
        "output_iso_path": state.output_iso_path,
        "selected_index": state.selected_index,
        "selected_appx_patterns": state.selected_appx_patterns,
        "reg_tweaks": [{"reg_file_path": t.reg_file_path, "hive": t.hive.value} for t in state.reg_tweaks],
        "installers": [dataclasses.asdict(i) for i in state.installers],
        "unattend": unattend_dict,
        "iso_strategy_name": state.iso_strategy_name,
    }


def save_preset(state: WizardState, path: str | Path) -> None:
    path = Path(path)
    path.write_text(json.dumps(state_to_preset_dict(state), indent=2), encoding="utf-8")
    if state.unattend.local_user is not None:
        logger.warning(
            "Preset saved to %s WITHOUT the local account password (never "
            "persisted) - re-enter it after loading this preset.",
            path,
        )
    else:
        logger.info("Preset saved to %s", path)


def preset_dict_to_state(data: dict) -> WizardState:
    """Reconstruct a `WizardState` from a preset dict (see `state_to_preset_dict`).

    The reconstructed `unattend.local_user.password` is always `""` - the
    caller (a wizard page) must prompt the user to re-enter it before the
    pipeline runs, since `UnattendConfig` validation rejects a blank
    password.
    """
    if data.get("format_version") != _PRESET_FORMAT_VERSION:
        raise PresetError(f"Unsupported preset format_version: {data.get('format_version')!r}")

    try:
        unattend_data = dict(data["unattend"])
        local_user_data = unattend_data.pop("local_user")
        regional_data = unattend_data.pop("regional")

        local_user = None
        if local_user_data is not None:
            local_user = LocalUserAccount(password="", **local_user_data)

        unattend = UnattendConfig(**unattend_data, regional=RegionalSettings(**regional_data), local_user=local_user)

        return WizardState(
            iso_path=data["iso_path"],
            work_dir=data["work_dir"],
            output_iso_path=data["output_iso_path"],
            selected_index=data.get("selected_index"),
            selected_appx_patterns=data.get("selected_appx_patterns", []),
            reg_tweaks=[
                RegTweak(reg_file_path=t["reg_file_path"], hive=OfflineHive(t["hive"]))
                for t in data.get("reg_tweaks", [])
            ],
            installers=[InstallerStep(**i) for i in data.get("installers", [])],
            unattend=unattend,
            iso_strategy_name=data.get("iso_strategy_name"),
        )
    except (KeyError, TypeError) as exc:
        raise PresetError(f"Malformed preset: {exc}") from exc


def load_preset(path: str | Path) -> WizardState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return preset_dict_to_state(data)
