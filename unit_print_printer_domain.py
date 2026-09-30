from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, override


class PrintRouteKind(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SEPARATOR = "separator"


class PrintTicketCapability(StrEnum):
    A4 = "psk:PageMediaSize/ISOA4"
    DUPLEX_LONG = "psk:DocumentDuplex/TwoSidedLongEdge"
    DUPLEX_SHORT = "psk:DocumentDuplex/TwoSidedShortEdge"
    MONOCHROME = "psk:PageOutputColor/Monochrome"
    PORTRAIT = "psk:PageOrientation/Portrait"
    LANDSCAPE = "psk:PageOrientation/Landscape"
    STAPLE_TOP_LEFT = "psk:DocumentStaple/StapleTopLeft"
    STAPLE_TOP_RIGHT = "psk:DocumentStaple/StapleTopRight"
    STAPLE_BOTTOM_LEFT = "psk:DocumentStaple/StapleBottomLeft"


class SetupBlockReason(StrEnum):
    DISCOVERY_FAILED = "discovery_failed"
    PERMISSION_DENIED = "permission_denied"
    MALFORMED_BASE_QUEUE = "malformed_base_queue"
    STALE_BASE_QUEUE = "stale_base_queue"
    NON_CANON_DRIVER = "non_canon_driver"
    UNSUPPORTED_AUTH_PORT = "unsupported_auth_port"
    MISSING_CAPABILITY = "missing_capability"
    MALFORMED_PROFILE = "malformed_profile"


class PrintOrientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class DuplexBinding(StrEnum):
    LONG_EDGE = "long_edge"
    SHORT_EDGE = "short_edge"


REQUIRED_CAPABILITIES: Final = frozenset(PrintTicketCapability)


@dataclass(frozen=True, slots=True)
class EmptyBaseQueueNameError(ValueError):
    raw_queue: str

    @override
    def __str__(self) -> str:
        return "A base printer queue name is required."


@dataclass(frozen=True, slots=True)
class PrinterProfileNames:
    base_queue: str
    portrait_queue: str
    landscape_queue: str
    separator_queue: str

    @property
    def able_queue(self) -> str:
        return f"{self.base_queue} -A:ble"

    @property
    def profile_queues(self) -> tuple[str, str, str, str]:
        return self.portrait_queue, self.landscape_queue, self.separator_queue, self.able_queue


@dataclass(frozen=True, slots=True)
class PrintRoute:
    kind: PrintRouteKind
    queue_name: str


@dataclass(frozen=True, slots=True)
class PrintTicketDefaults:
    orientation: PrintOrientation
    duplex_binding: DuplexBinding
    staple_top_left: bool


def derive_profile_names(base_queue: str) -> PrinterProfileNames:
    normalized = base_queue.strip()
    if not normalized:
        raise EmptyBaseQueueNameError(base_queue)
    return PrinterProfileNames(normalized, f"{normalized} - 세로", f"{normalized} - 가로", f"{normalized} - 구분지")


def route_print_job(profiles: PrinterProfileNames, kind: PrintRouteKind) -> PrintRoute:
    if kind is PrintRouteKind.PORTRAIT:
        return PrintRoute(kind, profiles.portrait_queue)
    if kind is PrintRouteKind.LANDSCAPE:
        return PrintRoute(kind, profiles.landscape_queue)
    if kind is PrintRouteKind.SEPARATOR:
        return PrintRoute(kind, profiles.separator_queue)


def ticket_defaults_for(kind: PrintRouteKind) -> PrintTicketDefaults:
    if kind is PrintRouteKind.PORTRAIT:
        return PrintTicketDefaults(PrintOrientation.PORTRAIT, DuplexBinding.LONG_EDGE, True)
    if kind is PrintRouteKind.LANDSCAPE:
        return PrintTicketDefaults(PrintOrientation.LANDSCAPE, DuplexBinding.SHORT_EDGE, True)
    if kind is PrintRouteKind.SEPARATOR:
        return PrintTicketDefaults(PrintOrientation.LANDSCAPE, DuplexBinding.SHORT_EDGE, False)


def route_demo_json(base_queue: str) -> str:
    profiles = derive_profile_names(base_queue)
    routes = {kind.value: route_print_job(profiles, kind).queue_name for kind in PrintRouteKind}
    payload = {"base_queue": profiles.base_queue, "profile_queues": profiles.profile_queues, "routes": routes}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:]) if argv is None else argv
    base_queue = arguments[0] if arguments else "iR-ADV 8585 III"
    print(route_demo_json(base_queue))
    return 0
