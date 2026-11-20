from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_quick_printer_release import (  # noqa: E402
    app_source_commit,
    collect_registered_pdfs,
    load_json,
)
from unit_print_paths import LOCAL_PATHS_CONFIG, RELEASE_ROOT, RELEASE_ROOT_ENV  # noqa: E402
from unit_print_instance import SingleInstance  # noqa: E402


ASSET_SCHEMA_VERSION = 1
DEFAULT_CHANNEL_CONFIG = PROJECT_ROOT / "quick_printer_asset_channel.local.json"
DEFAULT_HASH_CACHE = PROJECT_ROOT / ".quick_printer_asset_hash_cache.json"
DEFAULT_STATE_FILE = ".quick_printer_asset_state.json"
DEFAULT_LOCAL_MANIFEST = ".quick_printer_asset_manifest.json"
DEFAULT_REMOTE_PREFIX = "quick-printer-assets/v1"
CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AssetEntry:
    category: str
    relative_path: str
    size: int
    sha256: str
    origin: str = "base"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PublishPlan:
    entries: tuple[AssetEntry, ...]
    upload_paths: tuple[str, ...]
    added_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PullPlan:
    download_paths: tuple[str, ...]
    missing_base_paths: tuple[str, ...]
    unchanged_overlay_paths: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish and pull incremental Quick Printer PDF assets through rclone."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan", help="Compare the current registered PDFs with a full-release baseline."
    )
    plan_parser.add_argument("--baseline-manifest", type=Path, required=True)
    plan_parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "unit_print_manager_config.json")
    plan_parser.add_argument("--hash-cache", type=Path, default=DEFAULT_HASH_CACHE)
    plan_parser.add_argument("--output", type=Path)

    publish_parser = subparsers.add_parser(
        "publish", help="Upload only new or changed PDFs and then publish the latest manifest."
    )
    publish_parser.add_argument("--channel-config", type=Path, default=DEFAULT_CHANNEL_CONFIG)
    publish_parser.add_argument("--baseline-manifest", type=Path)
    publish_parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "unit_print_manager_config.json")
    publish_parser.add_argument("--hash-cache", type=Path, default=DEFAULT_HASH_CACHE)
    publish_parser.add_argument("--initialize", action="store_true")
    publish_parser.add_argument("--version")
    publish_parser.add_argument("--dry-run", action="store_true")

    pull_parser = subparsers.add_parser(
        "pull", help="Download only missing or changed overlay PDFs into an extracted release."
    )
    pull_parser.add_argument("--channel-config", type=Path, default=DEFAULT_CHANNEL_CONFIG)
    pull_parser.add_argument(
        "--root",
        type=Path,
        help="Release root; defaults to quick_printer_local_paths.json or the app folder.",
    )
    pull_parser.add_argument("--state", type=Path)
    pull_parser.add_argument("--dry-run", action="store_true")

    for command, help_text in (
        ("status", "Report the installed app, catalog and PDF versions."),
        ("plan-update", "Check a target asset version without applying files."),
        ("stage-update", "Prepare a verified asset root in a new directory."),
        ("activate-assets", "Switch the app to a verified asset root while it is closed."),
    ):
        inspection = subparsers.add_parser(command, help=help_text)
        inspection.add_argument("--root", type=Path, default=RELEASE_ROOT)
        if command in {"plan-update", "stage-update"}:
            target = inspection.add_mutually_exclusive_group()
            target.add_argument("--manifest", type=Path, help="Use a downloaded manifest.")
            target.add_argument("--version", help="Fetch a pinned immutable asset version.")
            inspection.add_argument("--channel-config", type=Path, default=DEFAULT_CHANNEL_CONFIG)
        if command == "stage-update":
            inspection.add_argument("--destination", type=Path, required=True)
        if command == "activate-assets":
            inspection.add_argument("--previous", action="store_true", help="Restore the previously selected root.")

    immutable = subparsers.add_parser("publish-version", help="Publish an immutable version of registered PDFs.")
    immutable.add_argument("--channel-config", type=Path, default=DEFAULT_CHANNEL_CONFIG)
    immutable.add_argument("--config", type=Path, default=PROJECT_ROOT / "unit_print_manager_config.json")
    immutable.add_argument("--hash-cache", type=Path, default=DEFAULT_HASH_CACHE)
    immutable.add_argument("--version")
    immutable.add_argument("--dry-run", action="store_true")
    immutable.add_argument("--from-manifest", type=Path, action="append", default=[],
                           help="Accept exact bytes from an approved previous install or asset inventory.")
    immutable.add_argument("--retire-list", type=Path,
                           help="UTF-8 list of reviewed paths absent from the new catalog.")
    intake = subparsers.add_parser("plan-import", help="Inspect new Ilsan or A:ble PDFs without changing files.")
    intake.add_argument("--family", choices=("ilsan", "able"), required=True)
    intake.add_argument("--source-root", type=Path, required=True)
    intake.add_argument("--root", type=Path, default=RELEASE_ROOT)

    return parser.parse_args()


def utc_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_relative_path(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    path = PurePosixPath(text)
    if (not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts)
            or any(char in ':<>|?"*' or ord(char) < 32 for char in text)):
        raise ValueError(f"Unsafe asset path: {value}")
    return path.as_posix()


