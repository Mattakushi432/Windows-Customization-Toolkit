"""Wizard pages implementing the step order from win-iso-customizer-prompt.md
section 4: source+workdir -> edition+debloat -> customization -> build.

No business logic lives here beyond collecting widget values into the
shared `WizardState` and calling `core/` (through `gui.blocking.run_blocking`
for short setup steps, or `gui.worker.PipelineWorker` for the long build
step) - every page's `validatePage()`/`initializePage()` reads or writes
`self.wizard().state`.
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
        self.setTitle("Source ISO and working directory")
        self.setSubTitle(
            "Pick the original Windows ISO, a working directory with enough free "
            "space, and where to save the finished ISO."
        )

        self.iso_edit = QLineEdit()
        self.work_dir_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.space_label = QLabel("")
        self.space_label.setWordWrap(True)

        iso_browse = QPushButton("Browse...")
        iso_browse.clicked.connect(self._browse_iso)
        work_browse = QPushButton("Browse...")
        work_browse.clicked.connect(self._browse_work_dir)
        output_browse = QPushButton("Browse...")
        output_browse.clicked.connect(self._browse_output)

        form = QFormLayout()
        form.addRow("Source ISO:", _row(self.iso_edit, iso_browse))
        form.addRow("Working directory:", _row(self.work_dir_edit, work_browse))
        form.addRow("Output ISO:", _row(self.output_edit, output_browse))

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.space_label)

        for edit in (self.iso_edit, self.work_dir_edit, self.output_edit):
            edit.textChanged.connect(self._on_fields_changed)

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state
        self.iso_edit.setText(state.iso_path)
        self.work_dir_edit.setText(state.work_dir)
        self.output_edit.setText(state.output_iso_path)

    def _browse_iso(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Select Windows ISO", "", "ISO images (*.iso)")
        if path:
            self.iso_edit.setText(path)
            if not self.output_edit.text():
                self.output_edit.setText(str(Path(path).with_name(Path(path).stem + "-custom.iso")))

    def _browse_work_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select working directory")
        if path:
            self.work_dir_edit.setText(path)

    def _browse_output(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save custom ISO as", self.output_edit.text(), "ISO images (*.iso)"
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
                f"<span style='color:#c0392b'>Not enough free space at {work_dir}: "
                f"{free / 1e9:.1f} GB free, need roughly {required / 1e9:.1f} GB "
                "(4x the ISO size - WIM mount/servicing needs headroom).</span>"
            )
        else:
            self.space_label.setText(f"{free / 1e9:.1f} GB free at {work_dir} - OK")

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
        self.setTitle("Select Windows edition")
        self.setSubTitle("The ISO is extracted once here; pick which edition/index to customize.")
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(lambda _row: self.completeChanged.emit())
        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        self._extracted_for_iso: str | None = None

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state
        if self._extracted_for_iso == state.iso_path and state.available_editions:
            self._populate_list(state)
            return

        self.list_widget.clear()
        source_dir = Path(state.work_dir) / "source"
        try:
            run_blocking(self, lambda: iso_extractor.extract_iso(state.iso_path, source_dir), label="Extracting ISO...")
            state.source_dir = str(source_dir)

            install_wim = source_dir / "sources" / "install.wim"
            install_esd = source_dir / "sources" / "install.esd"
            image_path = install_wim if install_wim.exists() else install_esd
            if not image_path.exists():
                raise FileNotFoundError(f"Neither install.wim nor install.esd found under {source_dir / 'sources'}")

            editions = run_blocking(self, lambda: image_info.get_wim_info(str(image_path)), label="Reading edition list...")
        except Exception as exc:
            QMessageBox.critical(self, "Extraction failed", str(exc))
            state.available_editions = []
            return

        state.available_editions = editions
        self._extracted_for_iso = state.iso_path
        self._populate_list(state)

    def _populate_list(self, state: WizardState) -> None:
        self.list_widget.clear()
        for edition in state.available_editions:
            size_text = f"{edition.size_bytes / 1e9:.1f} GB" if edition.size_bytes else "unknown size"
            item = QListWidgetItem(f"Index {edition.index}: {edition.name} - {edition.description} ({size_text})")
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
                    label="Converting install.esd to install.wim...",
                )
            except Exception as exc:
                QMessageBox.critical(self, "Conversion failed", str(exc))
                return False
        return True


# --------------------------------------------------------------------------
# Page 3: debloat (provisioned Appx packages actually present)
# --------------------------------------------------------------------------


class DebloatPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Remove preinstalled apps (debloat)")
        self.setSubTitle("Only packages actually present in the selected edition are listed.")
        self.list_widget = QListWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        self._scanned_for: tuple[str, int | None] | None = None

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
            packages = run_blocking(self, scan, label="Scanning image for installed apps...")
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Could not scan image",
                f"{exc}\n\nYou can continue without selecting any apps to remove; "
                "debloating will simply be skipped.",
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
        self.setTitle("Customization")
        self.setSubTitle("Registry tweaks, silent software installs, and Setup answer-file options.")

        outer = QVBoxLayout(self)
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

    def _build_reg_group(self) -> QGroupBox:
        group = QGroupBox(".reg file tweaks")
        self.reg_list = QListWidget()
        add_btn = QPushButton("Add .reg file...")
        add_btn.clicked.connect(self._add_reg_file)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(lambda: _remove_selected(self.reg_list))
        layout = QVBoxLayout(group)
        layout.addWidget(self.reg_list)
        layout.addWidget(_row(add_btn, remove_btn))
        return group

    def _add_reg_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Select .reg file", "", "Registry files (*.reg)")
        if not path:
            return
        hive_names = [h.value for h in OfflineHive]
        hive_name, ok = QInputDialog.getItem(
            self, "Target hive", f"Which offline hive does {Path(path).name} target?", hive_names, 0, False
        )
        if not ok:
            return
        item = QListWidgetItem(f"[{hive_name}] {path}")
        item.setData(Qt.ItemDataRole.UserRole, RegTweak(reg_file_path=path, hive=OfflineHive(hive_name)))
        self.reg_list.addItem(item)

    def _build_installer_group(self) -> QGroupBox:
        group = QGroupBox("Silent software installs (run once at first boot)")
        self.installer_list = QListWidget()
        add_btn = QPushButton("Add installer...")
        add_btn.clicked.connect(self._add_installer)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(lambda: _remove_selected(self.installer_list))
        layout = QVBoxLayout(group)
        layout.addWidget(self.installer_list)
        layout.addWidget(_row(add_btn, remove_btn))
        return group

    def _add_installer(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Select installer", "", "Installers (*.exe *.msi)")
        if not path:
            return
        silent_args, ok = QInputDialog.getText(
            self,
            "Silent install arguments",
            f"Arguments to run {Path(path).name} silently (e.g. /quiet /norestart):",
        )
        if not ok:
            return
        item = QListWidgetItem(f"{Path(path).name}  {silent_args}")
        item.setData(Qt.ItemDataRole.UserRole, InstallerStep(installer_path=path, silent_args=silent_args))
        self.installer_list.addItem(item)

    def _build_hardware_bypass_group(self) -> QGroupBox:
        group = QGroupBox("Windows 11 setup checks (autounattend.xml)")
        self.bypass_tpm = QCheckBox("Bypass TPM 2.0 check")
        self.bypass_secure_boot = QCheckBox("Bypass Secure Boot check")
        self.bypass_ram = QCheckBox("Bypass RAM check")
        self.bypass_storage = QCheckBox("Bypass storage check")
        self.bypass_cpu = QCheckBox("Bypass CPU check")
        self.bypass_nro = QCheckBox('Bypass "Microsoft account required" (BypassNRO)')
        checkboxes = (
            self.bypass_tpm,
            self.bypass_secure_boot,
            self.bypass_ram,
            self.bypass_storage,
            self.bypass_cpu,
            self.bypass_nro,
        )
        layout = QVBoxLayout(group)
        for cb in checkboxes:
            cb.setChecked(True)
            layout.addWidget(cb)
        return group

    def _build_local_user_group(self) -> QGroupBox:
        self.local_user_group = QGroupBox("Create local account")
        self.local_user_group.setCheckable(True)
        self.local_user_group.setChecked(True)
        self.username_edit = QLineEdit("Admin")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.group_combo = QComboBox()
        self.group_combo.addItems(["Administrators", "Users"])
        self.plaintext_checkbox = QCheckBox("Store password as plaintext in autounattend.xml (not recommended)")
        form = QFormLayout(self.local_user_group)
        form.addRow("Username:", self.username_edit)
        form.addRow("Password:", self.password_edit)
        form.addRow("Group:", self.group_combo)
        form.addRow(self.plaintext_checkbox)
        return self.local_user_group

    def _build_regional_group(self) -> QGroupBox:
        group = QGroupBox("Regional settings")
        self.input_locale_edit = QLineEdit("en-US")
        self.system_locale_edit = QLineEdit("en-US")
        self.ui_language_edit = QLineEdit("en-US")
        self.user_locale_edit = QLineEdit("en-US")
        self.timezone_edit = QLineEdit("UTC")
        self.computer_name_edit = QLineEdit()
        self.product_key_edit = QLineEdit()
        self.product_key_edit.setPlaceholderText("XXXXX-XXXXX-XXXXX-XXXXX-XXXXX (optional)")
        form = QFormLayout(group)
        form.addRow("Input locale:", self.input_locale_edit)
        form.addRow("System locale:", self.system_locale_edit)
        form.addRow("UI language:", self.ui_language_edit)
        form.addRow("User locale:", self.user_locale_edit)
        form.addRow("Time zone:", self.timezone_edit)
        form.addRow("Computer name:", self.computer_name_edit)
        form.addRow("Product key:", self.product_key_edit)
        return group

    def _build_iso_backend_group(self) -> QGroupBox:
        group = QGroupBox("ISO build tool")
        self.iso_backend_combo = QComboBox()
        self.iso_backend_combo.addItems(["Auto-detect", "oscdimg (Windows ADK)", "xorriso"])
        layout = QVBoxLayout(group)
        layout.addWidget(self.iso_backend_combo)
        return group

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
            QMessageBox.critical(self, "Invalid answer-file settings", str(exc))
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
        self.setTitle("Build")
        self.setSubTitle("Review the summary below, then start the build. This can take several minutes.")

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.stage_label = QLabel("Not started")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.start_button = QPushButton("Start build")
        self.start_button.clicked.connect(self._start_build)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)

        layout = QVBoxLayout(self)
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

    def initializePage(self) -> None:
        state: WizardState = self.wizard().state
        self.summary_label.setText(
            f"<b>Source:</b> {state.iso_path}<br>"
            f"<b>Edition index:</b> {state.selected_index}<br>"
            f"<b>Apps to remove:</b> {len(state.selected_appx_patterns)}<br>"
            f"<b>Registry tweaks:</b> {len(state.reg_tweaks)}<br>"
            f"<b>Software installs:</b> {len(state.installers)}<br>"
            f"<b>Output ISO:</b> {state.output_iso_path}"
        )

    def isComplete(self) -> bool:
        return self._succeeded

    def _start_build(self) -> None:
        state: WizardState = self.wizard().state
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
            "Orphaned image mount found",
            "An existing DISM mount was found, likely left over from a previous "
            f"run that didn't finish cleanly:\n\n"
            f"Mount dir: {image.mount_dir}\nImage file: {image.image_file}\nStatus: {image.status}\n\n"
            "Commit it (Yes), discard it (No), or abort the build (Cancel)?",
            buttons,
        )
        action_map = {
            QMessageBox.StandardButton.Yes: OrphanAction.COMMIT,
            QMessageBox.StandardButton.No: OrphanAction.DISCARD,
            QMessageBox.StandardButton.Cancel: OrphanAction.ABORT,
        }
        self._orphan_resolver.answer(action_map.get(choice, OrphanAction.ABORT))

    def _on_finished(self, success: bool, message: str) -> None:
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
            QMessageBox.information(self, "Build complete", f"ISO built successfully:\n{message}")
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            QMessageBox.critical(self, "Build failed", message)
