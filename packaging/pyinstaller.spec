# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the Windows Customization Toolkit GUI.

Build with (from the repo root):
    pyinstaller packaging/pyinstaller.spec --noconfirm --clean

Produces a single-file .exe (see ONEFILE below) that requests
Administrator privileges via packaging/app.manifest -
win-iso-customizer-prompt.md section 2 requires elevation to come from the
.exe's manifest, not a runas trick inside Python.

--onefile vs --onedir: this ships as --onefile (ONEFILE = True) for a
simple "download one .exe, double-click, done" experience for the IT
admins this tool targets - no folder to keep together or extract. The
tradeoffs (slower first launch while it self-extracts to a temp dir under
%TEMP%, and single-exe packers draw more antivirus false-positive scrutiny
than a plain folder of files) are judged acceptable for this MVP. Flip
ONEFILE to False to build --onedir instead if those tradeoffs matter more
in a given deployment.
"""

from pathlib import Path

ONEFILE = True

REPO_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is injected by PyInstaller
ENTRY_SCRIPT = str(REPO_ROOT / "run_gui.py")
MANIFEST_PATH = str(REPO_ROOT / "packaging" / "app.manifest")

a = Analysis(  # noqa: F821 - Analysis/PYZ/EXE/COLLECT are injected by PyInstaller
    [ENTRY_SCRIPT],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    # Bundle the debloat pattern list so it's readable at runtime via
    # gui.resources.resource_path() regardless of --onefile/--onedir - see
    # that module's docstring for why a __file__-relative path can't be
    # used here once frozen.
    datas=[
        (str(REPO_ROOT / "config" / "appx_debloat_list.json"), "config"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe_kwargs = dict(
    name="WindowsCustomizationToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app - no console window. Logs go to file (see core.logging_config).
    manifest=MANIFEST_PATH,
    # PyInstaller does NOT just embed a supplied `manifest` file verbatim:
    # it rewrites <requestedExecutionLevel level="..."> to match uac_admin
    # (default False -> "asInvoker") regardless of what the file says -
    # confirmed by extracting and inspecting a build without this flag,
    # which silently downgraded app.manifest's requireAdministrator to
    # asInvoker. uac_admin=True is what actually makes the final embedded
    # manifest say requireAdministrator; everything else in app.manifest
    # (description, dpiAware, longPathAware, compatibility) is preserved.
    uac_admin=True,
    uac_uiaccess=False,
)

if ONEFILE:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        **exe_kwargs,
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_kwargs,
    )
    COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="WindowsCustomizationToolkit",
    )