def safe_target(root: Path, relative_path: str) -> Path:
    normalized = normalize_relative_path(relative_path)
    root_resolved = root.resolve()
    target = (root_resolved / Path(*PurePosixPath(normalized).parts)).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Asset path escapes the target root: {relative_path}") from exc
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        while chunk := input_file.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(temporary, path)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def checked_version(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("Invalid asset version.")
    return value


def object_path(entry: AssetEntry) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", entry.sha256):
        raise ValueError(f"Invalid SHA-256: {entry.relative_path}")
    return f"objects/{entry.sha256}.pdf"


def entry_map(entries: Iterable[AssetEntry]) -> dict[str, AssetEntry]:
    result: dict[str, AssetEntry] = {}
    for entry in entries:
        relative_path = normalize_relative_path(entry.relative_path)
        key = relative_path.casefold()
        if key in result:
            raise ValueError(f"Duplicate asset path: {relative_path}")
        result[key] = AssetEntry(
            category=entry.category,
            relative_path=relative_path,
            size=int(entry.size),
            sha256=str(entry.sha256).casefold(),
            origin=entry.origin,
        )
    return result


def asset_set_sha256(entries: Iterable[AssetEntry]) -> str:
    rows = [
        {
            "category": entry.category,
            "relative_path": normalize_relative_path(entry.relative_path),
            "size": int(entry.size),
            "sha256": str(entry.sha256).casefold(),
        }
        for entry in sorted(entries, key=lambda item: item.relative_path.casefold())
    ]
    return json_sha256({"assets": rows})


def load_release_manifest(path: Path) -> tuple[dict[str, Any], tuple[AssetEntry, ...]]:
    payload = load_json(path)
    raw_entries = payload.get("pdfs")
    if not isinstance(raw_entries, list):
        raise ValueError(f"Release manifest has no PDF inventory: {path}")
    entries = tuple(
        AssetEntry(
            category=str(item.get("category", "")),
            relative_path=normalize_relative_path(str(item["relative_path"])),
            size=int(item["size"]),
            sha256=str(item["sha256"]).casefold(),
            origin="base",
        )
        for item in raw_entries
    )
    entry_map(entries)
    return payload, entries


def load_asset_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if int(payload.get("schema_version", 0)) != ASSET_SCHEMA_VERSION:
        raise ValueError(f"Unsupported asset manifest schema: {path}")
    raw_entries = payload.get("assets")
    if not isinstance(raw_entries, list):
        raise ValueError(f"Asset manifest has no assets list: {path}")
    entries = tuple(
        AssetEntry(
            category=str(item.get("category", "")),
            relative_path=normalize_relative_path(str(item["relative_path"])),
            size=int(item["size"]),
            sha256=str(item["sha256"]).casefold(),
            origin=str(item.get("origin", "base")),
        )
        for item in raw_entries
    )
    if any(entry.origin not in {"base", "overlay"} for entry in entries):
        raise ValueError(f"Asset manifest has an invalid origin: {path}")
    entry_map(entries)
    expected_digest = str(payload.get("asset_set_sha256", "")).casefold()
    if expected_digest and asset_set_sha256(entries) != expected_digest:
        raise ValueError(f"Asset manifest digest mismatch: {path}")
    raw_baseline_entries = payload.get("baseline_assets", [])
    if raw_baseline_entries and not isinstance(raw_baseline_entries, list):
        raise ValueError(f"Asset manifest has an invalid baseline inventory: {path}")
    baseline_entries = tuple(
        AssetEntry(
            category=str(item.get("category", "")),
            relative_path=normalize_relative_path(str(item["relative_path"])),
            size=int(item["size"]),
            sha256=str(item["sha256"]).casefold(),
            origin="base",
        )
        for item in raw_baseline_entries
    )
    if baseline_entries:
        entry_map(baseline_entries)
        expected_baseline_digest = str(
            (payload.get("baseline") or {}).get("asset_set_sha256", "")
        ).casefold()
        if expected_baseline_digest and asset_set_sha256(baseline_entries) != expected_baseline_digest:
            raise ValueError(f"Asset manifest baseline digest mismatch: {path}")
    payload["_entries"] = entries
    payload["_baseline_entries"] = baseline_entries
    return payload


def load_hash_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    records = payload.get("files", {})
    return records if isinstance(records, dict) else {}


def collect_current_assets(
    config_path: Path,
    hash_cache_path: Path,
) -> tuple[tuple[AssetEntry, ...], dict[str, Path]]:
    config = load_json(config_path)
    records = collect_registered_pdfs(config)
    cache = load_hash_cache(hash_cache_path)
    updated_cache: dict[str, dict[str, Any]] = {}
    entries: list[AssetEntry] = []
    sources: dict[str, Path] = {}
    total = len(records)
    for index, record in enumerate(records, start=1):
        source = record.source.resolve()
        stat = source.stat()
        cache_key = str(source)
        cached = cache.get(cache_key, {})
        if (
            int(cached.get("size", -1)) == stat.st_size
            and int(cached.get("mtime_ns", -1)) == stat.st_mtime_ns
            and isinstance(cached.get("sha256"), str)
        ):
            digest = str(cached["sha256"]).casefold()
        else:
            digest = sha256_file(source)
        updated_cache[cache_key] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest,
        }
        relative_path = normalize_relative_path(record.relative_path.as_posix())
        entries.append(
            AssetEntry(
                category=record.category,
                relative_path=relative_path,
                size=stat.st_size,
                sha256=digest,
                origin="base",
            )
        )
        sources[relative_path.casefold()] = source
        if index % 100 == 0 or index == total:
            print(f"Indexed {index:,}/{total:,} registered PDFs", flush=True)
    entry_map(entries)
    write_json_atomic(
        hash_cache_path,
        {"schema_version": 1, "generated_at": utc_timestamp(), "files": updated_cache},
    )
    return tuple(sorted(entries, key=lambda item: item.relative_path.casefold())), sources


