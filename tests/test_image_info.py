from unittest.mock import MagicMock, patch

from core import image_info


@patch("core.dism_runner.run")
def test_get_wim_info_parses_multiple_indices(mock_run: MagicMock) -> None:
    mock_run.return_value = (
        "Index : 1\n"
        "Name : Windows 11 Pro\n"
        "Description : Windows 11 Pro\n"
        "Size : 20,238,876,672 bytes\n"
        "\n"
        "Index : 2\n"
        "Name : Windows 11 Home\n"
        "Description : Windows 11 Home\n"
        "Size : 19,000,000,000 bytes\n"
    )

    infos = image_info.get_wim_info("install.wim")

    assert len(infos) == 2
    assert infos[0].index == 1
    assert infos[0].name == "Windows 11 Pro"
    assert infos[0].size_bytes == 20238876672
    assert infos[1].index == 2
    assert infos[1].name == "Windows 11 Home"


@patch("core.dism_runner.run")
def test_get_wim_info_handles_missing_size(mock_run: MagicMock) -> None:
    mock_run.return_value = "Index : 1\nName : Windows 11 Pro\nDescription : Windows 11 Pro\n"

    infos = image_info.get_wim_info("install.wim")

    assert infos[0].size_bytes is None


def test_is_esd() -> None:
    assert image_info.is_esd("C:\\images\\install.esd")
    assert image_info.is_esd("C:\\images\\INSTALL.ESD")
    assert not image_info.is_esd("C:\\images\\install.wim")


@patch("core.dism_runner.run")
def test_export_esd_to_wim_builds_expected_args(mock_run: MagicMock) -> None:
    mock_run.return_value = ""

    image_info.export_esd_to_wim("install.esd", 3, "install.wim")

    args = mock_run.call_args.args[0]
    assert "/Export-Image" in args
    assert "/SourceImageFile:install.esd" in args
    assert "/SourceIndex:3" in args
    assert "/DestinationImageFile:install.wim" in args
    assert "/CheckIntegrity" in args


@patch("core.dism_runner.run")
def test_export_esd_to_wim_can_skip_integrity_check(mock_run: MagicMock) -> None:
    mock_run.return_value = ""

    image_info.export_esd_to_wim("install.esd", 1, "install.wim", check_integrity=False)

    args = mock_run.call_args.args[0]
    assert "/CheckIntegrity" not in args
