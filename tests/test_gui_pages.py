import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.appx_cleaner import ProvisionedPackage
from core.image_info import WimImageInfo
from gui.wizard import CustomizerWizard


@pytest.fixture(scope="module", autouse=True)
def qapp():
    yield QCoreApplication.instance() or QApplication([])


@pytest.fixture
def wizard() -> CustomizerWizard:
    return CustomizerWizard()


# -- SourcePage -------------------------------------------------------------


def test_source_page_incomplete_until_all_fields_set(wizard: CustomizerWizard, tmp_path: Path) -> None:
    page = wizard.page(0)
    assert page.isComplete() is False

    iso_path = tmp_path / "win.iso"
    iso_path.write_bytes(b"x" * 1024)
    page.iso_edit.setText(str(iso_path))
    assert page.isComplete() is False  # work_dir/output still empty

    page.work_dir_edit.setText(str(tmp_path))
    page.output_edit.setText(str(tmp_path / "out.iso"))
    assert page.isComplete() is True


def test_source_page_blocks_when_insufficient_disk_space(wizard: CustomizerWizard, tmp_path: Path) -> None:
    page = wizard.page(0)
    iso_path = tmp_path / "win.iso"
    iso_path.write_bytes(b"x" * 1024)
    page.iso_edit.setText(str(iso_path))
    page.work_dir_edit.setText(str(tmp_path))
    page.output_edit.setText(str(tmp_path / "out.iso"))

    with patch("gui.pages.shutil.disk_usage") as mock_disk_usage:
        mock_disk_usage.return_value.free = 0
        assert page.isComplete() is False


def test_source_page_validate_writes_to_wizard_state(wizard: CustomizerWizard, tmp_path: Path) -> None:
    page = wizard.page(0)
    iso_path = tmp_path / "win.iso"
    iso_path.write_bytes(b"x")
    page.iso_edit.setText(str(iso_path))
    page.work_dir_edit.setText(str(tmp_path))
    page.output_edit.setText(str(tmp_path / "out.iso"))

    assert page.validatePage() is True
    assert wizard.state.iso_path == str(iso_path)
    assert wizard.state.work_dir == str(tmp_path)


# -- EditionPage --------------------------------------------------------------


def test_edition_page_lists_editions_and_preselects_first(wizard: CustomizerWizard, tmp_path: Path) -> None:
    wizard.state.iso_path = "C:\\fake.iso"
    wizard.state.work_dir = str(tmp_path)
    source_dir = tmp_path / "source"
    (source_dir / "sources").mkdir(parents=True)
    (source_dir / "sources" / "install.wim").write_bytes(b"fake")

    page = wizard.page(1)
    with patch("gui.pages.iso_extractor.extract_iso", return_value=source_dir), patch(
        "gui.pages.image_info.get_wim_info",
        return_value=[
            WimImageInfo(index=1, name="Pro", description="Pro", size_bytes=1000),
            WimImageInfo(index=2, name="Home", description="Home", size_bytes=900),
        ],
    ):
        page.initializePage()

    assert page.list_widget.count() == 2
    assert page.isComplete() is True
    assert page.validatePage() is True
    assert wizard.state.selected_index == 1


