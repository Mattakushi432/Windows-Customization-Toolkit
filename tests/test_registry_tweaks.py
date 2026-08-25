from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import registry_tweaks as rt
from core.registry_tweaks import OfflineHive, RegistryError


def _fake_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_hive_path_resolves_expected_relative_paths(tmp_path: Path) -> None:
    assert rt.hive_path(str(tmp_path), OfflineHive.SOFTWARE) == tmp_path / "Windows/System32/config/SOFTWARE"
    assert rt.hive_path(str(tmp_path), OfflineHive.SYSTEM) == tmp_path / "Windows/System32/config/SYSTEM"
    assert rt.hive_path(str(tmp_path), OfflineHive.DEFAULT_USER_PROFILE) == tmp_path / "Users/Default/NTUSER.DAT"
    assert rt.hive_path(str(tmp_path), OfflineHive.USERS_DEFAULT) == tmp_path / "Windows/System32/config/default"


def test_load_hive_raises_when_hive_file_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rt.load_hive(str(tmp_path), OfflineHive.SOFTWARE, "WCT_TEST")


@patch("core.registry_tweaks.subprocess.run")
def test_load_hive_raises_clear_error_when_already_loaded(mock_run: MagicMock, tmp_path: Path) -> None:
    hive_file = tmp_path / "Windows" / "System32" / "config" / "SOFTWARE"
    hive_file.parent.mkdir(parents=True)
    hive_file.write_text("fake hive")
    mock_run.return_value = _fake_proc(returncode=0)  # 'reg query' succeeds -> already loaded

    with pytest.raises(RegistryError, match="already loaded"):
        rt.load_hive(str(tmp_path), OfflineHive.SOFTWARE, "WCT_TEST")


@patch("core.registry_tweaks.subprocess.run")
def test_load_hive_calls_reg_load_with_expected_args(mock_run: MagicMock, tmp_path: Path) -> None:
    hive_file = tmp_path / "Windows" / "System32" / "config" / "SOFTWARE"
    hive_file.parent.mkdir(parents=True)
    hive_file.write_text("fake hive")
    mock_run.side_effect = [
        _fake_proc(returncode=1),  # 'reg query' -> not loaded
        _fake_proc(returncode=0),  # 'reg load' -> success
    ]

    rt.load_hive(str(tmp_path), OfflineHive.SOFTWARE, "WCT_TEST")

    load_call_args = mock_run.call_args_list[1].args[0]
    assert load_call_args[:2] == ["reg.exe", "load"]
    assert load_call_args[2] == "HKLM\\WCT_TEST"
    assert load_call_args[3] == str(hive_file)


@patch("core.registry_tweaks.time.sleep")
@patch("core.registry_tweaks.subprocess.run")
def test_unload_hive_retries_then_succeeds(mock_run: MagicMock, mock_sleep: MagicMock) -> None:
    mock_run.side_effect = [
        _fake_proc(returncode=1, stderr="in use"),
        _fake_proc(returncode=1, stderr="in use"),
        _fake_proc(returncode=0),
    ]

    rt.unload_hive("WCT_TEST", retries=5, retry_delay_seconds=0)

    assert mock_run.call_count == 3
    assert mock_sleep.call_count == 2


@patch("core.registry_tweaks.time.sleep")
@patch("core.registry_tweaks.subprocess.run")
def test_unload_hive_raises_after_exhausting_retries(mock_run: MagicMock, mock_sleep: MagicMock) -> None:
    mock_run.return_value = _fake_proc(returncode=1, stderr="in use")

    with pytest.raises(RegistryError, match="Could not unload"):
        rt.unload_hive("WCT_TEST", retries=3, retry_delay_seconds=0)

    assert mock_run.call_count == 3


@patch("core.registry_tweaks.subprocess.run")
def test_loaded_hive_unloads_on_exception(mock_run: MagicMock, tmp_path: Path) -> None:
    hive_file = tmp_path / "Windows" / "System32" / "config" / "SOFTWARE"
    hive_file.parent.mkdir(parents=True)
    hive_file.write_text("fake hive")
    mock_run.side_effect = [
        _fake_proc(returncode=1),  # query -> not loaded
        _fake_proc(returncode=0),  # load
        _fake_proc(returncode=0),  # unload (from finally)
    ]

    with pytest.raises(ValueError):
        with rt.loaded_hive(str(tmp_path), OfflineHive.SOFTWARE, "WCT_TEST"):
            raise ValueError("boom")

    unload_call_args = mock_run.call_args_list[2].args[0]
    assert unload_call_args == ["reg.exe", "unload", "HKLM\\WCT_TEST"]


def test_rewrite_reg_file_root_software() -> None:
    reg_text = (
        "Windows Registry Editor Version 5.00\n\n"
        "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection]\n"
        '"AllowTelemetry"=dword:00000000\n'
    )

    rewritten = rt._rewrite_reg_file_root(reg_text, OfflineHive.SOFTWARE, "WCT_TEST")

    assert "[HKEY_LOCAL_MACHINE\\WCT_TEST\\Policies\\Microsoft\\Windows\\DataCollection]" in rewritten
    assert '"AllowTelemetry"=dword:00000000' in rewritten
    assert "HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies" not in rewritten


