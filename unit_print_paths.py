from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final


APP_DIR: Final = Path(__file__).resolve().parent
LOCAL_PATHS_CONFIG: Final = APP_DIR / "quick_printer_local_paths.json"
RELEASE_ROOT_ENV: Final = "QUICK_PRINTER_RELEASE_ROOT"


def _expanded_path(value: object, *, app_dir: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    expanded = os.path.expandvars(os.path.expanduser(text))
    path = Path(expanded)
    return path if path.is_absolute() else app_dir / path


def resolve_release_root(
    *,
    app_dir: Path = APP_DIR,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve a local asset root without baking one operator path into Git."""
    environment = os.environ if environ is None else environ
    environment_path = _expanded_path(environment.get(RELEASE_ROOT_ENV), app_dir=app_dir)
    if environment_path is not None:
        return environment_path

    local_config = config_path or app_dir / LOCAL_PATHS_CONFIG.name
    if local_config.is_file():
        try:
            payload = json.loads(local_config.read_text(encoding="utf-8"))
            configured_path = _expanded_path(payload.get("release_root"), app_dir=app_dir)
            if configured_path is not None:
                return configured_path
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return app_dir


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def select_configured_root(
    configured: object,
    *,
    portable_root: Path,
    legacy_root: Path,
) -> Path:
    """Prefer the C-drive portable copy when a saved path is legacy or unavailable."""
    configured_path = _expanded_path(configured, app_dir=APP_DIR)
    if portable_root.is_dir() and (
        configured_path is None
        or same_path(configured_path, legacy_root)
        or not configured_path.is_dir()
    ):
        return portable_root
    if configured_path is not None:
        return configured_path
    return portable_root if portable_root.is_dir() else legacy_root


RELEASE_ROOT: Final = resolve_release_root()
DATA_ROOT: Final = RELEASE_ROOT / "data"
