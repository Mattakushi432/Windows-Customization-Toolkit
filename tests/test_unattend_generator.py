import base64
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core import unattend_generator as ug


def test_render_default_config_is_well_formed_xml() -> None:
    xml_text = ug.render_unattend_xml(ug.UnattendConfig())

    root = ET.fromstring(xml_text)  # raises ET.ParseError if malformed
    assert root.tag == f"{{{ug._UNATTEND_NS}}}unattend"


def test_render_declares_expected_namespaces() -> None:
    xml_text = ug.render_unattend_xml(ug.UnattendConfig())

    assert 'xmlns="urn:schemas-microsoft-com:unattend"' in xml_text
    assert 'xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"' in xml_text


def test_all_hardware_bypass_commands_present_when_all_enabled() -> None:
    xml_text = ug.render_unattend_xml(ug.UnattendConfig())

    for value_name in [
        "BypassTPMCheck",
        "BypassSecureBootCheck",
        "BypassRAMCheck",
        "BypassStorageCheck",
        "BypassCPUCheck",
        "BypassNRO",
    ]:
        assert value_name in xml_text


def test_hardware_bypass_commands_absent_when_all_disabled() -> None:
    config = ug.UnattendConfig(
        bypass_tpm_check=False,
        bypass_secure_boot_check=False,
        bypass_ram_check=False,
        bypass_storage_check=False,
        bypass_cpu_check=False,
        bypass_nro=False,
    )

    xml_text = ug.render_unattend_xml(config)

    assert "LabConfig" not in xml_text
    assert "RunSynchronous" not in xml_text


def test_bypass_nro_hides_online_account_screens_in_oobe() -> None:
    xml_text = ug.render_unattend_xml(ug.UnattendConfig(bypass_nro=True))

    assert "HideOnlineAccountScreens" in xml_text
    assert "HideWirelessSetupInOOBE" in xml_text


def test_bypass_nro_false_omits_oobe_hide_flags() -> None:
    xml_text = ug.render_unattend_xml(ug.UnattendConfig(bypass_nro=False))

    assert "HideOnlineAccountScreens" not in xml_text


def test_local_user_default_uses_obfuscated_password() -> None:
    config = ug.UnattendConfig(local_user=ug.LocalUserAccount(name="admin", password="Secret123!"))

    xml_text = ug.render_unattend_xml(config)

    assert "Secret123!" not in xml_text
    assert "<PlainText>false</PlainText>" in xml_text
    expected_value = base64.b64encode("Secret123!Password".encode("utf-16-le")).decode("ascii")
    assert expected_value in xml_text


def test_local_user_plaintext_opt_in() -> None:
    config = ug.UnattendConfig(
        local_user=ug.LocalUserAccount(name="admin", password="Secret123!"),
        plaintext_password_in_xml=True,
    )

    xml_text = ug.render_unattend_xml(config)

    assert "<PlainText>true</PlainText>" in xml_text
    assert "Secret123!" in xml_text


def test_local_user_adds_local_account_block() -> None:
    config = ug.UnattendConfig(
        local_user=ug.LocalUserAccount(name="itadmin", password="Secret123!", group="Administrators")
    )

    xml_text = ug.render_unattend_xml(config)

    assert "<Name>itadmin</Name>" in xml_text
    assert "<Group>Administrators</Group>" in xml_text
    assert "SkipUserOOBE>true<" in xml_text


def test_no_local_user_means_skip_user_oobe_false() -> None:
    xml_text = ug.render_unattend_xml(ug.UnattendConfig(local_user=None))

    assert "SkipUserOOBE>false<" in xml_text
    assert "UserAccounts" not in xml_text


def test_missing_password_raises() -> None:
    config = ug.UnattendConfig(local_user=ug.LocalUserAccount(name="admin", password=""))

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_empty_username_raises() -> None:
    config = ug.UnattendConfig(local_user=ug.LocalUserAccount(name="   ", password="x"))

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_username_too_long_raises() -> None:
    config = ug.UnattendConfig(local_user=ug.LocalUserAccount(name="x" * 21, password="secret"))

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_username_invalid_chars_raises() -> None:
    config = ug.UnattendConfig(local_user=ug.LocalUserAccount(name="ad/min", password="secret"))

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_non_standard_group_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    config = ug.UnattendConfig(local_user=ug.LocalUserAccount(name="admin", password="secret", group="PowerUsers"))

    with caplog.at_level(logging.WARNING, logger="wct.unattend_generator"):
        ug.render_unattend_xml(config)  # should not raise

    assert any("PowerUsers" in record.message for record in caplog.records)


def test_bypass_nro_without_local_user_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="wct.unattend_generator"):
        ug.render_unattend_xml(ug.UnattendConfig(bypass_nro=True, local_user=None))

    assert any("local account to fall back to" in record.message for record in caplog.records)


def test_valid_product_key_included() -> None:
    config = ug.UnattendConfig(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")

    xml_text = ug.render_unattend_xml(config)

    assert "<ProductKey>AAAAA-BBBBB-CCCCC-DDDDD-EEEEE</ProductKey>" in xml_text


def test_invalid_product_key_format_raises() -> None:
    config = ug.UnattendConfig(product_key="not-a-real-key")

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_computer_name_too_long_raises() -> None:
    config = ug.UnattendConfig(computer_name="THIS-NAME-IS-WAY-TOO-LONG")

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_computer_name_invalid_chars_raises() -> None:
    config = ug.UnattendConfig(computer_name="bad\\name")

    with pytest.raises(ug.UnattendValidationError):
        ug.render_unattend_xml(config)


def test_valid_computer_name_included() -> None:
    config = ug.UnattendConfig(computer_name="CORP-PC01")

    xml_text = ug.render_unattend_xml(config)

    assert "<ComputerName>CORP-PC01</ComputerName>" in xml_text


def test_regional_settings_appear_in_windows_pe_and_specialize_passes() -> None:
    regional = ug.RegionalSettings(
        input_locale="de-DE",
        system_locale="de-DE",
        ui_language="de-DE",
        user_locale="de-DE",
        timezone="W. Europe Standard Time",
    )
    config = ug.UnattendConfig(regional=regional)

    xml_text = ug.render_unattend_xml(config)

    assert xml_text.count("<InputLocale>de-DE</InputLocale>") == 2
    assert "<TimeZone>W. Europe Standard Time</TimeZone>" in xml_text


def test_write_unattend_xml_creates_file(tmp_path: Path) -> None:
    output_path = tmp_path / "sub" / "autounattend.xml"

    result_path = ug.write_unattend_xml(ug.UnattendConfig(), output_path)

    assert result_path == output_path
    assert output_path.exists()
    ET.fromstring(output_path.read_text(encoding="utf-8"))


def test_write_unattend_xml_warns_on_plaintext_password(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = ug.UnattendConfig(
        local_user=ug.LocalUserAccount(name="admin", password="secret"),
        plaintext_password_in_xml=True,
    )

    with caplog.at_level(logging.WARNING, logger="wct.unattend_generator"):
        ug.write_unattend_xml(config, tmp_path / "autounattend.xml")

    assert any("PLAINTEXT" in record.message for record in caplog.records)


def test_validate_against_xsd_raises_clear_error_without_lxml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "lxml" or name.startswith("lxml."):
            raise ImportError("simulated: lxml not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    xml_path = tmp_path / "autounattend.xml"
    xml_path.write_text("<unattend/>", encoding="utf-8")

    with pytest.raises(ImportError):
        ug.validate_against_xsd(xml_path, tmp_path / "does-not-matter.xsd")
