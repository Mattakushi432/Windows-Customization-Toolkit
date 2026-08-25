import sys
from pathlib import Path

import pytest

from gui.resources import resource_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_resource_path_resolves_relative_to_repo_root_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    path = resource_path("config", "appx_debloat_list.json")

    assert path == REPO_ROOT / "config" / "appx_debloat_list.json"
    assert path.is_file()


def test_resource_path_uses_meipass_when_frozen_onefile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    path = resource_path("config", "appx_debloat_list.json")

    assert path == tmp_path / "config" / "appx_debloat_list.json"


def test_resource_path_falls_back_to_executable_dir_when_frozen_onedir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    fake_exe = tmp_path / "WindowsCustomizationToolkit.exe"
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    path = resource_path("config", "appx_debloat_list.json")

    assert path == tmp_path / "config" / "appx_debloat_list.json"
