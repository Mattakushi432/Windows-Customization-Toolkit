from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import iso_extractor as ie


def _fake_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_mount_iso_raises_when_file_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ie.mount_iso(tmp_path / "missing.iso")


@patch("core.iso_extractor.subprocess.run")
def test_mount_iso_returns_drive_letter(mock_run: MagicMock, tmp_path: Path) -> None:
    iso_path = tmp_path / "windows.iso"
    iso_path.write_bytes(b"fake iso")
    mock_run.return_value = _fake_proc(returncode=0, stdout="D\n")

    drive_letter = ie.mount_iso(iso_path)

    assert drive_letter == "D"
    args = mock_run.call_args.args[0]
    assert args[0] == "powershell.exe"
    assert "-File" in args
    assert str(iso_path) in args


@patch("core.iso_extractor.subprocess.run")
def test_mount_iso_raises_when_no_drive_letter_reported(mock_run: MagicMock, tmp_path: Path) -> None:
    iso_path = tmp_path / "windows.iso"
    iso_path.write_bytes(b"fake iso")
    mock_run.return_value = _fake_proc(returncode=0, stdout="")

    with pytest.raises(ie.IsoExtractionError, match="did not report a drive letter"):
        ie.mount_iso(iso_path)


@patch("core.iso_extractor.subprocess.run")
def test_mount_iso_raises_on_powershell_failure(mock_run: MagicMock, tmp_path: Path) -> None:
    iso_path = tmp_path / "windows.iso"
    iso_path.write_bytes(b"fake iso")
    mock_run.return_value = _fake_proc(returncode=1, stderr="Mount-DiskImage failed")

    with pytest.raises(ie.IsoExtractionError, match="PowerShell script failed"):
        ie.mount_iso(iso_path)


@patch("core.iso_extractor.subprocess.run")
def test_dismount_iso_invokes_dismount_script(mock_run: MagicMock, tmp_path: Path) -> None:
    iso_path = tmp_path / "windows.iso"
    mock_run.return_value = _fake_proc(returncode=0)

    ie.dismount_iso(iso_path)

    args = mock_run.call_args.args[0]
    assert str(iso_path) in args


@pytest.mark.parametrize("exit_code", [0, 1, 3, 7])
@patch("core.iso_extractor.subprocess.run")
def test_robocopy_mirror_treats_0_to_7_as_success(mock_run: MagicMock, exit_code: int, tmp_path: Path) -> None:
    mock_run.return_value = _fake_proc(returncode=exit_code)

    ie._robocopy_mirror(tmp_path / "src", tmp_path / "dst")  # should not raise


@pytest.mark.parametrize("exit_code", [8, 16])
@patch("core.iso_extractor.subprocess.run")
def test_robocopy_mirror_treats_8_plus_as_failure(mock_run: MagicMock, exit_code: int, tmp_path: Path) -> None:
    mock_run.return_value = _fake_proc(returncode=exit_code, stderr="access denied")

    with pytest.raises(ie.IsoExtractionError, match="robocopy failed"):
        ie._robocopy_mirror(tmp_path / "src", tmp_path / "dst")


@patch("core.iso_extractor.subprocess.run")
def test_extract_iso_mounts_copies_and_dismounts(mock_run: MagicMock, tmp_path: Path) -> None:
    iso_path = tmp_path / "windows.iso"
    iso_path.write_bytes(b"fake iso")
    dest_dir = tmp_path / "extracted"

    mock_run.side_effect = [
        _fake_proc(returncode=0, stdout="E\n"),  # mount
        _fake_proc(returncode=0),  # robocopy
        _fake_proc(returncode=0),  # dismount
    ]

    result = ie.extract_iso(iso_path, dest_dir)

    assert result == dest_dir
    assert mock_run.call_count == 3
    robocopy_args = mock_run.call_args_list[1].args[0]
    assert robocopy_args[0] == "robocopy.exe"
    assert robocopy_args[1] == "E:\\"
    assert robocopy_args[2] == str(dest_dir)


@patch("core.iso_extractor.subprocess.run")
def test_extract_iso_dismounts_even_when_copy_fails(mock_run: MagicMock, tmp_path: Path) -> None:
    iso_path = tmp_path / "windows.iso"
    iso_path.write_bytes(b"fake iso")
    dest_dir = tmp_path / "extracted"

    mock_run.side_effect = [
        _fake_proc(returncode=0, stdout="E\n"),  # mount
        _fake_proc(returncode=8, stderr="copy failed"),  # robocopy fails
        _fake_proc(returncode=0),  # dismount (still happens)
    ]

    with pytest.raises(ie.IsoExtractionError, match="robocopy failed"):
        ie.extract_iso(iso_path, dest_dir)

    assert mock_run.call_count == 3