def test_edition_page_converts_esd_to_wim_on_validate(wizard: CustomizerWizard, tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    (source_dir / "sources").mkdir(parents=True)
    (source_dir / "sources" / "install.esd").write_bytes(b"fake esd")
    wizard.state.source_dir = str(source_dir)

    page = wizard.page(1)
    item_data_index = 1
    with patch("gui.pages.image_info.export_esd_to_wim") as mock_export:
        page.list_widget.clear()
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt

        item = QListWidgetItem("Index 1: Pro")
        item.setData(Qt.ItemDataRole.UserRole, item_data_index)
        page.list_widget.addItem(item)
        page.list_widget.setCurrentRow(0)

        assert page.validatePage() is True
        mock_export.assert_called_once()


# -- DebloatPage --------------------------------------------------------------


def test_debloat_page_preselects_default_patterns_on_first_scan(wizard: CustomizerWizard, tmp_path: Path) -> None:
    wizard.state.source_dir = str(tmp_path)
    wizard.state.selected_index = 1
    wizard.state.work_dir = str(tmp_path)

    packages = [
        ProvisionedPackage("Microsoft.XboxApp_1.0_x", "Microsoft.XboxApp", "x"),
        ProvisionedPackage("SomeVendor.NotInDefaultList_1.0_x", "SomeVendor.NotInDefaultList", "x"),
    ]
    page = wizard.page(2)
    with patch("gui.pages.run_blocking", return_value=packages):
        page.initializePage()

    from PySide6.QtCore import Qt

    checked_texts = [
        page.list_widget.item(i).text()
        for i in range(page.list_widget.count())
        if page.list_widget.item(i).checkState() == Qt.CheckState.Checked
    ]
    assert "Microsoft.XboxApp" in checked_texts
    assert "SomeVendor.NotInDefaultList" not in checked_texts


def test_debloat_page_validate_collects_checked_package_names(wizard: CustomizerWizard, tmp_path: Path) -> None:
    wizard.state.source_dir = str(tmp_path)
    wizard.state.selected_index = 1
    wizard.state.work_dir = str(tmp_path)
    # A name that doesn't match any default debloat pattern, so it starts
    # unchecked - the test checks it explicitly to simulate a user pick.
    packages = [ProvisionedPackage("Pkg1", "Pkg1", "x")]

    page = wizard.page(2)
    with patch("gui.pages.run_blocking", return_value=packages):
        page.initializePage()

    from PySide6.QtCore import Qt

    assert page.list_widget.item(0).checkState() == Qt.CheckState.Unchecked
    page.list_widget.item(0).setCheckState(Qt.CheckState.Checked)

    assert page.validatePage() is True
    assert wizard.state.selected_appx_patterns == ["Pkg1"]


def test_debloat_page_continues_gracefully_when_scan_fails(wizard: CustomizerWizard, tmp_path: Path) -> None:
    wizard.state.source_dir = str(tmp_path)
    wizard.state.selected_index = 1
    wizard.state.work_dir = str(tmp_path)

    page = wizard.page(2)
    with patch("gui.pages.run_blocking", side_effect=RuntimeError("not admin")), patch(
        "gui.pages.QMessageBox.warning"
    ) as mock_warning:
        page.initializePage()  # should not raise

    mock_warning.assert_called_once()
    assert page.list_widget.count() == 0


# -- CustomizePage ------------------------------------------------------------


def test_customize_page_local_user_checkbox_defaults_on_for_fresh_wizard(wizard: CustomizerWizard) -> None:
    """Regression test: initializePage() must not force the "create local
    account" checkbox off just because a fresh WizardState.unattend.local_user
    is None - that used to silently discard the default-on UX (and any values
    the user had already typed) every time the page was shown."""
    page = wizard.page(3)
    page.initializePage()

    assert page.local_user_group.isChecked() is True


def test_customize_page_validate_builds_unattend_config(wizard: CustomizerWizard) -> None:
    page = wizard.page(3)
    page.initializePage()
    page.username_edit.setText("itadmin")
    page.password_edit.setText("Secret123!")

    assert page.validatePage() is True
    assert wizard.state.unattend.local_user is not None
    assert wizard.state.unattend.local_user.name == "itadmin"
    assert wizard.state.unattend.local_user.password == "Secret123!"


def test_customize_page_rejects_invalid_product_key(wizard: CustomizerWizard) -> None:
    page = wizard.page(3)
    page.initializePage()
    page.product_key_edit.setText("not-a-real-key")

    with patch("gui.pages.QMessageBox.critical") as mock_critical:
        assert page.validatePage() is False
        mock_critical.assert_called_once()


def test_customize_page_loaded_preset_forces_checkbox_on(wizard: CustomizerWizard) -> None:
    from core.unattend_generator import LocalUserAccount

    wizard.state.unattend.local_user = LocalUserAccount(name="preset-user", password="")
    page = wizard.page(3)

    page.initializePage()

    assert page.local_user_group.isChecked() is True
    assert page.username_edit.text() == "preset-user"


# -- BuildPage ------------------------------------------------------------


def test_build_page_summary_reflects_state(wizard: CustomizerWizard) -> None:
    wizard.state.iso_path = "C:\\win.iso"
    wizard.state.selected_index = 3
    wizard.state.selected_appx_patterns = ["A", "B"]
    page = wizard.page(4)

    page.initializePage()

    assert "win.iso" in page.summary_label.text()
    assert "3" in page.summary_label.text()


def test_build_page_not_complete_until_pipeline_succeeds(wizard: CustomizerWizard) -> None:
    page = wizard.page(4)
    assert page.isComplete() is False


def test_build_page_runs_pipeline_end_to_end_via_real_qthread(wizard: CustomizerWizard, tmp_path: Path) -> None:
    wizard.state.iso_path = "C:\\win.iso"
    wizard.state.work_dir = str(tmp_path)
    wizard.state.source_dir = str(tmp_path / "source")
    wizard.state.output_iso_path = str(tmp_path / "out.iso")
    wizard.state.selected_index = 1

    page = wizard.page(4)
    page.initializePage()

    with patch("gui.worker.wim_manager") as mock_wim_manager, patch(
        "gui.worker.iso_builder"
    ) as mock_iso_builder, patch("gui.worker.unattend_generator"), patch(
        "gui.pages.QMessageBox.information"
    ) as mock_info:
        mock_wim_manager.resolve_orphaned_mounts.return_value = []
        mock_wim_manager.mounted_wim.return_value.__enter__.return_value = str(tmp_path / "mount")
        mock_iso_builder.build_iso.return_value = str(tmp_path / "out.iso")

        page._start_build()

        deadline = time.time() + 10
        while not page.isComplete() and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)

    assert page.isComplete() is True
    mock_info.assert_called_once()
    assert page.start_button.isEnabled() is True
