import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import appx_cleaner
from core.appx_cleaner import ProvisionedPackage

REPO_ROOT = Path(__file__).resolve().parents[1]


@patch("core.dism_runner.run")
def test_get_provisioned_appx_packages_parses_output(mock_run: MagicMock) -> None:
    mock_run.return_value = (
        "PackageName : Microsoft.XboxApp_1.0.0.0_neutral__8wekyb3d8bbwe\n"
        "DisplayName : Microsoft.XboxApp\n"
        "PublisherId : 8wekyb3d8bbwe\n"
        "\n"
        "PackageName : Microsoft.BingWeather_1.0.0.0_neutral__8wekyb3d8bbwe\n"
        "DisplayName : Microsoft.BingWeather\n"
        "PublisherId : 8wekyb3d8bbwe\n"
    )

    packages = appx_cleaner.get_provisioned_appx_packages("C:\\mount")

    assert len(packages) == 2
    assert packages[0].display_name == "Microsoft.XboxApp"
    assert packages[1].package_name == "Microsoft.BingWeather_1.0.0.0_neutral__8wekyb3d8bbwe"


def test_select_packages_to_remove_matches_case_insensitively() -> None:
    available = [
        ProvisionedPackage("Microsoft.XboxApp_1.0_neutral__x", "Microsoft.XboxApp", "x"),
        ProvisionedPackage("Microsoft.BingWeather_1.0_neutral__x", "Microsoft.BingWeather", "x"),
    ]

    selected = appx_cleaner.select_packages_to_remove(available, ["xboxapp"])

    assert len(selected) == 1
    assert selected[0].display_name == "Microsoft.XboxApp"


def test_select_packages_to_remove_ignores_non_matching() -> None:
    available = [ProvisionedPackage("Microsoft.Calculator_x", "Microsoft.Calculator", "x")]

    selected = appx_cleaner.select_packages_to_remove(available, ["XboxApp"])

    assert selected == []


def test_select_packages_to_remove_dedupes() -> None:
    available = [ProvisionedPackage("Pkg", "Pkg", "x")]

    selected = appx_cleaner.select_packages_to_remove(available, ["pkg", "Pkg"])

    assert len(selected) == 1


def test_load_debloat_patterns_reads_config(tmp_path: Path) -> None:
    config_path = tmp_path / "debloat.json"
    config_path.write_text(json.dumps({"packages": ["XboxApp", "BingWeather"]}), encoding="utf-8")

    patterns = appx_cleaner.load_debloat_patterns(config_path)

    assert patterns == ["XboxApp", "BingWeather"]


def test_load_debloat_patterns_rejects_non_list_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps({"packages": "not-a-list"}), encoding="utf-8")

    with pytest.raises(ValueError):
        appx_cleaner.load_debloat_patterns(config_path)


@patch("core.dism_runner.run")
def test_remove_packages_reports_progress_in_order(mock_run: MagicMock) -> None:
    mock_run.return_value = ""
    packages = [
        ProvisionedPackage("Pkg1", "Pkg1", "x"),
        ProvisionedPackage("Pkg2", "Pkg2", "x"),
    ]
    progress_events: list[tuple[int, int, str]] = []

    removed = appx_cleaner.remove_packages(
        "C:\\mount",
        packages,
        progress_callback=lambda done, total, name: progress_events.append((done, total, name)),
    )

    assert removed == ["Pkg1", "Pkg2"]
    assert progress_events == [(1, 2, "Pkg1"), (2, 2, "Pkg2")]


@patch("core.dism_runner.run")
def test_remove_packages_works_without_progress_callback(mock_run: MagicMock) -> None:
    mock_run.return_value = ""

    removed = appx_cleaner.remove_packages("C:\\mount", [ProvisionedPackage("Pkg1", "Pkg1", "x")])

    assert removed == ["Pkg1"]


def test_default_appx_debloat_list_is_valid_and_has_no_duplicates() -> None:
    config_path = REPO_ROOT / "config" / "appx_debloat_list.json"

    patterns = appx_cleaner.load_debloat_patterns(config_path)

    assert len(patterns) > 0
    assert len(patterns) == len(set(p.lower() for p in patterns))
