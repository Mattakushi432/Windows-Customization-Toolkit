import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import iso_builder as ib


def _make_source_tree(tmp_path: Path) -> Path:
    source_dir = tmp_path / "source"
    (source_dir / "boot").mkdir(parents=True)
    (source_dir / "boot" / "etfsboot.com").write_bytes(b"fake bios boot sector")
    (source_dir / "efi" / "microsoft" / "boot").mkdir(parents=True)
    (source_dir / "efi" / "microsoft" / "boot" / "efisys.bin").write_bytes(b"fake efi boot image")
    return source_dir


def _fake_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_require_boot_files_raises_when_missing(tmp_path: Path) -> None:
    empty_source = tmp_path / "empty"
    empty_source.mkdir()

    with pytest.raises(ib.IsoBuildError, match="doesn't look like"):
        ib._require_boot_files(empty_source)


def test_require_boot_files_returns_paths_when_present(tmp_path: Path) -> None:
    source_dir = _make_source_tree(tmp_path)

    bios_boot, efi_boot = ib._require_boot_files(source_dir)

    assert bios_boot == source_dir / "boot" / "etfsboot.com"
    assert efi_boot == source_dir / "efi" / "microsoft" / "boot" / "efisys.bin"


# -- OscdimgIsoBuilder --------------------------------------------------


@patch("core.iso_builder.shutil.which", return_value=None)
def test_oscdimg_not_available_when_missing(mock_which: MagicMock) -> None:
    builder = ib.OscdimgIsoBuilder()
    with patch.object(ib.OscdimgIsoBuilder, "_COMMON_INSTALL_PATHS", []):
        assert builder.is_available() is False
        assert "Windows ADK" in builder.unavailable_reason()


@patch("core.iso_builder.shutil.which", return_value="C:\\tools\\oscdimg.exe")
def test_oscdimg_available_when_on_path(mock_which: MagicMock) -> None:
    assert ib.OscdimgIsoBuilder().is_available() is True


@patch("core.iso_builder.shutil.which", return_value=None)
def test_oscdimg_build_raises_when_tool_missing(mock_which: MagicMock, tmp_path: Path) -> None:
    source_dir = _make_source_tree(tmp_path)
    with patch.object(ib.OscdimgIsoBuilder, "_COMMON_INSTALL_PATHS", []):
        with pytest.raises(ib.IsoBuildError, match="Windows ADK"):
            ib.OscdimgIsoBuilder().build(source_dir, tmp_path / "out.iso")


