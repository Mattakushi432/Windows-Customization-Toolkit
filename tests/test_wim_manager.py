from unittest.mock import MagicMock, patch

import pytest

from core import wim_manager
from core.errors import AdminRequiredError, OrphanResolutionAborted
from core.wim_manager import MountedImage, OrphanAction


@pytest.fixture(autouse=True)
def _admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wim_manager, "is_admin", lambda: True)


def test_require_admin_raises_when_not_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wim_manager, "is_admin", lambda: False)

    with pytest.raises(AdminRequiredError):
        wim_manager.require_admin()


@patch("core.dism_runner.run")
def test_get_mounted_wim_info_parses_mounted_images(mock_run: MagicMock) -> None:
    mock_run.return_value = (
        "Mount Dir : C:\\mount\n"
        "Image File : C:\\images\\install.wim\n"
        "Image Index : 1\n"
        "Status : Ok\n"
    )

    images = wim_manager.get_mounted_wim_info()

    assert images == [
        MountedImage(
            mount_dir="C:\\mount",
            image_file="C:\\images\\install.wim",
            image_index="1",
            status="Ok",
        )
    ]


@patch("core.dism_runner.run")
def test_resolve_orphaned_mounts_calls_resolver_and_unmounts(mock_run: MagicMock) -> None:
    mock_run.side_effect = [
        "Mount Dir : C:\\mount\nImage File : x\nImage Index : 1\nStatus : Ok\n",
        "",
    ]
    resolver_calls: list[MountedImage] = []

    def resolver(image: MountedImage) -> OrphanAction:
        resolver_calls.append(image)
        return OrphanAction.DISCARD

    resolved = wim_manager.resolve_orphaned_mounts(resolver)

    assert len(resolved) == 1
    assert resolver_calls == resolved
    unmount_call_args = mock_run.call_args_list[1].args[0]
    assert unmount_call_args == ["/Unmount-Wim", "/MountDir:C:\\mount", "/Discard"]


@patch("core.dism_runner.run")
def test_resolve_orphaned_mounts_abort_raises_without_unmounting(mock_run: MagicMock) -> None:
    mock_run.return_value = "Mount Dir : C:\\mount\nImage File : x\nImage Index : 1\nStatus : Ok\n"

    with pytest.raises(OrphanResolutionAborted):
        wim_manager.resolve_orphaned_mounts(lambda image: OrphanAction.ABORT)

    assert mock_run.call_count == 1  # only the Get-MountedWimInfo call, no unmount


@patch("core.dism_runner.run")
def test_resolve_orphaned_mounts_returns_empty_when_nothing_mounted(mock_run: MagicMock) -> None:
    mock_run.return_value = ""

    resolved = wim_manager.resolve_orphaned_mounts(lambda image: OrphanAction.COMMIT)

    assert resolved == []


@patch("core.dism_runner.run")
def test_mount_wim_requires_admin(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setattr(wim_manager, "is_admin", lambda: False)

    with pytest.raises(AdminRequiredError):
        wim_manager.mount_wim("x.wim", 1, str(tmp_path) + "\\mount")

    mock_run.assert_not_called()


@patch("core.dism_runner.run")
def test_mount_wim_passes_read_only_flag(mock_run: MagicMock, tmp_path: object) -> None:
    mount_dir = str(tmp_path) + "\\mount"

    wim_manager.mount_wim("x.wim", 1, mount_dir, read_only=True)

    args = mock_run.call_args.args[0]
    assert "/ReadOnly" in args


@patch("core.dism_runner.run")
def test_mounted_wim_commits_on_success(mock_run: MagicMock, tmp_path: object) -> None:
    mount_dir = str(tmp_path) + "\\mount"

    with wim_manager.mounted_wim("x.wim", 1, mount_dir) as md:
        assert md == mount_dir

    calls = [c.args[0] for c in mock_run.call_args_list]
    assert calls[0][0] == "/Mount-Wim"
    assert calls[-1] == ["/Unmount-Wim", f"/MountDir:{mount_dir}", "/Commit"]


@patch("core.dism_runner.run")
def test_mounted_wim_discards_on_exception(mock_run: MagicMock, tmp_path: object) -> None:
    mount_dir = str(tmp_path) + "\\mount"

    with pytest.raises(ValueError):
        with wim_manager.mounted_wim("x.wim", 1, mount_dir):
            raise ValueError("boom")

    calls = [c.args[0] for c in mock_run.call_args_list]
    assert calls[0][0] == "/Mount-Wim"
    assert calls[-1] == ["/Unmount-Wim", f"/MountDir:{mount_dir}", "/Discard"]


@patch("core.dism_runner.run")
def test_mounted_wim_read_only_always_discards_even_on_success(
    mock_run: MagicMock, tmp_path: object
) -> None:
    mount_dir = str(tmp_path) + "\\mount"

    with wim_manager.mounted_wim("x.wim", 1, mount_dir, read_only=True):
        pass

    calls = [c.args[0] for c in mock_run.call_args_list]
    assert calls[-1] == ["/Unmount-Wim", f"/MountDir:{mount_dir}", "/Discard"]
