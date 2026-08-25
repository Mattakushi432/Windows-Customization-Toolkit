"""Wizard pages implementing the step order from win-iso-customizer-prompt.md
section 4: source+workdir -> edition+debloat -> customization -> build.

No business logic lives here beyond collecting widget values into the
shared `WizardState` and calling `core/` (through `gui.blocking.run_blocking`
for short setup steps, or `gui.worker.PipelineWorker` for the long build
step) - every page's `validatePage()`/`initializePage()` reads or writes
`self.wizard().state`.

Static UI chrome (titles, subtitles, button/label/checkbox text) is set in
each page's `retranslate_ui()`, called once from `__init__` and again
whenever `gui.i18n.translator.language_changed` fires, so switching the
interface language updates already-open pages immediately. Text built from
scanned/extracted data (edition list, Appx package list) is produced with
`tr()` inside the `_populate_list`/`initializePage` methods that already
run every time a page is (re-)entered, so it picks up a language change on
the next visit rather than instantly - re-running the underlying DISM scan
just to relabel a list isn't worth it.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from core import appx_cleaner, image_info, iso_extractor, unattend_generator, wim_manager
from core.appx_cleaner import ProvisionedPackage
from core.registry_tweaks import OfflineHive
from core.unattend_generator import LocalUserAccount, RegionalSettings, UnattendConfig
from core.wim_manager import OrphanAction
from gui.blocking import run_blocking
from gui.i18n import LanguageSelector, tr, translator
from gui.logging_bridge import QtLogHandler
from gui.models import InstallerStep, RegTweak, WizardState
from gui.resources import resource_path
from gui.worker import GuiOrphanResolver, PipelineWorker

logger = logging.getLogger("wct.gui.pages")

# WIM mount/servicing needs headroom well beyond the ISO's own size - the
# extracted source tree, the mounted image, and the rebuilt ISO can each
# independently approach the original ISO's size. 4x is a conservative
# rule of thumb, not a precise calculation.
_FREE_SPACE_MULTIPLIER = 4

_DEFAULT_DEBLOAT_CONFIG = resource_path("config", "appx_debloat_list.json")


def _row(*widgets: QWidget) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        layout.addWidget(w)
    return container


def _remove_selected(list_widget: QListWidget) -> None:
    for item in list_widget.selectedItems():
        list_widget.takeItem(list_widget.row(item))


# --------------------------------------------------------------------------
# Page 1: source ISO, working directory, output path
# --------------------------------------------------------------------------


class SourcePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.iso_edit = QLineEdit()
        self.work_dir_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.space_label = QLabel("")
        self.space_label.setWordWrap(True)

        self.iso_browse = QPushButton()
        self.iso_browse.clicked.connect(self._browse_iso)
        self.work_browse = QPushButton()
        self.work_browse.clicked.connect(self._browse_work_dir)
        self.output_browse = QPushButton()
        self.output_browse.clicked.connect(self._browse_output)

        self.form = QFormLayout()
        self.iso_row_label = QLabel()
        self.work_row_label = QLabel()
        self.output_row_label = QLabel()
        self.form.addRow(self.iso_row_label, _row(self.iso_edit, self.iso_browse))
        self.form.addRow(self.work_row_label, _row(self.work_dir_edit, self.work_browse))
        self.form.addRow(self.output_row_label, _row(self.output_edit, self.output_browse))

        layout = QVBoxLayout(self)
        layout.addWidget(LanguageSelector())
        layout.addLayout(self.form)
        layout.addWidget(self.space_label)

        for edit in (self.iso_edit, self.work_dir_edit, self.output_edit):
            edit.textChanged.connect(self._on_fields_changed)

        translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setTitle(tr("Source ISO and working directory"))
        self.setSubTitle(
            tr(
                "Pick the original Windows ISO, a working directory with enough free "
                "space, and where to save the finished ISO."
            )
        )
        self.iso_row_label.setText(tr("Source ISO:"))
        self.work_row_label.setText(tr("Working directory:"))
        self.output_row_label.setText(tr("Output ISO:"))
        for btn in (self.iso_browse, self.work_browse, self.output_browse):
            btn.setText(tr("Browse..."))
        self._update_space_label()

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state
        self.iso_edit.setText(state.iso_path)
        self.work_dir_edit.setText(state.work_dir)
        self.output_edit.setText(state.output_iso_path)

    def _browse_iso(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Select Windows ISO"), "", "ISO images (*.iso)")
        if path:
            self.iso_edit.setText(path)
            if not self.output_edit.text():
                self.output_edit.setText(str(Path(path).with_name(Path(path).stem + "-custom.iso")))

    def _browse_work_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Select working directory"))
        if path:
            self.work_dir_edit.setText(path)

    def _browse_output(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, tr("Save custom ISO as"), self.output_edit.text(), "ISO images (*.iso)"
        )
        if path:
            self.output_edit.setText(path)

    def _on_fields_changed(self) -> None:
        self._update_space_label()
        self.completeChanged.emit()

    def _update_space_label(self) -> None:
        iso_path = Path(self.iso_edit.text())
        work_dir = self.work_dir_edit.text()
        if not iso_path.is_file() or not work_dir:
            self.space_label.setText("")
            return
        try:
            check_dir = work_dir if Path(work_dir).exists() else (Path(work_dir).anchor or "C:\\")
            free = shutil.disk_usage(check_dir).free
            iso_size = iso_path.stat().st_size
        except OSError:
            self.space_label.setText("")
            return
        required = iso_size * _FREE_SPACE_MULTIPLIER
        if free < required:
            self.space_label.setText(
                "<span style='color:#c0392b'>"
                + tr(
                    "Not enough free space at {path}: {free} GB free, need roughly {required} GB "
                    "(4x the ISO size - WIM mount/servicing needs headroom)."
                ).format(path=work_dir, free=f"{free / 1e9:.1f}", required=f"{required / 1e9:.1f}")
                + "</span>"
            )
        else:
            self.space_label.setText(
                tr("{free} GB free at {path} - OK").format(free=f"{free / 1e9:.1f}", path=work_dir)
            )

    def isComplete(self) -> bool:
        iso_path = Path(self.iso_edit.text())
        work_dir = self.work_dir_edit.text()
        output_path = self.output_edit.text()
        if not iso_path.is_file() or not work_dir or not output_path:
            return False
        try:
            Path(work_dir).mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(work_dir).free
        except OSError:
            return False
        return free >= iso_path.stat().st_size * _FREE_SPACE_MULTIPLIER

    def validatePage(self) -> bool:
        state: WizardState = self.wizard().state
        state.iso_path = self.iso_edit.text()
        state.work_dir = self.work_dir_edit.text()
        state.output_iso_path = self.output_edit.text()
        return True


# --------------------------------------------------------------------------
# Page 2: extract ISO, pick edition/index
# --------------------------------------------------------------------------


class EditionPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(lambda _row: self.completeChanged.emit())
        layout = QVBoxLayout(self)
        layout.addWidget(LanguageSelector())
        layout.addWidget(self.list_widget)
        self._extracted_for_iso: str | None = None

        translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setTitle(tr("Select Windows edition"))
        self.setSubTitle(tr("The ISO is extracted once here; pick which edition/index to customize."))
        state: WizardState = self.wizard().state if self.wizard() else None
        if state is not None and state.available_editions:
            self._populate_list(state)

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state
        if self._extracted_for_iso == state.iso_path and state.available_editions:
            self._populate_list(state)
            return

        self.list_widget.clear()
        source_dir = Path(state.work_dir) / "source"
        try:
            run_blocking(
                self, lambda: iso_extractor.extract_iso(state.iso_path, source_dir), label=tr("Extracting ISO...")
            )
            state.source_dir = str(source_dir)

            install_wim = source_dir / "sources" / "install.wim"
            install_esd = source_dir / "sources" / "install.esd"
            image_path = install_wim if install_wim.exists() else install_esd
            if not image_path.exists():
                raise FileNotFoundError(
                    tr("Neither install.wim nor install.esd found under {path}").format(
                        path=source_dir / "sources"
                    )
                )

            editions = run_blocking(
                self, lambda: image_info.get_wim_info(str(image_path)), label=tr("Reading edition list...")
            )
        except Exception as exc:
            QMessageBox.critical(self, tr("Extraction failed"), str(exc))
            state.available_editions = []
            return

        state.available_editions = editions
        self._extracted_for_iso = state.iso_path
        self._populate_list(state)

    def _populate_list(self, state: WizardState) -> None:
        self.list_widget.clear()
        for edition in state.available_editions:
            size_text = f"{edition.size_bytes / 1e9:.1f} GB" if edition.size_bytes else tr("unknown size")
            item = QListWidgetItem(
                tr("Index {index}: {name} - {description} ({size})").format(
                    index=edition.index, name=edition.name, description=edition.description, size=size_text
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, edition.index)
            self.list_widget.addItem(item)

        target_row = 0
        if state.selected_index is not None:
            for row in range(self.list_widget.count()):
                if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == state.selected_index:
                    target_row = row
                    break
        if self.list_widget.count():
            self.list_widget.setCurrentRow(target_row)

    def isComplete(self) -> bool:
        return self.list_widget.currentItem() is not None

    def validatePage(self) -> bool:
        state: WizardState = self.wizard().state
        item = self.list_widget.currentItem()
        if item is None:
            return False
        state.selected_index = item.data(Qt.ItemDataRole.UserRole)

        source_dir = Path(state.source_dir)
        install_wim = source_dir / "sources" / "install.wim"
        install_esd = source_dir / "sources" / "install.esd"
        if not install_wim.exists() and install_esd.exists():
            try:
                run_blocking(
                    self,
                    lambda: image_info.export_esd_to_wim(str(install_esd), state.selected_index, str(install_wim)),
                    label=tr("Converting install.esd to install.wim..."),
                )
            except Exception as exc:
                QMessageBox.critical(self, tr("Conversion failed"), str(exc))
                return False
        return True


# --------------------------------------------------------------------------
# Page 3: debloat (provisioned Appx packages actually present)
# --------------------------------------------------------------------------


class DebloatPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.list_widget = QListWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(LanguageSelector())
        layout.addWidget(self.list_widget)
        self._scanned_for: tuple[str, int | None] | None = None

        translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setTitle(tr("Remove preinstalled apps (debloat)"))
        self.setSubTitle(tr("Only packages actually present in the selected edition are listed."))
        state: WizardState = self.wizard().state if self.wizard() else None
        if state is not None and state.available_appx:
            self._populate_list(state)

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state
        key = (state.source_dir, state.selected_index)
        if self._scanned_for == key and state.available_appx:
            self._populate_list(state)
            return

        source_dir = Path(state.source_dir)
        install_wim = source_dir / "sources" / "install.wim"
        scan_mount_dir = Path(state.work_dir) / "scan_mount"

        def scan() -> list[ProvisionedPackage]:
            wim_manager.require_admin()
            wim_manager.mount_wim(str(install_wim), state.selected_index, str(scan_mount_dir), read_only=True)
            try:
                return appx_cleaner.get_provisioned_appx_packages(str(scan_mount_dir))
            finally:
                wim_manager.unmount_wim(str(scan_mount_dir), commit=False)

        try:
            packages = run_blocking(self, scan, label=tr("Scanning image for installed apps..."))
        except Exception as exc:
            QMessageBox.warning(
                self,
                tr("Could not scan image"),
                tr(
                    "{exc}\n\nYou can continue without selecting any apps to remove; "
                    "debloating will simply be skipped."
                ).format(exc=exc),
            )
            packages = []

        state.available_appx = packages
        self._scanned_for = key
        self._populate_list(state)

    def _populate_list(self, state: WizardState) -> None:
        self.list_widget.clear()
        try:
            default_patterns = appx_cleaner.load_debloat_patterns(_DEFAULT_DEBLOAT_CONFIG)
        except (OSError, ValueError):
            default_patterns = []

        for package in state.available_appx:
            item = QListWidgetItem(package.display_name or package.package_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            haystack = f"{package.package_name} {package.display_name}".lower()
            preselect = package.package_name in state.selected_appx_patterns or (
                not state.selected_appx_patterns and any(p.lower() in haystack for p in default_patterns)
            )
            item.setCheckState(Qt.CheckState.Checked if preselect else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, package.package_name)
            self.list_widget.addItem(item)

    def validatePage(self) -> bool:
        state: WizardState = self.wizard().state
        state.selected_appx_patterns = [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
        ]
        return True


# --------------------------------------------------------------------------
# Page 4: registry tweaks, silent installs, unattend.xml options
# --------------------------------------------------------------------------


class CustomizePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.addWidget(LanguageSelector())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        content_layout = QVBoxLayout(content)

        content_layout.addWidget(self._build_reg_group())
        content_layout.addWidget(self._build_installer_group())
        content_layout.addWidget(self._build_hardware_bypass_group())
        content_layout.addWidget(self._build_local_user_group())
        content_layout.addWidget(self._build_regional_group())
        content_layout.addWidget(self._build_iso_backend_group())
        content_layout.addStretch(1)

        translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setTitle(tr("Customization"))
        self.setSubTitle(tr("Registry tweaks, silent software installs, and Setup answer-file options."))

        self.reg_group.setTitle(tr(".reg file tweaks"))
        self.reg_add_btn.setText(tr("Add .reg file..."))
        self.reg_remove_btn.setText(tr("Remove selected"))

        self.installer_group.setTitle(tr("Silent software installs (run once at first boot)"))
        self.installer_add_btn.setText(tr("Add installer..."))
        self.installer_remove_btn.setText(tr("Remove selected"))

        self.hardware_bypass_group.setTitle(tr("Windows 11 setup checks (autounattend.xml)"))
        self.bypass_tpm.setText(tr("Bypass TPM 2.0 check"))
        self.bypass_secure_boot.setText(tr("Bypass Secure Boot check"))
        self.bypass_ram.setText(tr("Bypass RAM check"))
        self.bypass_storage.setText(tr("Bypass storage check"))
        self.bypass_cpu.setText(tr("Bypass CPU check"))
        self.bypass_nro.setText(tr('Bypass "Microsoft account required" (BypassNRO)'))

        self.local_user_group.setTitle(tr("Create local account"))
        self.username_row_label.setText(tr("Username:"))
        self.password_row_label.setText(tr("Password:"))
        self.group_row_label.setText(tr("Group:"))
        self.plaintext_checkbox.setText(tr("Store password as plaintext in autounattend.xml (not recommended)"))

        self.regional_group.setTitle(tr("Regional settings"))
        self.input_locale_row_label.setText(tr("Input locale:"))
        self.system_locale_row_label.setText(tr("System locale:"))
        self.ui_language_row_label.setText(tr("UI language:"))
        self.user_locale_row_label.setText(tr("User locale:"))
        self.timezone_row_label.setText(tr("Time zone:"))
        self.computer_name_row_label.setText(tr("Computer name:"))
        self.product_key_row_label.setText(tr("Product key:"))
        self.product_key_edit.setPlaceholderText(tr("XXXXX-XXXXX-XXXXX-XXXXX-XXXXX (optional)"))

        self.iso_backend_group.setTitle(tr("ISO build tool"))
        backend_index = self.iso_backend_combo.currentIndex()
        self.iso_backend_combo.blockSignals(True)
        self.iso_backend_combo.clear()
        self.iso_backend_combo.addItems([tr("Auto-detect"), "oscdimg (Windows ADK)", "xorriso"])
        self.iso_backend_combo.setCurrentIndex(max(backend_index, 0))
        self.iso_backend_combo.blockSignals(False)

    def _build_reg_group(self) -> QGroupBox:
        self.reg_group = QGroupBox()
        self.reg_list = QListWidget()
        self.reg_add_btn = QPushButton()
        self.reg_add_btn.clicked.connect(self._add_reg_file)
        self.reg_remove_btn = QPushButton()
        self.reg_remove_btn.clicked.connect(lambda: _remove_selected(self.reg_list))
        layout = QVBoxLayout(self.reg_group)
        layout.addWidget(self.reg_list)
        layout.addWidget(_row(self.reg_add_btn, self.reg_remove_btn))
        return self.reg_group

    def _add_reg_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Select .reg file"), "", "Registry files (*.reg)")
        if not path:
            return
        hive_names = [h.value for h in OfflineHive]
        hive_name, ok = QInputDialog.getItem(
            self,
            tr("Target hive"),
            tr("Which offline hive does {name} target?").format(name=Path(path).name),
            hive_names,
            0,
            False,
        )
        if not ok:
            return
        item = QListWidgetItem(f"[{hive_name}] {path}")
        item.setData(Qt.ItemDataRole.UserRole, RegTweak(reg_file_path=path, hive=OfflineHive(hive_name)))
        self.reg_list.addItem(item)

    def _build_installer_group(self) -> QGroupBox:
        self.installer_group = QGroupBox()
        self.installer_list = QListWidget()
        self.installer_add_btn = QPushButton()
        self.installer_add_btn.clicked.connect(self._add_installer)
        self.installer_remove_btn = QPushButton()
        self.installer_remove_btn.clicked.connect(lambda: _remove_selected(self.installer_list))
        layout = QVBoxLayout(self.installer_group)
        layout.addWidget(self.installer_list)
        layout.addWidget(_row(self.installer_add_btn, self.installer_remove_btn))
        return self.installer_group

    def _add_installer(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Select installer"), "", "Installers (*.exe *.msi)")
        if not path:
            return
        silent_args, ok = QInputDialog.getText(
            self,
            tr("Silent install arguments"),
            tr("Arguments to run {name} silently (e.g. /quiet /norestart):").format(name=Path(path).name),
        )
        if not ok:
            return
        item = QListWidgetItem(f"{Path(path).name}  {silent_args}")
        item.setData(Qt.ItemDataRole.UserRole, InstallerStep(installer_path=path, silent_args=silent_args))
        self.installer_list.addItem(item)

    def _build_hardware_bypass_group(self) -> QGroupBox:
        self.hardware_bypass_group = QGroupBox()
        self.bypass_tpm = QCheckBox()
        self.bypass_secure_boot = QCheckBox()
        self.bypass_ram = QCheckBox()
        self.bypass_storage = QCheckBox()
        self.bypass_cpu = QCheckBox()
        self.bypass_nro = QCheckBox()
        checkboxes = (
            self.bypass_tpm,
            self.bypass_secure_boot,
            self.bypass_ram,
            self.bypass_storage,
            self.bypass_cpu,
            self.bypass_nro,
        )
        layout = QVBoxLayout(self.hardware_bypass_group)
        for cb in checkboxes:
            cb.setChecked(True)
            layout.addWidget(cb)
        return self.hardware_bypass_group

    def _build_local_user_group(self) -> QGroupBox:
        self.local_user_group = QGroupBox()
        self.local_user_group.setCheckable(True)
        self.local_user_group.setChecked(True)
        self.username_edit = QLineEdit("Admin")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.group_combo = QComboBox()
        self.group_combo.addItems(["Administrators", "Users"])
        self.plaintext_checkbox = QCheckBox()
        form = QFormLayout(self.local_user_group)
        self.username_row_label = QLabel()
        self.password_row_label = QLabel()
        self.group_row_label = QLabel()
        form.addRow(self.username_row_label, self.username_edit)
        form.addRow(self.password_row_label, self.password_edit)
        form.addRow(self.group_row_label, self.group_combo)
        form.addRow(self.plaintext_checkbox)
        return self.local_user_group

    def _build_regional_group(self) -> QGroupBox:
        self.regional_group = QGroupBox()
        self.input_locale_edit = QLineEdit("en-US")
        self.system_locale_edit = QLineEdit("en-US")
        self.ui_language_edit = QLineEdit("en-US")
        self.user_locale_edit = QLineEdit("en-US")
        self.timezone_edit = QLineEdit("UTC")
        self.computer_name_edit = QLineEdit()
        self.product_key_edit = QLineEdit()
        form = QFormLayout(self.regional_group)
        self.input_locale_row_label = QLabel()
        self.system_locale_row_label = QLabel()
        self.ui_language_row_label = QLabel()
        self.user_locale_row_label = QLabel()
        self.timezone_row_label = QLabel()
        self.computer_name_row_label = QLabel()
        self.product_key_row_label = QLabel()
        form.addRow(self.input_locale_row_label, self.input_locale_edit)
        form.addRow(self.system_locale_row_label, self.system_locale_edit)
        form.addRow(self.ui_language_row_label, self.ui_language_edit)
        form.addRow(self.user_locale_row_label, self.user_locale_edit)
        form.addRow(self.timezone_row_label, self.timezone_edit)
        form.addRow(self.computer_name_row_label, self.computer_name_edit)
        form.addRow(self.product_key_row_label, self.product_key_edit)
        return self.regional_group

    def _build_iso_backend_group(self) -> QGroupBox:
        self.iso_backend_group = QGroupBox()
        self.iso_backend_combo = QComboBox()
        self.iso_backend_combo.addItems(["Auto-detect", "oscdimg (Windows ADK)", "xorriso"])
        layout = QVBoxLayout(self.iso_backend_group)
        layout.addWidget(self.iso_backend_combo)
        return self.iso_backend_group

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state

        self.reg_list.clear()
        for tweak in state.reg_tweaks:
            item = QListWidgetItem(f"[{tweak.hive.value}] {tweak.reg_file_path}")
            item.setData(Qt.ItemDataRole.UserRole, tweak)
            self.reg_list.addItem(item)

        self.installer_list.clear()
        for step in state.installers:
            item = QListWidgetItem(f"{Path(step.installer_path).name}  {step.silent_args}")
            item.setData(Qt.ItemDataRole.UserRole, step)
            self.installer_list.addItem(item)

        u = state.unattend
        self.bypass_tpm.setChecked(u.bypass_tpm_check)
        self.bypass_secure_boot.setChecked(u.bypass_secure_boot_check)
        self.bypass_ram.setChecked(u.bypass_ram_check)
        self.bypass_storage.setChecked(u.bypass_storage_check)
        self.bypass_cpu.setChecked(u.bypass_cpu_check)
        self.bypass_nro.setChecked(u.bypass_nro)
        self.plaintext_checkbox.setChecked(u.plaintext_password_in_xml)
        # Only force the checkbox ON when state actually has a local user
        # (e.g. a loaded preset) - never force it OFF here, since a fresh
        # WizardState's unattend.local_user is None and this page is
        # re-initialized every time it's shown; forcing it off on every
        # visit would silently discard whatever the user just typed and
        # defeat the "on by default" UX from the constructor.
        if u.local_user is not None:
            self.local_user_group.setChecked(True)
            self.username_edit.setText(u.local_user.name)
            self.password_edit.setText(u.local_user.password)
            idx = self.group_combo.findText(u.local_user.group)
            if idx >= 0:
                self.group_combo.setCurrentIndex(idx)
        self.input_locale_edit.setText(u.regional.input_locale)
        self.system_locale_edit.setText(u.regional.system_locale)
        self.ui_language_edit.setText(u.regional.ui_language)
        self.user_locale_edit.setText(u.regional.user_locale)
        self.timezone_edit.setText(u.regional.timezone)
        self.computer_name_edit.setText(u.computer_name or "")
        self.product_key_edit.setText(u.product_key or "")
        backend_index = {None: 0, "oscdimg": 1, "xorriso": 2}.get(state.iso_strategy_name, 0)
        self.iso_backend_combo.setCurrentIndex(backend_index)

    def validatePage(self) -> bool:
        state: WizardState = self.wizard().state

        state.reg_tweaks = [self.reg_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.reg_list.count())]
        state.installers = [
            self.installer_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.installer_list.count())
        ]

        local_user = None
        if self.local_user_group.isChecked():
            local_user = LocalUserAccount(
                name=self.username_edit.text(),
                password=self.password_edit.text(),
                group=self.group_combo.currentText(),
            )

        unattend = UnattendConfig(
            bypass_tpm_check=self.bypass_tpm.isChecked(),
            bypass_secure_boot_check=self.bypass_secure_boot.isChecked(),
            bypass_ram_check=self.bypass_ram.isChecked(),
            bypass_storage_check=self.bypass_storage.isChecked(),
            bypass_cpu_check=self.bypass_cpu.isChecked(),
            bypass_nro=self.bypass_nro.isChecked(),
            local_user=local_user,
            plaintext_password_in_xml=self.plaintext_checkbox.isChecked(),
            regional=RegionalSettings(
                input_locale=self.input_locale_edit.text(),
                system_locale=self.system_locale_edit.text(),
                ui_language=self.ui_language_edit.text(),
                user_locale=self.user_locale_edit.text(),
                timezone=self.timezone_edit.text(),
            ),
            computer_name=self.computer_name_edit.text() or None,
            product_key=self.product_key_edit.text() or None,
        )

        try:
            unattend_generator.render_unattend_xml(unattend)  # validate eagerly, before Build
        except unattend_generator.UnattendValidationError as exc:
            QMessageBox.critical(self, tr("Invalid answer-file settings"), str(exc))
            return False

        state.unattend = unattend
        state.iso_strategy_name = {0: None, 1: "oscdimg", 2: "xorriso"}[self.iso_backend_combo.currentIndex()]
        return True


# --------------------------------------------------------------------------
# Page 5: build - progress, live log, done
# --------------------------------------------------------------------------


class BuildPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.stage_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.start_button = QPushButton()
        self.start_button.clicked.connect(self._start_build)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)

        layout = QVBoxLayout(self)
        layout.addWidget(LanguageSelector())
        layout.addWidget(self.summary_label)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_view)

        self._succeeded = False
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._log_handler: QtLogHandler | None = None
        self._orphan_resolver: GuiOrphanResolver | None = None
        self._build_running = False

        translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setTitle(tr("Build"))
        self.setSubTitle(tr("Review the summary below, then start the build. This can take several minutes."))
        self.start_button.setText(tr("Start build"))
        if not self._build_running:
            self.stage_label.setText(tr("Not started"))
        if self.wizard() is not None:
            self._update_summary()

    def initializePage(self) -> None:
        self._update_summary()

    def _update_summary(self) -> None:
        state: WizardState = self.wizard().state
        self.summary_label.setText(
            f"<b>{tr('Source:')}</b> {state.iso_path}<br>"
            f"<b>{tr('Edition index:')}</b> {state.selected_index}<br>"
            f"<b>{tr('Apps to remove:')}</b> {len(state.selected_appx_patterns)}<br>"
            f"<b>{tr('Registry tweaks:')}</b> {len(state.reg_tweaks)}<br>"
            f"<b>{tr('Software installs:')}</b> {len(state.installers)}<br>"
            f"<b>{tr('Output ISO:')}</b> {state.output_iso_path}"
        )

    def isComplete(self) -> bool:
        return self._succeeded

    def _start_build(self) -> None:
        state: WizardState = self.wizard().state
        self._build_running = True
        self.start_button.setEnabled(False)
        back_button = self.wizard().button(QWizard.WizardButton.BackButton)
        back_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.log_view.clear()

        self._log_handler = QtLogHandler()
        self._log_handler.log_record.connect(self._append_log)
        logging.getLogger("wct").addHandler(self._log_handler)

        self._orphan_resolver = GuiOrphanResolver()
        self._orphan_resolver.ask.connect(self._on_orphan_ask)

        self._thread = QThread()
        self._worker = PipelineWorker(state, self._orphan_resolver)
        self._worker.moveToThread(self._thread)
        self._worker.stage_changed.connect(self.stage_label.setText)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def _append_log(self, message: str, levelno: int) -> None:
        self.log_view.appendPlainText(message)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
        self.stage_label.setText(f"{message} ({done}/{total})")

    def _on_orphan_ask(self, image) -> None:
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
        choice = QMessageBox.question(
            self,
            tr("Orphaned image mount found"),
            tr(
                "An existing DISM mount was found, likely left over from a previous "
                "run that didn't finish cleanly:\n\n"
                "Mount dir: {mount_dir}\nImage file: {image_file}\nStatus: {status}\n\n"
                "Commit it (Yes), discard it (No), or abort the build (Cancel)?"
            ).format(mount_dir=image.mount_dir, image_file=image.image_file, status=image.status),
            buttons,
        )
        action_map = {
            QMessageBox.StandardButton.Yes: OrphanAction.COMMIT,
            QMessageBox.StandardButton.No: OrphanAction.DISCARD,
            QMessageBox.StandardButton.Cancel: OrphanAction.ABORT,
        }
        self._orphan_resolver.answer(action_map.get(choice, OrphanAction.ABORT))

    def _on_finished(self, success: bool, message: str) -> None:
        self._build_running = False
        if self._log_handler is not None:
            logging.getLogger("wct").removeHandler(self._log_handler)
            self._log_handler = None
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None

        self.wizard().button(QWizard.WizardButton.BackButton).setEnabled(True)
        self.start_button.setEnabled(True)

        if success:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
            self._succeeded = True
            self.completeChanged.emit()
            QMessageBox.information(self, tr("Build complete"), tr("ISO built successfully:\n{path}").format(path=message))
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            QMessageBox.critical(self, tr("Build failed"), message)
