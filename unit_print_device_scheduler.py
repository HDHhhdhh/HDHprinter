"""Helpers that make Canon job-log matching deterministic."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path


def new_dispatch_token() -> str:
    return uuid.uuid4().hex[:16].upper()


def prepare_dispatch_copy(source: Path, spool_dir: Path, token: str) -> Path:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    safe_stem = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", source.stem).strip("._")[:60] or "document"
    spool_dir.mkdir(parents=True, exist_ok=True)
    target = spool_dir / f"QP_{token}_{safe_stem}.pdf"
    shutil.copy2(source, target)
    return target


def remove_dispatch_copy(path: Path, *, spool_dir: Path | None = None) -> bool:
    """Remove only a tokenized PDF directly inside the owned dispatch spool.

    Cleanup failure must not change a verified physical completion. Refuse
    arbitrary source paths and symlinks even if a future caller passes them.
    """
    owned = spool_dir or Path(__file__).resolve().parent / "output" / "unit_print_manager" / "device_spool"
    target = Path(path)
    try:
        if not re.fullmatch(r"QP_[0-9A-F]{16}_.+\.pdf", target.name):
            return False
        if target.is_symlink() or target.resolve().parent != Path(owned).resolve():
            return False
        target.unlink(missing_ok=True)
        return True
    except (OSError, RuntimeError):
        return False
