"""Printer device status values shared by the monitor, scheduler, and UI.

The Windows spooler reports whether a document was handed to a queue.  These
types describe the separate, physical-device view obtained from the printer's
Remote UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Iterable


class DeviceHealth(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    BUSY = "busy"
    HELD = "held"
    ERROR = "error"
    OFFLINE = "offline"


class DeviceJobState(StrEnum):
    QUEUED = "queued"
    PRINTING = "printing"
    HELD = "held"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PaperSupply:
    source: str
    paper_size: str
    level_min_percent: int | None = None
    level_max_percent: int | None = None
    level_text: str = "알 수 없음"

    @property
    def is_a4(self) -> bool:
        compact = self.paper_size.upper().replace(" ", "").replace("-", "")
        return compact.startswith("A4")

    @property
    def midpoint_percent(self) -> int | None:
        if self.level_min_percent is None or self.level_max_percent is None:
            return None
        return round((self.level_min_percent + self.level_max_percent) / 2)


@dataclass(frozen=True)
class DeviceConsumables:
    paper: tuple[PaperSupply, ...] = ()
    toner_text: str = "알 수 없음"
    staple_text: str = "알 수 없음"
    saddle_staple_text: str = "알 수 없음"

    @property
    def a4_sources(self) -> tuple[PaperSupply, ...]:
        return tuple(item for item in self.paper if item.is_a4)

    @property
    def a4_level_text(self) -> str:
        sources = self.a4_sources
        if not sources:
            return "A4 알 수 없음"
        details = ", ".join(f"{item.source} {item.level_text}" for item in sources)
        return f"A4 {details}"


@dataclass(frozen=True)
class DeviceJob:
    document_name: str
    state: DeviceJobState = DeviceJobState.UNKNOWN
    receipt_no: str = ""
    result: str = ""
    end_code: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    source_pages: int | None = None
    output_pages: int | None = None
    sets: int | None = None
    copies: int | None = None
    dispatch_token: str = ""


@dataclass(frozen=True)
class DeviceSnapshot:
    observed_at: datetime
    health: DeviceHealth = DeviceHealth.UNKNOWN
    status_text: str = "상태 확인 중"
    errors: tuple[str, ...] = ()
    consumables: DeviceConsumables = field(default_factory=DeviceConsumables)
    active_jobs: tuple[DeviceJob, ...] = ()
    recent_jobs: tuple[DeviceJob, ...] = ()
    device_key: str = ""
    authenticated: bool = False
    source_url: str = ""
    stale: bool = False
    consecutive_stale_reads: int = 0

    @property
    def connectivity_only(self) -> bool:
        return (
            self.stale
            and self.health == DeviceHealth.OFFLINE
            and not any(job.state in {DeviceJobState.HELD, DeviceJobState.FAILED}
                        for job in (*self.active_jobs, *self.recent_jobs))
        )

    @classmethod
    def unavailable(
        cls,
        message: str,
        *,
        source_url: str = "",
        observed_at: datetime | None = None,
    ) -> "DeviceSnapshot":
        return cls(
            observed_at=observed_at or datetime.now().astimezone(),
            health=DeviceHealth.OFFLINE,
            status_text=message,
            errors=(message,),
            source_url=source_url,
            stale=True,
        )

    def find_job(self, dispatch_token: str) -> DeviceJob | None:
        wanted = dispatch_token.casefold()
        for job in (*self.active_jobs, *self.recent_jobs):
            if job.dispatch_token.casefold() == wanted:
                return job
            if wanted and wanted in job.document_name.casefold():
                return job
        return None

    @property
    def can_dispatch(self) -> bool:
        return (
            self.authenticated
            and not self.stale
            and self.health in {DeviceHealth.READY, DeviceHealth.BUSY}
            and not self.errors
        )


def merge_error_texts(values: Iterable[str]) -> tuple[str, ...]:
    """Return unique, non-empty messages while preserving their source order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value).split())
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return tuple(result)
