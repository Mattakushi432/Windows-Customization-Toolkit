import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtWidgets import QApplication

from core.registry_tweaks import OfflineHive
from core.unattend_generator import LocalUserAccount, UnattendConfig
from core.wim_manager import MountedImage, OrphanAction
from gui.models import InstallerStep, RegTweak, WizardState
from gui.worker import GuiOrphanResolver, PipelineWorker


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QCoreApplication.instance() or QApplication([])
    yield app


def _sample_state(**overrides: object) -> WizardState:
    defaults = dict(
        iso_path="C:\\iso\\win11.iso",
        work_dir="C:\\build",
        output_iso_path="C:\\out\\custom.iso",
        source_dir="C:\\build\\source",
        selected_index=1,
        selected_appx_patterns=["XboxApp"],
        reg_tweaks=[RegTweak(reg_file_path="C:\\tweaks\\t.reg", hive=OfflineHive.SOFTWARE)],
        installers=[InstallerStep(installer_path="C:\\agents\\agent.msi", silent_args="/quiet")],
        unattend=UnattendConfig(local_user=LocalUserAccount(name="itadmin", password="x")),
        iso_strategy_name="oscdimg",
    )
    defaults.update(overrides)
    return WizardState(**defaults)


def test_gui_orphan_resolver_blocks_worker_thread_until_main_thread_answers() -> None:
    resolver = GuiOrphanResolver()
    answers: list[object] = []

    def on_ask(image):
        answers.append(image)
        resolver.answer(OrphanAction.DISCARD)

    resolver.ask.connect(on_ask)

    call_result: dict[str, object] = {}

    def worker_thread_body():
        image = MountedImage(mount_dir="C:\\mount", image_file="x.wim", image_index="1", status="Ok")
        call_result["action"] = resolver(image)

    t = threading.Thread(target=worker_thread_body)
    t.start()

    # Pump the event loop briefly so the queued 'ask' signal is delivered
    # to the main-thread slot while the worker thread blocks on it.
    deadline = time.time() + 5
    while t.is_alive() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    t.join(timeout=5)

    assert not t.is_alive(), "worker thread never unblocked"
    assert call_result["action"] == OrphanAction.DISCARD
    assert len(answers) == 1


@patch("gui.worker.iso_builder")
@patch("gui.worker.unattend_generator")
@patch("gui.worker.software_injector")
@patch("gui.worker.registry_tweaks")
@patch("gui.worker.appx_cleaner")
@patch("gui.worker.wim_manager")
def test_pipeline_worker_runs_steps_in_order_and_emits_finished_true(
    mock_wim_manager: MagicMock,
    mock_appx: MagicMock,
    mock_reg: MagicMock,
    mock_software: MagicMock,
    mock_unattend: MagicMock,
    mock_iso_builder: MagicMock,
) -> None:
    mock_wim_manager.resolve_orphaned_mounts.return_value = []
    mock_wim_manager.mounted_wim.return_value.__enter__.return_value = "C:\\build\\mount"
    mock_appx.get_provisioned_appx_packages.return_value = []
    mock_appx.select_packages_to_remove.return_value = []
    mock_iso_builder.build_iso.return_value = "C:\\out\\custom.iso"

    state = _sample_state()
    worker = PipelineWorker(state, GuiOrphanResolver())

    finished_calls = []
    worker.finished.connect(lambda success, msg: finished_calls.append((success, msg)))

    worker.run()

    assert finished_calls == [(True, "C:\\out\\custom.iso")]
    mock_wim_manager.require_admin.assert_called_once()
    mock_reg.import_reg_file.assert_called_once_with("C:\\build\\mount", OfflineHive.SOFTWARE, "C:\\tweaks\\t.reg")
    mock_software.stage_silent_install.assert_called_once()
    mock_unattend.write_unattend_xml.assert_called_once()
    mock_iso_builder.verify_iso.assert_called_once_with("C:\\out\\custom.iso")


@patch("gui.worker.wim_manager")
def test_pipeline_worker_emits_finished_false_on_failure(mock_wim_manager: MagicMock) -> None:
    mock_wim_manager.require_admin.side_effect = RuntimeError("not elevated")

    state = _sample_state()
    worker = PipelineWorker(state, GuiOrphanResolver())

    finished_calls = []
    worker.finished.connect(lambda success, msg: finished_calls.append((success, msg)))

    worker.run()

    assert finished_calls == [(False, "not elevated")]


def test_pipeline_worker_raises_when_source_dir_not_extracted() -> None:
    state = _sample_state(source_dir="")
    worker = PipelineWorker(state, GuiOrphanResolver())

    finished_calls = []
    worker.finished.connect(lambda success, msg: finished_calls.append((success, msg)))

    worker.run()

    assert finished_calls[0][0] is False
    assert "source_dir" in finished_calls[0][1]


@patch("gui.worker.iso_builder")
@patch("gui.worker.unattend_generator")
@patch("gui.worker.wim_manager")
def test_pipeline_worker_skips_debloat_when_no_patterns_selected(
    mock_wim_manager: MagicMock, mock_unattend: MagicMock, mock_iso_builder: MagicMock
) -> None:
    mock_wim_manager.resolve_orphaned_mounts.return_value = []
    mock_wim_manager.mounted_wim.return_value.__enter__.return_value = "C:\\build\\mount"
    mock_iso_builder.build_iso.return_value = "C:\\out\\custom.iso"

    state = _sample_state(selected_appx_patterns=[], reg_tweaks=[], installers=[])
    worker = PipelineWorker(state, GuiOrphanResolver())

    with patch("gui.worker.appx_cleaner") as mock_appx:
        worker.run()
        mock_appx.get_provisioned_appx_packages.assert_not_called()
