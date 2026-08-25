"""The customization wizard: assembles all pages around one shared WizardState."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWizard

from gui import presets
from gui.i18n import tr, translator
from gui.models import WizardState
from gui.pages import BuildPage, CustomizePage, DebloatPage, EditionPage, SourcePage

logger = logging.getLogger("wct.gui.wizard")


class CustomizerWizard(QWizard):
    def __init__(self) -> None:
        super().__init__()
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.state = WizardState()

        self.addPage(SourcePage())
        self.addPage(EditionPage())
        self.addPage(DebloatPage())
        self.addPage(CustomizePage())
        self.addPage(BuildPage())

        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        self.customButtonClicked.connect(self._on_custom_button)

        translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("Windows ISO Customizer"))
        self.setButtonText(QWizard.WizardButton.CustomButton1, tr("Load preset..."))
        self.setButtonText(QWizard.WizardButton.CustomButton2, tr("Save preset..."))

    def _on_custom_button(self, which: int) -> None:
        if which == int(QWizard.WizardButton.CustomButton1):
            self._load_preset()
        elif which == int(QWizard.WizardButton.CustomButton2):
            self._save_preset()

    def _load_preset(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr("Load preset"), "", "Preset files (*.json)")
        if not path:
            return
        try:
            loaded_state = presets.load_preset(path)
        except (OSError, presets.PresetError) as exc:
            QMessageBox.critical(self, tr("Could not load preset"), str(exc))
            return

        self.state = loaded_state
        if self.state.unattend.local_user is not None:
            QMessageBox.information(
                self,
                tr("Password not restored"),
                tr(
                    "This preset has a local account configured, but its password is "
                    "never saved to disk - re-enter it on the Customization page."
                ),
            )
        self.restart()

    def _save_preset(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, tr("Save preset"), "", "Preset files (*.json)")
        if not path:
            return
        try:
            presets.save_preset(self.state, path)
        except OSError as exc:
            QMessageBox.critical(self, tr("Could not save preset"), str(exc))
