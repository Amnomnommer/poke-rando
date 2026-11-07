"""Unique disposable workroots with marker-guarded cleanup."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from . import BuildError

MARKER = ".b2er-workroot"


def create(base: Path, label: str) -> Path:
    base = Path(base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=base))
    (path / MARKER).write_text("disposable b2er workroot; CONTAINS LOCAL GAME DATA (ROM copies, extracted files); "
                               "never commit, upload, or share it; safe to delete\n", encoding="utf-8")
    return path


def remove(path: Path, base: Path) -> None:
    """Delete a workroot only if it carries the marker and lies directly inside ``base``."""
    path, base = Path(path).resolve(), Path(base).resolve()
    if path.parent != base:
        raise BuildError(f"refusing to delete {path}: not directly inside the workroot base {base}")
    if not (path / MARKER).is_file() or (path / MARKER).is_symlink():
        raise BuildError(f"refusing to delete {path}: workroot marker missing")

    def onerror(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    for dirpath, dirnames, _ in os.walk(path):
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full):
                os.chmod(full, 0o700)
    shutil.rmtree(path, onerror=onerror)