def same_content(left: AssetEntry, right: AssetEntry) -> bool:
    return left.size == right.size and left.sha256.casefold() == right.sha256.casefold()


def build_publish_plan(
    current_entries: Iterable[AssetEntry],
    prior_entries: Iterable[AssetEntry],
    baseline_entries: Iterable[AssetEntry],
    *,
    existing_overlay_paths: Iterable[str] = (),
) -> PublishPlan:
    current = entry_map(current_entries)
    prior = entry_map(prior_entries)
    baseline = entry_map(baseline_entries)
    # Older schema-1 publishers could relabel a rollback as "base" while an
    # installed client still held its overlay bytes. The remote inventory also
    # preserves that history when a later no-change manifest has forgotten it.
    overlay_keys = {
        normalize_relative_path(path).casefold() for path in existing_overlay_paths
    }
    overlay_keys.update(key for key, entry in prior.items() if entry.origin == "overlay")
    final_entries: list[AssetEntry] = []
    upload_paths: list[str] = []
    added_paths: list[str] = []
    changed_paths: list[str] = []

    for key, current_entry in sorted(current.items(), key=lambda item: item[1].relative_path.casefold()):
        prior_entry = prior.get(key)
        baseline_entry = baseline.get(key)
        if baseline_entry is not None and same_content(current_entry, baseline_entry):
            # Once a path was distributed as an overlay, its baseline content
            # must remain downloadable too: clients may already have the update.
            origin = "overlay" if key in overlay_keys else "base"
        elif prior_entry is not None and same_content(current_entry, prior_entry):
            origin = prior_entry.origin
        else:
            origin = "overlay"
        final_entry = AssetEntry(
            category=current_entry.category,
            relative_path=current_entry.relative_path,
            size=current_entry.size,
            sha256=current_entry.sha256,
            origin=origin,
        )
        final_entries.append(final_entry)

        if prior_entry is None:
            added_paths.append(final_entry.relative_path)
        elif not same_content(final_entry, prior_entry):
            changed_paths.append(final_entry.relative_path)
        if origin == "overlay" and (
            prior_entry is None
            or prior_entry.origin != "overlay"
            or not same_content(final_entry, prior_entry)
        ):
            upload_paths.append(final_entry.relative_path)

    removed_paths = sorted(
        (entry.relative_path for key, entry in prior.items() if key not in current),
        key=str.casefold,
    )
    return PublishPlan(
        entries=tuple(final_entries),
        upload_paths=tuple(upload_paths),
        added_paths=tuple(added_paths),
        changed_paths=tuple(changed_paths),
        removed_paths=tuple(removed_paths),
    )


def local_file_matches(path: Path, entry: AssetEntry) -> bool:
    if not path.is_file() or path.stat().st_size != entry.size:
        return False
    return sha256_file(path).casefold() == entry.sha256.casefold()


def build_pull_plan(root: Path, entries: Iterable[AssetEntry]) -> PullPlan:
    downloads: list[str] = []
    missing_base: list[str] = []
    unchanged_overlay: list[str] = []
    for entry in sorted(entries, key=lambda item: item.relative_path.casefold()):
        target = safe_target(root, entry.relative_path)
        if entry.origin == "base":
            if not local_file_matches(target, entry):
                missing_base.append(entry.relative_path)
            continue
        if local_file_matches(target, entry):
            unchanged_overlay.append(entry.relative_path)
        else:
            downloads.append(entry.relative_path)
    return PullPlan(tuple(downloads), tuple(missing_base), tuple(unchanged_overlay))


def installed_inventory(root: Path) -> tuple[dict[str, Any], dict[str, AssetEntry]]:
    release_path = root / "release_manifest.json"
    applied_path = root / DEFAULT_LOCAL_MANIFEST
    catalog_path = PROJECT_ROOT / "unit_print_textbook_catalog.json"
    release, baseline = load_release_manifest(release_path) if release_path.is_file() else ({}, ())
    applied = load_asset_manifest(applied_path) if applied_path.is_file() else {}
    revision = app_source_commit()
    info = {
        "root": str(root),
        "configured_root": str(RELEASE_ROOT),
        "app_commit": revision or release.get("app_commit"),
        "baseline_app_commit": release.get("app_commit"),
        "release_name": release.get("release_name"),
        "baseline_digest": asset_set_sha256(baseline) if baseline else
            (applied.get("baseline") or {}).get("asset_set_sha256"),
        "catalog_schema": load_json(catalog_path).get("schema_version") if catalog_path.is_file() else None,
        "catalog_sha256": {name: sha256_file(PROJECT_ROOT / name) for name in
                           ("unit_print_catalog.json", "unit_print_textbook_catalog.json")
                           if (PROJECT_ROOT / name).is_file()},
        "asset_version": applied.get("version"),
        "asset_set_sha256": applied.get("asset_set_sha256") or
            (asset_set_sha256(baseline) if baseline else None),
    }
    known = entry_map(baseline)
    known.update(entry_map(applied.get("_entries", ())))
    return info, known


