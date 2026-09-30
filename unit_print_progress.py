"""Pure print-run display state shared by Tk status and the Windows taskbar."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class RunPhase(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    HELD = "held"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PrintProgressState:
    run_id: str | None = None
    phase: RunPhase = RunPhase.IDLE
    completed: int = 0
    total: int = 1
    hold_reason: str = ""
    completion_pending: bool = False
    completion_basis: str = "none"

    def with_phase(
        self,
        phase: RunPhase,
        *,
        hold_reason: str = "",
        completion_basis: str = "none",
    ) -> "PrintProgressState":
        if phase is RunPhase.IDLE:
            return PrintProgressState()
        return replace(
            self,
            phase=phase,
            hold_reason=hold_reason if phase is RunPhase.HELD else "",
            completion_pending=phase is RunPhase.COMPLETE,
            completion_basis=(completion_basis if phase is RunPhase.COMPLETE else "none"),
        )

    def begin(self, run_id: str | None, total: int = 1) -> "PrintProgressState":
        return PrintProgressState(
            run_id=run_id,
            phase=RunPhase.PREPARING,
            completed=0,
            total=max(1, int(total)),
        )

    def with_progress(self, completed: int, total: int) -> "PrintProgressState":
        """Update counters without clearing a terminal or operator-held phase."""

        return replace(
            self,
            completed=max(0, int(completed)),
            total=max(1, int(total)),
        )

    @property
    def taskbar_kind(self) -> str:
        if self.phase is RunPhase.IDLE:
            return "idle"
        if self.phase in {RunPhase.HELD, RunPhase.STOPPING, RunPhase.STOPPED}:
            return "paused"
        if self.phase is RunPhase.ERROR:
            return "error"
        if self.phase is RunPhase.COMPLETE:
            return "complete"
        return "progress" if self.completed > 0 else "indeterminate"


@dataclass(frozen=True, slots=True)
class PersistentAlertSpec:
    """One operator acknowledgement alert derived from a terminal/held status."""

    key: str
    title: str
    message: str
    tone: str
    allow_resume: bool = False


def persistent_alert_for_status(
    status: str,
    *,
    device_detail: str = "",
) -> PersistentAlertSpec | None:
    """Return the durable alert required for a visible app status."""

    if status == "프린터 오류 / 인쇄 일시 중지":
        detail = device_detail.strip() or "프린터 상태에서 중지 원인을 확인해 주세요."
        return PersistentAlertSpec(
            key="printer-held",
            title="프린터 인쇄 중지",
            message=(
                f"{detail}\n"
                "원인을 해결한 뒤 이어서 진행하세요. 해결 전에는 다음 문서를 전송하지 않습니다."
            ),
            tone="warning",
            allow_resume=True,
        )
    if status in {
        "전송 중지 요청됨",
        "전송 중지됨",
        "완료 확인 중지됨",
        "전송 완료 / 완료 확인 중지",
    }:
        if status == "전송 중지 요청됨":
            message = (
                "남은 문서 전송을 중지하고 있습니다. "
                "이미 프린터로 전송된 문서는 계속 인쇄됩니다."
            )
        elif status in {"완료 확인 중지됨", "전송 완료 / 완료 확인 중지"}:
            message = (
                "새 문서 전송은 끝났지만 실제 인쇄 완료 확인이 중지되었습니다. "
                "프린터 상태를 확인한 뒤 남은 작업을 다시 시작하세요."
            )
        else:
            message = (
                "남은 문서 전송이 중지되었습니다. 이미 전송된 문서는 계속 인쇄되며, "
                "[인쇄 시작]을 누르면 남은 작업부터 이어집니다."
            )
        return PersistentAlertSpec(
            key="transfer-stopped",
            title="인쇄 전송이 중지되었습니다",
            message=message,
            tone="warning",
        )
    if status == "오류 발생":
        return PersistentAlertSpec(
            key="run-error",
            title="빠른 인쇄 오류",
            message=(
                "인쇄 작업이 중단되었습니다. 현재 작업과 로그에서 원인을 확인하세요."
            ),
            tone="danger",
        )
    if status == "실제 인쇄 완료":
        return PersistentAlertSpec(
            key="print-complete",
            title="실제 인쇄 완료",
            message="모든 문서가 프린터에서 출력되었습니다.",
            tone="success",
        )
    return None


PHASE_BY_TASKBAR_KIND = {
    "idle": RunPhase.IDLE,
    "running": RunPhase.RUNNING,
    "indeterminate": RunPhase.RUNNING,
    "paused": RunPhase.HELD,
    "error": RunPhase.ERROR,
    "complete": RunPhase.COMPLETE,
}


def transition_taskbar_state(
    state: PrintProgressState,
    kind: str,
    completed: int = 0,
    total: int = 1,
) -> PrintProgressState:
    if kind == "progress":
        return state.with_progress(completed, total)
    phase = PHASE_BY_TASKBAR_KIND.get(kind)
    if phase is None:
        raise ValueError(f"Unknown taskbar state: {kind}")
    if kind == "indeterminate" and state.phase in {
        RunPhase.HELD,
        RunPhase.STOPPING,
        RunPhase.STOPPED,
        RunPhase.ERROR,
        RunPhase.COMPLETE,
    }:
        return state
    next_state = state.with_phase(phase)
    if phase is RunPhase.RUNNING and (completed or total != 1):
        next_state = next_state.with_progress(completed, total)
    return next_state
