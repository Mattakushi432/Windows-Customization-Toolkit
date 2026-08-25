"""Background worker that runs the full image-customization pipeline.

Lives on its own `QThread` (via `moveToThread`, wired up by the caller) so
DISM/reg/ISO operations - which can take minutes - never block the GUI
thread. Talks to `core/` only; no PySide-specific behavior leaks into
`core/`, and no business logic lives here beyond orchestrating the order
`core/` functions get called in.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core import appx_cleaner, iso_builder, registry_tweaks, software_injector, unattend_generator, wim_manager
from gui.models import WizardState

logger = logging.getLogger("wct.gui.worker")


class GuiOrphanResolver(QObject):
    """Cross-thread bridge letting the worker thread ask the GUI thread
    how to resolve an orphaned WIM mount, then blocks until it gets an
    answer.

    `core.wim_manager.resolve_orphaned_mounts()` calls this synchronously
    from the worker thread. Emitting `ask` to a receiver that lives on the
    main thread auto-queues it onto that thread's event loop (Qt's normal
    cross-thread signal behavior); the worker thread then blocks on
    `self._event.wait()` until the main thread's connected slot has shown a
    dialog and called `answer()`. Only the worker thread blocks - the GUI
    thread keeps running its event loop the whole time, so the dialog can
    actually appear and be clicked.

    The pending answer is kept on `self` (a plain instance attribute)
    rather than passed as a mutable argument on the signal itself: Qt's
    queued cross-thread delivery marshals signal arguments for later
    delivery, which does not reliably preserve Python object identity for
    a mutable object like a `list` used as an out-parameter. `self` is the
    same interpreter-level object in both threads without going through
    that marshaling, so writing `self._answer` on the main thread and
    reading it on the worker thread after `event.wait()` returns is safe.
    Only one orphan is ever resolved at a time (`resolve_orphaned_mounts`
    calls this synchronously in a loop), so there's no concurrent-request
    case to worry about.
    """

    ask = Signal(object)  # MountedImage

    def __init__(self) -> None:
        super().__init__()
        self._event = threading.Event()
        self._answer: wim_manager.OrphanAction | None = None

    def __call__(self, image: wim_manager.MountedImage) -> wim_manager.OrphanAction:
        self._event = threading.Event()
        self._answer = None
        self.ask.emit(image)
        self._event.wait()
        return self._answer or wim_manager.OrphanAction.ABORT

    def answer(self, action: wim_manager.OrphanAction) -> None:
        """Call from the main-thread slot connected to `ask`, after the user has chosen."""
        self._answer = action
        self._event.set()


class PipelineWorker(QObject):
    """Runs the mount -> customize -> commit -> build-ISO pipeline for one `WizardState`.

    Expects `state.source_dir` to already point at a fully extracted ISO
    source tree (see `gui.blocking.run_blocking` + `core.iso_extractor`,
    invoked earlier by the wizard's Edition page) - extraction is a
    separate, shorter step with its own progress dialog, not repeated here.
    """

    progress = Signal(int, int, str)  # done, total, message
    stage_changed = Signal(str)
    finished = Signal(bool, str)  # success, output-path-or-error-message

    def __init__(self, state: WizardState, orphan_resolver: GuiOrphanResolver) -> None:
        super().__init__()
        self._state = state
        self._orphan_resolver = orphan_resolver

    def run(self) -> None:
        try:
            output_path = self._run_pipeline()
        except Exception as exc:
            logger.exception("Pipeline failed")
            self.finished.emit(False, str(exc))
        else:
            self.finished.emit(True, str(output_path))

    def _run_pipeline(self) -> Path:
        state = self._state
        if not state.source_dir:
            raise ValueError("state.source_dir is empty - the ISO must be extracted before running the pipeline")
        if state.selected_index is None:
            raise ValueError("No image index selected")

        source_dir = Path(state.source_dir)
        install_wim = source_dir / "sources" / "install.wim"
        mount_dir = Path(state.work_dir) / "mount"

        wim_manager.require_admin()

        self.stage_changed.emit("Checking for orphaned mounts")
        wim_manager.resolve_orphaned_mounts(self._orphan_resolver)

        self.stage_changed.emit(f"Mounting image (index {state.selected_index})")
        with wim_manager.mounted_wim(str(install_wim), state.selected_index, str(mount_dir)):
            self._apply_debloat(mount_dir)
            self._apply_registry_tweaks(mount_dir)
            self._apply_software(mount_dir)

        self.stage_changed.emit("Writing unattend answer file")
        unattend_generator.write_unattend_xml(state.unattend, source_dir / "autounattend.xml")

        self.stage_changed.emit("Building ISO")
        strategy = None
        if state.iso_strategy_name == "oscdimg":
            strategy = iso_builder.OscdimgIsoBuilder()
        elif state.iso_strategy_name == "xorriso":
            strategy = iso_builder.XorrisoIsoBuilder()
        output_path = iso_builder.build_iso(source_dir, state.output_iso_path, strategy=strategy)

        self.stage_changed.emit("Verifying ISO")
        iso_builder.verify_iso(output_path)

        self.stage_changed.emit("Done")
        return output_path

    def _apply_debloat(self, mount_dir: Path) -> None:
        if not self._state.selected_appx_patterns:
            return
        self.stage_changed.emit("Removing provisioned Appx packages")
        available = appx_cleaner.get_provisioned_appx_packages(str(mount_dir))
        selected = appx_cleaner.select_packages_to_remove(available, self._state.selected_appx_patterns)
        appx_cleaner.remove_packages(
            str(mount_dir),
            selected,
            progress_callback=lambda done, total, name: self.progress.emit(done, total, name),
        )

    def _apply_registry_tweaks(self, mount_dir: Path) -> None:
        tweaks = self._state.reg_tweaks
        for i, tweak in enumerate(tweaks, start=1):
            self.stage_changed.emit(f"Applying registry tweak {i}/{len(tweaks)}: {Path(tweak.reg_file_path).name}")
            registry_tweaks.import_reg_file(str(mount_dir), tweak.hive, tweak.reg_file_path)

    def _apply_software(self, mount_dir: Path) -> None:
        installers = self._state.installers
        for i, step in enumerate(installers, start=1):
            self.stage_changed.emit(f"Staging installer {i}/{len(installers)}: {Path(step.installer_path).name}")
            software_injector.stage_silent_install(
                str(mount_dir), step.installer_path, step.silent_args, dest_name=step.dest_name
            )
