from pathlib import Path

import pytest

from core import software_injector as si


def test_stage_installer_copies_file_and_returns_installed_path(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()
    installer = tmp_path / "ZabbixAgent2.msi"
    installer.write_bytes(b"fake msi contents")

    installed_path = si.stage_installer(str(mount_dir), installer)

    copied = si.installers_dir(str(mount_dir)) / "ZabbixAgent2.msi"
    assert copied.exists()
    assert copied.read_bytes() == b"fake msi contents"
    assert installed_path == "C:\\Windows\\Setup\\Scripts\\Installers\\ZabbixAgent2.msi"


def test_stage_installer_honors_dest_name(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()
    installer = tmp_path / "orig-name.exe"
    installer.write_bytes(b"x")

    installed_path = si.stage_installer(str(mount_dir), installer, dest_name="renamed.exe")

    assert (si.installers_dir(str(mount_dir)) / "renamed.exe").exists()
    assert installed_path.endswith("renamed.exe")


def test_stage_installer_raises_when_source_missing(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        si.stage_installer(str(mount_dir), tmp_path / "does-not-exist.exe")


def test_add_setup_complete_commands_creates_file_with_header(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()

    si.add_setup_complete_commands(str(mount_dir), ['start /wait "" agent.msi /quiet'])

    content = si.setup_complete_path(str(mount_dir)).read_text(encoding="utf-8", newline="")
    assert content.startswith("@echo off\r\n")
    assert 'start /wait "" agent.msi /quiet' in content
    assert "\r\n\r\n" not in content  # no accidental doubled line endings


def test_add_setup_complete_commands_appends_without_duplicating_header(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()

    si.add_setup_complete_commands(str(mount_dir), ["command-one.exe"])
    si.add_setup_complete_commands(str(mount_dir), ["command-two.exe"])

    content = si.setup_complete_path(str(mount_dir)).read_text(encoding="utf-8", newline="")
    assert content.count("@echo off") == 1
    assert "command-one.exe" in content
    assert "command-two.exe" in content
    assert content.index("command-one.exe") < content.index("command-two.exe")


def test_add_setup_complete_commands_rejects_multiline_entries(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()

    with pytest.raises(ValueError):
        si.add_setup_complete_commands(str(mount_dir), ["line one\nline two"])


def test_add_setup_complete_commands_skips_blank_entries(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()

    si.add_setup_complete_commands(str(mount_dir), ["", "   ", "real-command.exe"])

    content = si.setup_complete_path(str(mount_dir)).read_text(encoding="utf-8", newline="")
    assert "real-command.exe" in content


def test_stage_silent_install_copies_file_and_queues_command(tmp_path: Path) -> None:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()
    installer = tmp_path / "agent.msi"
    installer.write_bytes(b"x")

    si.stage_silent_install(str(mount_dir), installer, "/quiet /norestart")

    assert (si.installers_dir(str(mount_dir)) / "agent.msi").exists()
    content = si.setup_complete_path(str(mount_dir)).read_text(encoding="utf-8", newline="")
    assert "Installers\\agent.msi" in content
    assert "/quiet /norestart" in content


def test_setup_complete_cmd_only_contains_first_boot_commands_not_build_time_actions(tmp_path: Path) -> None:
    """SetupComplete.cmd is the "runs later" half of the split described in
    the module docstring - staging a file must never itself write into it."""
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir()
    installer = tmp_path / "agent.msi"
    installer.write_bytes(b"x")

    si.stage_installer(str(mount_dir), installer)

    assert not si.setup_complete_path(str(mount_dir)).exists()
