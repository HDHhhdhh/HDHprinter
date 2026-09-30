"""Pure presentation helpers for printer state shown by Tkinter."""

from __future__ import annotations

from dataclasses import dataclass

from unit_print_device_state import DeviceHealth, DeviceSnapshot


@dataclass(frozen=True)
class DeviceStatusView:
    headline: str
    detail: str
    a4_text: str
    tone: str


def build_device_status_view(snapshot: DeviceSnapshot) -> DeviceStatusView:
    tone = {
        DeviceHealth.READY: "success",
        DeviceHealth.BUSY: "primary",
        DeviceHealth.HELD: "warning",
        DeviceHealth.ERROR: "danger",
        DeviceHealth.OFFLINE: "danger",
        DeviceHealth.UNKNOWN: "secondary",
    }[snapshot.health]
    if snapshot.errors:
        detail = snapshot.errors[0]
    else:
        detail = snapshot.status_text
    headline = snapshot.status_text
    if not headline.startswith("프린터"):
        headline = f"프린터 {headline}"
    return DeviceStatusView(
        headline=headline,
        detail=detail,
        a4_text=snapshot.consumables.a4_level_text,
        tone=tone,
    )
