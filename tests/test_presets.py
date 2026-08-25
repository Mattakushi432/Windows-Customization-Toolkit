import json
import logging
from pathlib import Path

import pytest

from core.registry_tweaks import OfflineHive
from core.unattend_generator import LocalUserAccount, RegionalSettings, UnattendConfig
from gui import presets
from gui.models import InstallerStep, RegTweak, WizardState


def _sample_state(with_local_user: bool = True) -> WizardState:
    unattend = UnattendConfig(
        regional=RegionalSettings(input_locale="de-DE"),
        product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
    )
    if with_local_user:
        unattend.local_user = LocalUserAccount(name="itadmin", password="TopSecret1!", group="Administrators")

    return WizardState(
        iso_path="C:\\iso\\win11.iso",
        work_dir="C:\\build",
        output_iso_path="C:\\out\\custom.iso",
        selected_index=3,
        selected_appx_patterns=["XboxApp", "BingWeather"],
        reg_tweaks=[RegTweak(reg_file_path="C:\\tweaks\\telemetry.reg", hive=OfflineHive.SOFTWARE)],
        installers=[InstallerStep(installer_path="C:\\agents\\zabbix.msi", silent_args="/quiet", dest_name="zabbix.msi")],
        unattend=unattend,
        iso_strategy_name="oscdimg",
    )


def test_state_to_preset_dict_excludes_password() -> None:
    state = _sample_state()

    preset = presets.state_to_preset_dict(state)

    assert "TopSecret1!" not in json.dumps(preset)
    assert preset["unattend"]["local_user"]["name"] == "itadmin"
    assert "password" not in preset["unattend"]["local_user"]


def test_save_preset_file_never_contains_password(tmp_path: Path) -> None:
    state = _sample_state()
    preset_path = tmp_path / "preset.json"

    presets.save_preset(state, preset_path)

    assert "TopSecret1!" not in preset_path.read_text(encoding="utf-8")


def test_save_preset_warns_when_local_user_present(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    state = _sample_state(with_local_user=True)

    with caplog.at_level(logging.WARNING, logger="wct.gui.presets"):
        presets.save_preset(state, tmp_path / "preset.json")

    assert any("password" in r.message for r in caplog.records)


def test_save_and_load_roundtrip_preserves_non_secret_fields(tmp_path: Path) -> None:
    state = _sample_state()
    preset_path = tmp_path / "preset.json"
    presets.save_preset(state, preset_path)

    loaded = presets.load_preset(preset_path)

    assert loaded.iso_path == state.iso_path
    assert loaded.work_dir == state.work_dir
    assert loaded.output_iso_path == state.output_iso_path
    assert loaded.selected_index == state.selected_index
    assert loaded.selected_appx_patterns == state.selected_appx_patterns
    assert loaded.reg_tweaks == state.reg_tweaks
    assert loaded.installers == state.installers
    assert loaded.iso_strategy_name == state.iso_strategy_name
    assert loaded.unattend.regional.input_locale == "de-DE"
    assert loaded.unattend.product_key == state.unattend.product_key


def test_load_preset_local_user_password_is_blank(tmp_path: Path) -> None:
    state = _sample_state(with_local_user=True)
    preset_path = tmp_path / "preset.json"
    presets.save_preset(state, preset_path)

    loaded = presets.load_preset(preset_path)

    assert loaded.unattend.local_user is not None
    assert loaded.unattend.local_user.name == "itadmin"
    assert loaded.unattend.local_user.password == ""


def test_load_preset_without_local_user_roundtrips_none(tmp_path: Path) -> None:
    state = _sample_state(with_local_user=False)
    preset_path = tmp_path / "preset.json"
    presets.save_preset(state, preset_path)

    loaded = presets.load_preset(preset_path)

    assert loaded.unattend.local_user is None


def test_load_preset_rejects_unsupported_format_version(tmp_path: Path) -> None:
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps({"format_version": 999}), encoding="utf-8")

    with pytest.raises(presets.PresetError, match="format_version"):
        presets.load_preset(preset_path)


def test_load_preset_rejects_malformed_data(tmp_path: Path) -> None:
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps({"format_version": 1, "unattend": {}}), encoding="utf-8")

    with pytest.raises(presets.PresetError, match="Malformed preset"):
        presets.load_preset(preset_path)
