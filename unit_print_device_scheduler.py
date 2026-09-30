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


def remove_dispatch_copy(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        # Disposable matching copies are cleaned again at startup/release
        # maintenance.  Cleanup failure must not turn a verified print into an
        # operator-visible failure.
        pass
