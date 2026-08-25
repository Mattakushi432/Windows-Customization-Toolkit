# Stage 0 PoC — mount, debloat one package, commit, unmount

`mount_and_debloat_poc.py` is a throwaway proof of concept: it must run
reliably against a real `install.wim` before the project moves on to
Stage 1 (generalizing this into `core/wim_manager.py`, `core/appx_cleaner.py`,
`core/image_info.py`).

## Requirements

- Windows 10/11 host (DISM is a native Windows tool; this will not run on
  macOS/Linux).
- An **elevated** shell (Run as Administrator) — `dism /Mount-Wim` and
  `/Unmount-Wim` fail without Administrator rights. The script checks for
  this up front and exits with a clear error instead of letting DISM fail.
- Python 3.10+ (stdlib only — no extra packages needed for the PoC).
- A real `install.wim` from a **licensed, officially obtained** Windows
  ISO. If your ISO ships `install.esd` instead, convert it first:
  ```
  dism /Export-Image /SourceImageFile:install.esd /SourceIndex:<N> ^
      /DestinationImageFile:install.wim /Compress:max /CheckIntegrity
  ```
- Enough free disk space for the mount — WIM mount/servicing needs several
  times the size of the image itself.

## How to run it

From an **elevated** PowerShell or cmd prompt:

```
python mount_and_debloat_poc.py --wim C:\images\install.wim --index 1 --mount-dir C:\mount --package-match MicrosoftEdge
```

This will:
1. Check you're running as Administrator.
2. Check for and let you resolve any orphaned mount point left by a
   previous crashed run (`dism /Get-MountedWimInfo`).
3. `dism /Mount-Wim` the image.
4. `dism /Get-ProvisionedAppxPackages` and remove any package whose name
   contains `MicrosoftEdge` (case-insensitive).
5. `dism /Unmount-Wim /Commit` to save the change back into the `.wim`.

To test the recovery path without changing the image, add `--discard`:
the mount is unmounted with `/Discard` instead of `/Commit`.

To only check for / resolve an orphaned mount (e.g. after a crash), run:

```
python mount_and_debloat_poc.py --check-orphans-only
```

## What "success" looks like

- Exit code `0`.
- `poc.log` (written next to the script) shows, in order: admin check
  passed, no/resolved orphaned mounts, `Mount succeeded.`, the count of
  provisioned packages found, `Package removed.` for the matched package,
  `Unmount succeeded.`, `PoC run completed successfully.`
- Manually verify the removal by mounting the image read-only afterward
  and confirming the package is gone:
  ```
  dism /Mount-Wim /WimFile:C:\images\install.wim /Index:1 /MountDir:C:\mount /ReadOnly
  dism /Image:C:\mount /Get-ProvisionedAppxPackages
  dism /Unmount-Wim /MountDir:C:\mount /Discard
  ```
  The removed package should no longer appear in the list.

## What "failure" and recovery look like

- If DISM fails mid-run (e.g. bad index, locked file), the script logs the
  full DISM output, attempts an automatic `/Discard` to leave the `.wim`
  untouched, and exits with code `1`.
- If the automatic discard itself fails, the log will say so explicitly
  and tell you to run `dism /Get-MountedWimInfo` and
  `dism /Unmount-Wim /MountDir:<dir> /Discard` by hand.
- On the *next* run, orphaned-mount detection will catch any leftover
  mount and let you choose commit/discard/abort interactively instead of
  DISM failing on you unexpectedly.

## Known limitations (expected — this is a PoC, not the final tool)

- No progress parsing, no GUI, no config file for the package list — all
  hardcoded to a single `--package-match` argument.
- No disk-space pre-check.
- Only tested against `.wim`; `.esd` must be exported to `.wim` manually
  first (see above).

Once this has been run successfully (and the recovery path exercised at
least once) against a real image, report back so Stage 1 can begin.