def test_rewrite_reg_file_root_current_user_for_default_profile() -> None:
    reg_text = "[HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced]\n"

    rewritten = rt._rewrite_reg_file_root(reg_text, OfflineHive.DEFAULT_USER_PROFILE, "WCT_NTUSER")

    assert "[HKEY_LOCAL_MACHINE\\WCT_NTUSER\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced]" in rewritten


def test_rewrite_reg_file_root_handles_delete_marker() -> None:
    reg_text = "[-HKEY_LOCAL_MACHINE\\SOFTWARE\\SomeVendor\\Bloat]\n"

    rewritten = rt._rewrite_reg_file_root(reg_text, OfflineHive.SOFTWARE, "WCT_TEST")

    assert "[-HKEY_LOCAL_MACHINE\\WCT_TEST\\SomeVendor\\Bloat]" in rewritten


def test_rewrite_reg_file_root_raises_on_mismatched_root() -> None:
    reg_text = "[HKEY_CURRENT_USER\\Software\\Foo]\n"

    with pytest.raises(RegistryError, match="doesn't start with"):
        rt._rewrite_reg_file_root(reg_text, OfflineHive.SOFTWARE, "WCT_TEST")


def test_rewrite_reg_file_root_raises_when_no_key_lines() -> None:
    reg_text = "Windows Registry Editor Version 5.00\n\n; just a comment\n"

    with pytest.raises(RegistryError, match="no key-path"):
        rt._rewrite_reg_file_root(reg_text, OfflineHive.SOFTWARE, "WCT_TEST")


def test_read_reg_file_text_handles_utf16_bom(tmp_path: Path) -> None:
    reg_path = tmp_path / "tweak.reg"
    content = "Windows Registry Editor Version 5.00\r\n\r\n[HKEY_LOCAL_MACHINE\\SOFTWARE\\X]\r\n"
    reg_path.write_bytes(content.encode("utf-16"))

    text = rt._read_reg_file_text(reg_path)

    assert "HKEY_LOCAL_MACHINE\\SOFTWARE\\X" in text


def test_read_reg_file_text_handles_plain_utf8(tmp_path: Path) -> None:
    reg_path = tmp_path / "tweak.reg"
    reg_path.write_text("[HKEY_LOCAL_MACHINE\\SOFTWARE\\X]\n", encoding="utf-8")

    text = rt._read_reg_file_text(reg_path)

    assert "HKEY_LOCAL_MACHINE\\SOFTWARE\\X" in text


@patch("core.registry_tweaks.subprocess.run")
def test_import_reg_file_end_to_end(mock_run: MagicMock, tmp_path: Path) -> None:
    hive_file = tmp_path / "mount" / "Windows" / "System32" / "config" / "SOFTWARE"
    hive_file.parent.mkdir(parents=True)
    hive_file.write_text("fake hive")

    reg_file = tmp_path / "tweak.reg"
    reg_file.write_text(
        "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Test]\n\"Value\"=dword:00000001\n",
        encoding="utf-8",
    )

    mock_run.side_effect = [
        _fake_proc(returncode=1),  # query -> not loaded
        _fake_proc(returncode=0),  # load
        _fake_proc(returncode=0),  # import
        _fake_proc(returncode=0),  # unload
    ]

    rt.import_reg_file(str(tmp_path / "mount"), OfflineHive.SOFTWARE, reg_file, temp_key_name="WCT_TEST")

    import_call_args = mock_run.call_args_list[2].args[0]
    assert import_call_args[:2] == ["reg.exe", "import"]
    temp_reg_path = Path(import_call_args[2])
    assert not temp_reg_path.exists()  # cleaned up after import


@patch("core.registry_tweaks.subprocess.run")
def test_import_reg_file_unloads_even_if_import_fails(mock_run: MagicMock, tmp_path: Path) -> None:
    hive_file = tmp_path / "mount" / "Windows" / "System32" / "config" / "SOFTWARE"
    hive_file.parent.mkdir(parents=True)
    hive_file.write_text("fake hive")

    reg_file = tmp_path / "tweak.reg"
    reg_file.write_text("[HKEY_LOCAL_MACHINE\\SOFTWARE\\Test]\n", encoding="utf-8")

    mock_run.side_effect = [
        _fake_proc(returncode=1),  # query -> not loaded
        _fake_proc(returncode=0),  # load
        _fake_proc(returncode=1, stderr="bad reg file"),  # import fails
        _fake_proc(returncode=0),  # unload (still happens)
    ]

    with pytest.raises(RegistryError, match="import"):
        rt.import_reg_file(str(tmp_path / "mount"), OfflineHive.SOFTWARE, reg_file, temp_key_name="WCT_TEST")

    assert mock_run.call_count == 4
    unload_call_args = mock_run.call_args_list[3].args[0]
    assert unload_call_args == ["reg.exe", "unload", "HKLM\\WCT_TEST"]


def test_import_reg_file_raises_when_file_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rt.import_reg_file(str(tmp_path), OfflineHive.SOFTWARE, tmp_path / "missing.reg")
