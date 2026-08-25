from unittest.mock import MagicMock, patch

import pytest

from core import dism_runner
from core.errors import DismError


def _fake_completed_process(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


@patch("core.dism_runner.find_dism", return_value="dism.exe")
@patch("core.dism_runner.subprocess.run")
def test_run_returns_combined_output_on_success(mock_run: MagicMock, mock_find: MagicMock) -> None:
    mock_run.return_value = _fake_completed_process(returncode=0, stdout="ok\n")

    output = dism_runner.run(["/Get-WimInfo"])

    assert output == "ok\n"
    called_args = mock_run.call_args.args[0]
    assert called_args[0] == "dism.exe"
    assert "/Get-WimInfo" in called_args


@patch("core.dism_runner.find_dism", return_value="dism.exe")
@patch("core.dism_runner.subprocess.run")
def test_run_never_uses_shell(mock_run: MagicMock, mock_find: MagicMock) -> None:
    mock_run.return_value = _fake_completed_process(returncode=0)

    dism_runner.run(["/Mount-Wim", "/WimFile:C:\\a b\\install.wim"])

    assert mock_run.call_args.kwargs.get("shell", False) is False
    assert isinstance(mock_run.call_args.args[0], list)


@patch("core.dism_runner.find_dism", return_value="dism.exe")
@patch("core.dism_runner.subprocess.run")
def test_run_raises_dism_error_on_nonzero_exit(mock_run: MagicMock, mock_find: MagicMock) -> None:
    mock_run.return_value = _fake_completed_process(returncode=87, stderr="Error: 87\n")

    with pytest.raises(DismError) as excinfo:
        dism_runner.run(["/Bad-Flag"])

    assert excinfo.value.returncode == 87
    assert "87" in str(excinfo.value)


def test_parse_blocks_splits_on_blank_lines() -> None:
    text = "A : 1\nB : 2\n\nA : 3\nB : 4\n"

    blocks = dism_runner.parse_blocks(text)

    assert blocks == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


def test_parse_blocks_ignores_banner_lines_without_colon() -> None:
    text = (
        "Deployment Image Servicing and Management tool\n"
        "Version: 10.0.19041.844\n"
        "\n"
        "A : 1\n"
        "\n"
        "The operation completed successfully.\n"
    )

    blocks = dism_runner.parse_blocks(text)

    assert {"A": "1"} in blocks


def test_find_dism_raises_clear_error_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dism_runner.shutil, "which", lambda name: None)

    with pytest.raises(FileNotFoundError):
        dism_runner.find_dism()
