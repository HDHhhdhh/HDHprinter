from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    collect_registered_pdfs,
    load_json,
)
from unit_print_paths import RELEASE_ROOT  # noqa: E402


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

    return parser.parse_args()


def utc_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_relative_path(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
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
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


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


def main() -> int:
    args = parse_args()
    if args.command == "plan":
        return command_plan(args)
    if args.command == "publish":
        return command_publish(args)
    if args.command == "pull":
        return command_pull(args)
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