@patch("core.iso_builder.subprocess.run")
@patch("core.iso_builder.shutil.which", return_value="C:\\tools\\oscdimg.exe")
def test_oscdimg_build_calls_expected_args(mock_which: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
    source_dir = _make_source_tree(tmp_path)
    output_iso = tmp_path / "out" / "custom.iso"
    mock_run.return_value = _fake_proc(returncode=0)

    result = ib.OscdimgIsoBuilder().build(source_dir, output_iso, volume_label="MYLABEL")

    assert result == output_iso
    assert output_iso.parent.exists()
    args = mock_run.call_args.args[0]
    assert args[0] == "C:\\tools\\oscdimg.exe"
    assert "-lMYLABEL" in args
    assert any(a.startswith("-bootdata:2#p0,e,b") and "efisys.bin" in a for a in args)
    assert args[-2] == str(source_dir)
    assert args[-1] == str(output_iso)


@patch("core.iso_builder.subprocess.run")
@patch("core.iso_builder.shutil.which", return_value="C:\\tools\\oscdimg.exe")
def test_oscdimg_build_raises_on_nonzero_exit(mock_which: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
    source_dir = _make_source_tree(tmp_path)
    mock_run.return_value = _fake_proc(returncode=1, stderr="disk full")

    with pytest.raises(ib.IsoBuildError, match="oscdimg failed"):
        ib.OscdimgIsoBuilder().build(source_dir, tmp_path / "out.iso")


@patch("core.iso_builder.shutil.which", return_value="C:\\tools\\oscdimg.exe")
def test_oscdimg_build_raises_when_source_tree_incomplete(mock_which: MagicMock, tmp_path: Path) -> None:
    incomplete_source = tmp_path / "incomplete"
    incomplete_source.mkdir()

    with pytest.raises(ib.IsoBuildError, match="doesn't look like"):
        ib.OscdimgIsoBuilder().build(incomplete_source, tmp_path / "out.iso")


# -- XorrisoIsoBuilder ----------------------------------------------------


@patch("core.iso_builder.shutil.which", return_value=None)
def test_xorriso_not_available_when_missing(mock_which: MagicMock) -> None:
    builder = ib.XorrisoIsoBuilder()
    assert builder.is_available() is False
    assert "xorriso" in builder.unavailable_reason()


@patch("core.iso_builder.subprocess.run")
@patch("core.iso_builder.shutil.which", return_value="/usr/bin/xorriso")
def test_xorriso_build_calls_expected_args_with_relative_boot_paths(
    mock_which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    source_dir = _make_source_tree(tmp_path)
    output_iso = tmp_path / "out.iso"
    mock_run.return_value = _fake_proc(returncode=0)

    ib.XorrisoIsoBuilder().build(source_dir, output_iso, volume_label="MYLABEL")

    args = mock_run.call_args.args[0]
    assert args[0] == "/usr/bin/xorriso"
    assert "-eltorito-boot" in args
    boot_idx = args.index("-eltorito-boot")
    assert args[boot_idx + 1] == "boot/etfsboot.com"
    e_idx = args.index("-e")
    assert args[e_idx + 1] == "efi/microsoft/boot/efisys.bin"
    assert "-V" in args and "MYLABEL" in args


@patch("core.iso_builder.subprocess.run")
@patch("core.iso_builder.shutil.which", return_value="/usr/bin/xorriso")
def test_xorriso_build_raises_on_nonzero_exit(mock_which: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
    source_dir = _make_source_tree(tmp_path)
    mock_run.return_value = _fake_proc(returncode=1, stderr="bad args")

    with pytest.raises(ib.IsoBuildError, match="xorriso failed"):
        ib.XorrisoIsoBuilder().build(source_dir, tmp_path / "out.iso")


# -- strategy selection ---------------------------------------------------


def test_pick_default_strategy_prefers_oscdimg() -> None:
    with patch.object(ib.OscdimgIsoBuilder, "is_available", return_value=True):
        strategy = ib.pick_default_strategy()
    assert isinstance(strategy, ib.OscdimgIsoBuilder)


def test_pick_default_strategy_falls_back_to_xorriso() -> None:
    with patch.object(ib.OscdimgIsoBuilder, "is_available", return_value=False), patch.object(
        ib.XorrisoIsoBuilder, "is_available", return_value=True
    ):
        strategy = ib.pick_default_strategy()
    assert isinstance(strategy, ib.XorrisoIsoBuilder)


def test_pick_default_strategy_raises_when_neither_available() -> None:
    with patch.object(ib.OscdimgIsoBuilder, "is_available", return_value=False), patch.object(
        ib.XorrisoIsoBuilder, "is_available", return_value=False
    ):
        with pytest.raises(ib.IsoBuildError, match="No ISO-building tool"):
            ib.pick_default_strategy()


def test_available_strategies_filters_to_installed_tools() -> None:
    with patch.object(ib.OscdimgIsoBuilder, "is_available", return_value=True), patch.object(
        ib.XorrisoIsoBuilder, "is_available", return_value=False
    ):
        strategies = ib.available_strategies()
    assert len(strategies) == 1
    assert isinstance(strategies[0], ib.OscdimgIsoBuilder)


def test_build_iso_uses_explicitly_given_strategy(tmp_path: Path) -> None:
    source_dir = _make_source_tree(tmp_path)
    fake_strategy = MagicMock(spec=ib.IsoBuilderStrategy)
    fake_strategy.name = "fake"
    fake_strategy.build.return_value = tmp_path / "out.iso"

    result = ib.build_iso(source_dir, tmp_path / "out.iso", strategy=fake_strategy)

    fake_strategy.build.assert_called_once()
    assert result == tmp_path / "out.iso"


# -- verify_iso -------------------------------------------------------------


def test_verify_iso_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(ib.IsoBuildError, match="not found"):
        ib.verify_iso(tmp_path / "missing.iso")


def test_verify_iso_raises_when_too_small(tmp_path: Path) -> None:
    tiny_iso = tmp_path / "tiny.iso"
    tiny_iso.write_bytes(b"not a real iso")

    with pytest.raises(ib.IsoBuildError, match="far smaller"):
        ib.verify_iso(tiny_iso, min_size_bytes=1024)


def test_verify_iso_returns_size_and_checksum(tmp_path: Path) -> None:
    fake_iso = tmp_path / "fake.iso"
    content = b"x" * 2048
    fake_iso.write_bytes(content)

    result = ib.verify_iso(fake_iso, min_size_bytes=1024)

    assert result.size_bytes == 2048
    assert result.sha256 == hashlib.sha256(content).hexdigest()


def test_verify_iso_can_skip_checksum(tmp_path: Path) -> None:
    fake_iso = tmp_path / "fake.iso"
    fake_iso.write_bytes(b"x" * 2048)

    result = ib.verify_iso(fake_iso, min_size_bytes=1024, compute_sha256=False)

    assert result.sha256 == ""
