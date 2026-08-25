"""PySide6 GUI shell over the UI-independent `core/` engine.

Contains no business logic itself - every DISM/registry/ISO operation is a
call into `core/`, run on a background thread (see `gui.worker`,
`gui.blocking`) so the window never freezes during a multi-minute
operation. See win-iso-customizer-prompt.md section 2 for the
architectural rule this follows.

Licensing note: this project uses PySide6 (LGPL), not PyQt6 (GPL/commercial),
because closed-source distribution is planned - LGPL permits that as long
as PySide6 itself is dynamically linked (the default) rather than statically
bundled in a way that prevents relinking.
"""