def inspect_update(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    info, installed = installed_inventory(root)
    target = entry_map(manifest["_entries"])
    downloads, missing_base, conflicts = [], [], []
    for key, entry in target.items():
        path = safe_target(root, entry.relative_path)
        if local_file_matches(path, entry):
            continue
        if entry.origin == "base":
            missing_base.append(entry.relative_path)
        elif path.exists():
            prior = installed.get(key)
            digest = sha256_file(path) if path.is_file() else None
            accepted = manifest.get("accepted_previous_hashes", {}).get(key, ())
            if digest is None or (digest not in accepted and (prior is None or digest != prior.sha256)):
                conflicts.append(entry.relative_path)
            else:
                downloads.append(entry.relative_path)
        else:
            downloads.append(entry.relative_path)
    retired = {normalize_relative_path(path).casefold() for path in manifest.get("retired_paths", [])}
    omitted = [item.relative_path for key, item in installed.items() if key not in target and key not in retired]
    state = "ready"
    if (manifest.get("catalog_schema") not in (None, info["catalog_schema"])
            or manifest.get("catalog_sha256") not in (None, info["catalog_sha256"])):
        state = "catalog_incompatible"
    elif conflicts:
        state = "local_conflict"
    elif missing_base:
        state = "baseline_repair_required"
    elif omitted:
        state = "inventory_review_required"
    elif not downloads:
        state = "current"
    return {
        **info,
        "status": state,
        "target_version": manifest.get("version"),
        "target_baseline_digest": (manifest.get("baseline") or {}).get("asset_set_sha256"),
        "target_asset_set_sha256": manifest.get("asset_set_sha256"),
        "download_count": len(downloads),
        "download_bytes": sum(target[path.casefold()].size for path in downloads),
        "download_paths": downloads,
        "missing_base_paths": missing_base,
        "local_conflicts": conflicts,
        "omitted_paths": omitted,
        "retired_paths": sorted(item.relative_path for key, item in installed.items() if key in retired),
    }


def command_inspect(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if args.command == "status":
        report, _ = installed_inventory(root)
    elif args.manifest is not None:
        report = inspect_update(root, load_asset_manifest(args.manifest.resolve()))
    else:
        channel = load_channel_config(args.channel_config.resolve())
        executable = find_rclone(str(channel.get("rclone_exe", "")).strip() or None)
        with tempfile.TemporaryDirectory(prefix="quick-printer-inspect-") as temporary:
            manifest_path = Path(temporary) / "latest_assets.json"
            version = getattr(args, "version", None)
            path = f"versions/{checked_version(version)}.json" if version else "latest_assets.json"
            copy_remote_file(executable, channel, path, manifest_path)
            report = inspect_update(root, load_asset_manifest(manifest_path))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def load_channel_config(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError(f"Unsupported channel config schema: {path}")
    remote = str(payload.get("remote", "")).strip()
    if not remote.endswith(":"):
        raise ValueError("Channel config 'remote' must look like 'gdrive:'.")
    remote_prefix = str(payload.get("remote_prefix", DEFAULT_REMOTE_PREFIX)).strip(" /\\")
    if not remote_prefix:
        raise ValueError("Channel config needs a non-empty remote_prefix.")
    payload["remote"] = remote
    payload["remote_prefix"] = remote_prefix
    return payload


def find_rclone(explicit: str | None = None) -> Path:
    if explicit:
        candidate = Path(os.path.expandvars(explicit)).expanduser()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Configured rclone.exe was not found: {candidate}")
    command = shutil.which("rclone")
    if command:
        return Path(command)
    candidates = [
        Path.home() / "scoop" / "apps" / "rclone" / "current" / "rclone.exe",
        Path(os.environ.get("ChocolateyInstall", r"C:\ProgramData\chocolatey")) / "bin" / "rclone.exe",
    ]
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    winget_root = local_app_data / "Microsoft" / "WinGet" / "Packages"
    if winget_root.is_dir():
        candidates.extend(winget_root.glob("Rclone.Rclone_*/*/rclone.exe"))
        candidates.extend(winget_root.glob("Rclone.Rclone_*/rclone.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("rclone.exe was not found. Install it with: winget install Rclone.Rclone")


def remote_path(channel: dict[str, Any], suffix: str = "") -> str:
    base = f"{channel['remote']}{channel['remote_prefix']}"
    clean_suffix = suffix.strip(" /\\")
    return f"{base}/{clean_suffix}" if clean_suffix else base


def rclone_common_args(channel: dict[str, Any]) -> list[str]:
    args: list[str] = []
    root_id = str(channel.get("drive_root_folder_id", "")).strip()
    if root_id:
        args.extend(["--drive-root-folder-id", root_id])
    return args


def run_rclone(
    executable: Path,
    channel: dict[str, Any],
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(executable), *arguments, *rclone_common_args(channel)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"rclone exited with {result.returncode}")
    return result


def copy_remote_file(
    executable: Path,
    channel: dict[str, Any],
    suffix: str,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_rclone(
        executable,
        channel,
        ["copyto", remote_path(channel, suffix), str(destination), "--retries", "5", "--low-level-retries", "10"],
    )


def upload_remote_file(
    executable: Path,
    channel: dict[str, Any],
    source: Path,
    suffix: str,
) -> None:
    run_rclone(
        executable,
        channel,
        ["copyto", str(source), remote_path(channel, suffix), "--checksum", "--retries", "5", "--low-level-retries", "10"],
    )


def list_existing_overlay_paths(
    executable: Path,
    channel: dict[str, Any],
) -> tuple[str, ...]:
    """Find legacy overlays without assuming that an overlay folder exists."""
    result = run_rclone(
        executable,
        channel,
        ["lsf", remote_path(channel), "--recursive", "--files-only", "--format", "p"],
    )
    prefix = "overlay/"
    return tuple(
        normalize_relative_path(path[len(prefix):])
        for path in result.stdout.splitlines()
        if path.casefold().startswith(prefix)
    )


def manifest_payload(
    *,
    version: str,
    baseline: dict[str, Any],
    baseline_entries: Iterable[AssetEntry],
    plan: PublishPlan,
) -> dict[str, Any]:
    frozen_baseline = tuple(baseline_entries)
    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "version": version,
        "generated_at": utc_timestamp(),
        "baseline": baseline,
        "baseline_assets": [
            AssetEntry(
                category=entry.category,
                relative_path=entry.relative_path,
                size=entry.size,
                sha256=entry.sha256,
                origin="base",
            ).to_json()
            for entry in frozen_baseline
        ],
        "asset_set_sha256": asset_set_sha256(plan.entries),
        "asset_count": len(plan.entries),
        "asset_bytes": sum(entry.size for entry in plan.entries),
        "changes": {
            "added": list(plan.added_paths),
            "changed": list(plan.changed_paths),
            "removed": list(plan.removed_paths),
            "uploaded": list(plan.upload_paths),
        },
        "assets": [entry.to_json() for entry in plan.entries],
    }


def baseline_descriptor(path: Path, payload: dict[str, Any], entries: Iterable[AssetEntry]) -> dict[str, Any]:
    return {
        "release_name": str(payload.get("release_name", path.parent.name)),
        "manifest_sha256": sha256_file(path),
        "asset_set_sha256": asset_set_sha256(entries),
        "registered_pdf_count": len(tuple(entries)),
    }


def print_summary(plan: PublishPlan) -> None:
    print(
        json.dumps(
            {
                "asset_count": len(plan.entries),
                "upload_count": len(plan.upload_paths),
                "added_count": len(plan.added_paths),
                "changed_count": len(plan.changed_paths),
                "removed_count": len(plan.removed_paths),
                "upload_paths": list(plan.upload_paths),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_plan(args: argparse.Namespace) -> int:
    baseline_payload, baseline_entries = load_release_manifest(args.baseline_manifest.resolve())
    current_entries, _sources = collect_current_assets(args.config.resolve(), args.hash_cache.resolve())
    plan = build_publish_plan(current_entries, baseline_entries, baseline_entries)
    payload = manifest_payload(
        version="plan",
        baseline=baseline_descriptor(args.baseline_manifest.resolve(), baseline_payload, baseline_entries),
        baseline_entries=baseline_entries,
        plan=plan,
    )
    if args.output:
        write_json_atomic(args.output.resolve(), payload)
    print_summary(plan)
    return 0


def command_publish(args: argparse.Namespace) -> int:
    channel = load_channel_config(args.channel_config.resolve())
    executable = find_rclone(str(channel.get("rclone_exe", "")).strip() or None)
    version = args.version or utc_version()

    with tempfile.TemporaryDirectory(prefix="quick-printer-asset-publish-") as temp_text:
        temporary = Path(temp_text)
        remote_latest = temporary / "remote_latest_assets.json"
        existing_overlay_paths: tuple[str, ...] = ()
        if args.initialize:
            if args.baseline_manifest is None:
                raise ValueError("--initialize requires --baseline-manifest.")
            existing = run_rclone(
                executable,
                channel,
                ["copyto", remote_path(channel, "latest_assets.json"), str(remote_latest)],
                check=False,
            )
            if existing.returncode == 0 and remote_latest.is_file():
                raise FileExistsError("The asset channel is already initialized; omit --initialize.")
            baseline_path = args.baseline_manifest.resolve()
            baseline_payload, baseline_entries = load_release_manifest(baseline_path)
            prior_entries = baseline_entries
            baseline = baseline_descriptor(baseline_path, baseline_payload, baseline_entries)
        else:
            copy_remote_file(executable, channel, "latest_assets.json", remote_latest)
            prior_manifest = load_asset_manifest(remote_latest)
            prior_entries = tuple(prior_manifest["_entries"])
            baseline = dict(prior_manifest.get("baseline") or {})
            if not baseline:
                raise ValueError("Remote asset manifest has no baseline descriptor.")
            if args.baseline_manifest is not None:
                baseline_path = args.baseline_manifest.resolve()
                baseline_payload, baseline_entries = load_release_manifest(baseline_path)
                if baseline_descriptor(baseline_path, baseline_payload, baseline_entries) != baseline:
                    raise ValueError("The supplied baseline manifest does not match the channel baseline.")
            else:
                baseline_entries = tuple(prior_manifest.get("_baseline_entries") or ())
                if not baseline_entries:
                    raise ValueError(
                        "Remote asset manifest predates the baseline inventory. "
                        "Publish once with --baseline-manifest."
                    )
            existing_overlay_paths = list_existing_overlay_paths(executable, channel)

        current_entries, sources = collect_current_assets(args.config.resolve(), args.hash_cache.resolve())
        plan = build_publish_plan(
            current_entries, prior_entries, baseline_entries,
            existing_overlay_paths=existing_overlay_paths,
        )
        print_summary(plan)
        if args.dry_run:
            return 0

        stage = temporary / "stage"
        upload_list = temporary / "upload_files.txt"
        upload_lines: list[str] = []
        for relative_path in plan.upload_paths:
            source = sources[relative_path.casefold()]
            staged = safe_target(stage / "overlay", relative_path)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            upload_lines.append(f"overlay/{relative_path}")
        upload_list.write_text("\n".join(upload_lines) + ("\n" if upload_lines else ""), encoding="utf-8")

        if upload_lines:
            transfer_args = [
                "copy",
                str(stage),
                remote_path(channel),
                "--files-from",
                str(upload_list),
                "--checksum",
                "--transfers",
                str(channel.get("transfers", 12)),
                "--checkers",
                str(channel.get("checkers", 24)),
                "--drive-chunk-size",
                str(channel.get("drive_chunk_size", "64M")),
                "--retries",
                "8",
                "--low-level-retries",
                "20",
                "--stats",
                "10s",
                "--stats-one-line",
            ]
            run_rclone(executable, channel, transfer_args)
            run_rclone(
                executable,
                channel,
                [
                    "check",
                    str(stage),
                    remote_path(channel),
                    "--files-from",
                    str(upload_list),
                    "--one-way",
                    "--checksum",
                ],
            )

        payload = manifest_payload(
            version=version,
            baseline=baseline,
            baseline_entries=baseline_entries,
            plan=plan,
        )
        history_path = temporary / f"{version}.json"
        latest_path = temporary / "latest_assets.json"
        write_json_atomic(history_path, payload)
        write_json_atomic(latest_path, payload)
        upload_remote_file(executable, channel, history_path, f"history/{version}.json")
        upload_remote_file(executable, channel, latest_path, "latest_assets.json")
        print(f"Published asset channel version {version}: {len(plan.upload_paths)} PDF(s) uploaded.")
    return 0


def command_pull(args: argparse.Namespace) -> int:
    root = (args.root or RELEASE_ROOT).resolve()
    channel = load_channel_config(args.channel_config.resolve())
    executable = find_rclone(str(channel.get("rclone_exe", "")).strip() or None)
    state_path = args.state.resolve() if args.state else root / DEFAULT_STATE_FILE

    with tempfile.TemporaryDirectory(prefix="quick-printer-asset-pull-") as temp_text:
        temporary = Path(temp_text)
        latest_path = temporary / "latest_assets.json"
        copy_remote_file(executable, channel, "latest_assets.json", latest_path)
        manifest = load_asset_manifest(latest_path)
        entries = tuple(manifest["_entries"])
        plan = build_pull_plan(root, entries)
        summary = {
            "version": manifest.get("version"),
            "download_count": len(plan.download_paths),
            "missing_base_count": len(plan.missing_base_paths),
            "unchanged_overlay_count": len(plan.unchanged_overlay_paths),
            "download_paths": list(plan.download_paths),
            "missing_base_paths": list(plan.missing_base_paths),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if plan.missing_base_paths:
            sample = "\n".join(f"- {path}" for path in plan.missing_base_paths[:10])
            raise FileNotFoundError(
                "The base release has missing or modified PDFs (SHA-256 mismatch). "
                "Restore these files from the original full release, or have the publisher "
                "republish a repaired asset manifest before applying updates.\n"
                + sample
            )
        if args.dry_run:
            return 0

        if plan.download_paths:
            files_from = temporary / "download_files.txt"
            files_from.write_text(
                "".join(f"overlay/{path}\n" for path in plan.download_paths),
                encoding="utf-8",
            )
            incoming = temporary / "incoming"
            run_rclone(
                executable,
                channel,
                [
                    "copy",
                    remote_path(channel),
                    str(incoming),
                    "--files-from",
                    str(files_from),
                    "--checksum",
                    "--transfers",
                    str(channel.get("transfers", 8)),
                    "--checkers",
                    str(channel.get("checkers", 16)),
                    "--retries",
                    "8",
                    "--low-level-retries",
                    "20",
                ],
            )
            entries_by_path = {entry.relative_path.casefold(): entry for entry in entries}
            for relative_path in plan.download_paths:
                entry = entries_by_path[relative_path.casefold()]
                incoming_path = safe_target(incoming / "overlay", relative_path)
                if not local_file_matches(incoming_path, entry):
                    raise ValueError(f"Downloaded asset failed SHA-256 verification: {relative_path}")
            for relative_path in plan.download_paths:
                incoming_path = safe_target(incoming / "overlay", relative_path)
                target = safe_target(root, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_target = target.with_name(f"{target.name}.asset-sync.tmp")
                shutil.copy2(incoming_path, temporary_target)
                os.replace(temporary_target, target)

        state_payload = {
            "schema_version": 1,
            "updated_at": utc_timestamp(),
            "version": manifest.get("version"),
            "asset_set_sha256": manifest.get("asset_set_sha256"),
            "downloaded_paths": list(plan.download_paths),
        }
        local_manifest_payload = {
            key: value for key, value in manifest.items() if not key.startswith("_")
        }
        write_json_atomic(root / DEFAULT_LOCAL_MANIFEST, local_manifest_payload)
        write_json_atomic(state_path, state_payload)
        print(f"Asset sync complete: {len(plan.download_paths)} PDF(s) downloaded.")
    return 0


def transfer_list(executable: Path, channel: dict[str, Any], source: str,
                  destination: str, work: Path, paths: Iterable[str], *, publish: bool = False) -> None:
    paths = sorted(set(paths))
    if not paths:
        return
    listing = work / "transfer.txt"
    listing.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    options = ["--files-from-raw", str(listing), "--checksum"]
    run_rclone(executable, channel, [
        "copy", source, destination, *options, *(["--immutable"] if publish else []),
    ])
    if publish:
        run_rclone(executable, channel, ["check", source, destination, *options, "--one-way"])


def command_publish_version(args: argparse.Namespace) -> int:
    channel = load_channel_config(args.channel_config.resolve())
    executable = find_rclone(str(channel.get("rclone_exe", "")).strip() or None)
    version = checked_version(args.version or utc_version())
    entries, sources = collect_current_assets(args.config.resolve(), args.hash_cache.resolve())
    if not entries:
        raise ValueError("No registered PDFs to publish.")
    previous_hashes: dict[str, set[str]] = {}
    omitted = set()
    current = entry_map(entries)
    for previous_path in getattr(args, "from_manifest", ()):
        previous = (load_release_manifest(previous_path)[1] if "pdfs" in load_json(previous_path)
                    else load_asset_manifest(previous_path)["_entries"])
        for key, record in entry_map(previous).items():
            if key not in current:
                omitted.add(key)
            elif not same_content(record, current[key]):
                previous_hashes.setdefault(key, set()).add(record.sha256)
    retire_list = getattr(args, "retire_list", None)
    retired = ({normalize_relative_path(line).casefold()
                for line in retire_list.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
               if retire_list is not None else set())
    if omitted != retired:
        raise ValueError(f"Retired paths require explicit review: {sorted(omitted ^ retired)}")
    objects = {object_path(entry): entry for entry in entries}
    remote = remote_path(channel)
    listing = run_rclone(executable, channel, ["lsf", remote, "--recursive", "--files-only"])
    existing = set(listing.stdout.splitlines())
    manifest_name = f"versions/{version}.json"
    if manifest_name in existing:
        raise FileExistsError(f"Asset version already published: {version}")
    new_paths = sorted(objects.keys() - existing)
    print(json.dumps({"version": version, "asset_count": len(entries),
                      "upload_count": len(new_paths), "upload_bytes": sum(objects[p].size for p in new_paths)}))
    if args.dry_run:
        return 0
    with tempfile.TemporaryDirectory(prefix="quick-printer-immutable-") as temp:
        work = Path(temp)
        payload_root = work / "upload"
        payload_root.mkdir()
        for path, entry in objects.items():
            target = safe_target(payload_root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sources[entry.relative_path.casefold()], target)
            if not local_file_matches(target, entry):
                raise ValueError(f"Publisher input changed during staging: {entry.relative_path}")
        transfer_list(executable, channel, str(payload_root), remote, work, new_paths, publish=True)
        # Verify previously published objects too before referencing them.
        retained = objects.keys() & existing
        if retained:
            paths_file = work / "retained.txt"
            paths_file.write_text("".join(f"{path}\n" for path in sorted(retained)), encoding="utf-8")
            run_rclone(executable, channel, ["check", str(payload_root), remote, "--files-from-raw",
                                            str(paths_file), "--checksum", "--one-way"])
        manifest = manifest_payload(
            version=version, baseline={}, baseline_entries=(),
            plan=PublishPlan(tuple(AssetEntry(e.category, e.relative_path, e.size, e.sha256, "overlay")
                                   for e in entries), (), (), (), ()),
        )
        info, _ = installed_inventory(RELEASE_ROOT)
        manifest.update(storage="sha256", catalog_schema=info["catalog_schema"],
                        catalog_sha256=info["catalog_sha256"], app_commit=info["app_commit"],
                        accepted_previous_hashes={key: sorted(hashes) for key, hashes in previous_hashes.items()},
                        retired_paths=sorted(retired))
        local_manifest = work / "version.json"
        write_json_atomic(local_manifest, manifest)
        run_rclone(executable, channel, ["copyto", str(local_manifest), remote_path(channel, manifest_name),
                                        "--immutable", "--checksum"])
        received = work / "received.json"
        copy_remote_file(executable, channel, manifest_name, received)
        if sha256_file(local_manifest) != sha256_file(received):
            raise ValueError("Published manifest failed verification.")
    return 0


def command_plan_import(args: argparse.Namespace) -> int:
    from pypdf import PdfReader
    from unit_print_materials import scan_able_catalog
    from unit_print_server_catalog import build_registered_test_assets
    from unit_print_test_catalog import scan_test_catalog

    source_root = args.source_root.resolve()
    paths = {}
    plans = []
    if args.family == "ilsan":
        family_root = Path("data", "일산 기출")
        catalog = scan_test_catalog(source_root, "일산기출")
        registered = build_registered_test_assets({}, source_root, {"일산기출": catalog})
        for asset in registered:
            paths[asset.path] = {"material_id": asset.material_id, "server_id": asset.server_test_id,
                                 "subject": asset.subject, "term": asset.term, "level": asset.level}
    else:
        family_root = Path("data", "Able", "렉쳐노트")
        for subject in scan_able_catalog(source_root).subjects:
            for path in subject.full_documents:
                paths[path] = {"subject": subject.subject, "type": "full_document"}
            if subject.review_document is not None:
                paths[subject.review_document] = {"subject": subject.subject, "type": "review"}
            for number, path in subject.lesson_documents:
                paths[path] = {"subject": subject.subject, "type": "lesson", "lesson": number}
            plans.append({"subject": subject.subject, "lessons": subject.lesson_numbers,
                          "full_set": [p.relative_to(source_root).as_posix() for p in subject.full_set_documents]})

    files = []
    for source, metadata in sorted(paths.items(), key=lambda item: item[0].as_posix().casefold()):
        path = (family_root / source.relative_to(source_root)).as_posix()
        source = safe_target(source_root, source.relative_to(source_root).as_posix())
        target = safe_target(args.root, path)
        with source.open("rb") as stream:
            pages = len(PdfReader(stream).pages)
        if pages < 1:
            raise ValueError(f"PDF has no pages: {path}")
        digest = sha256_file(source)
        installed = sha256_file(target) if target.is_file() else None
        action = "unchanged" if installed == digest else ("replace" if installed else "add")
        files.append({"path": path, "action": action, "pages": pages, "sha256": digest,
                      "installed_sha256": installed, **metadata})
    excluded = [p.relative_to(source_root).as_posix() for p in source_root.rglob("*")
                if p.is_file() and p.suffix.casefold() == ".pdf" and p not in paths]
    print(json.dumps({"files": files, "review_unrecognized": sorted(excluded),
                      "able_print_sets": plans}, ensure_ascii=False, indent=2))
    return 0


def command_stage_update(args: argparse.Namespace) -> int:
    root, destination = args.root.resolve(), args.destination.resolve()
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError("Choose a new destination outside the installed asset root.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".quick-printer-stage-", dir=destination.parent) as temp:
        work = Path(temp)
        channel = None
        if args.manifest is None:
            channel = load_channel_config(args.channel_config.resolve())
            executable = find_rclone(str(channel.get("rclone_exe", "")).strip() or None)
            version = checked_version(args.version) if args.version else None
            name = f"versions/{version}.json" if version else "latest_assets.json"
            manifest_path = work / "target.json"
            copy_remote_file(executable, channel, name, manifest_path)
        else:
            manifest_path = args.manifest.resolve()
        manifest = load_asset_manifest(manifest_path)
        report = inspect_update(root, manifest)
        if report["status"] not in {"ready", "current"}:
            raise ValueError(f"Cannot stage this asset version: {report['status']}. Run plan-update for details.")
        entries = manifest["_entries"]
        if not entries or asset_set_sha256(entries) != manifest.get("asset_set_sha256"):
            raise ValueError("A verified asset inventory is required.")
        if any(
            not (entry.relative_path.startswith("data/") or entry.relative_path == "unit_print_separator_a4.pdf")
            or not entry.relative_path.casefold().endswith(".pdf")
            for entry in entries
        ):
            raise ValueError("Asset inventories may contain only registered PDFs.")
        to_download = set(report["download_paths"])
        if shutil.disk_usage(work).free < sum(entry.size for entry in entries) + report["download_bytes"]:
            raise OSError("Insufficient space to prepare and verify the asset snapshot.")
        incoming = work / "incoming"
        if to_download:
            if channel is None:
                channel = load_channel_config(args.channel_config.resolve())
                executable = find_rclone(str(channel.get("rclone_exe", "")).strip() or None)
            remote_paths = [object_path(e) if manifest.get("storage") == "sha256"
                            else f"overlay/{e.relative_path}" for e in entries if e.relative_path in to_download]
            transfer_list(executable, channel, remote_path(channel), str(incoming), work, remote_paths)
        snapshot = work / "snapshot"
        snapshot.mkdir()
        for entry in entries:
            source = safe_target(root, entry.relative_path)
            if entry.relative_path in to_download:
                relative = object_path(entry) if manifest.get("storage") == "sha256" else f"overlay/{entry.relative_path}"
                source = safe_target(incoming, relative)
            target = safe_target(snapshot, entry.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if not local_file_matches(target, entry):
                raise ValueError(f"Asset copy failed verification: {entry.relative_path}")
        shutil.copyfile(manifest_path, snapshot / DEFAULT_LOCAL_MANIFEST)
        snapshot.rename(destination)
    print(json.dumps({"prepared_root": str(destination), "version": manifest.get("version")}, ensure_ascii=False))
    return 0


def command_activate_assets(args: argparse.Namespace) -> int:
    if os.environ.get(RELEASE_ROOT_ENV, "").strip():
        raise ValueError(f"Clear the {RELEASE_ROOT_ENV} override before switching asset roots.")
    lease = SingleInstance()
    if not lease.acquire():
        raise RuntimeError("Close Quick Printer before activating an asset version.")
    try:
        settings = load_json(LOCAL_PATHS_CONFIG) if LOCAL_PATHS_CONFIG.is_file() else {}
        previous_root = settings.get("previous_release_root")
        if args.previous and not previous_root:
            raise ValueError("No previous asset root is recorded.")
        root = Path(previous_root).resolve() if args.previous else args.root.resolve()
        if args.previous and not (root / DEFAULT_LOCAL_MANIFEST).is_file():
            if not (root / "data").is_dir():
                raise ValueError("The previous asset root is unavailable.")
        else:
            manifest = load_asset_manifest(root / DEFAULT_LOCAL_MANIFEST)
            if inspect_update(root, manifest)["status"] != "current":
                raise ValueError("The staged assets have missing, incompatible, or changed files.")
        current_root = RELEASE_ROOT.resolve()
        if root != current_root:
            settings.update(release_root=str(root), previous_release_root=str(current_root))
            write_json_atomic(LOCAL_PATHS_CONFIG, settings)
    finally:
        lease.close()
    print(json.dumps({"active_root": str(root)}, ensure_ascii=False))
    return 0


def main() -> int:
    args = parse_args()
    if args.command in {"status", "plan-update"}:
        return command_inspect(args)
    if args.command == "publish-version":
        return command_publish_version(args)
    if args.command == "plan-import":
        return command_plan_import(args)
    if args.command == "stage-update":
        return command_stage_update(args)
    if args.command == "activate-assets":
        return command_activate_assets(args)
    if args.command == "plan":
        return command_plan(args)
    if args.command == "publish":
        return command_publish(args)
    if args.command == "pull":
        return command_pull(args)
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
