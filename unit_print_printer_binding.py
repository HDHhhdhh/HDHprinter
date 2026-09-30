"""Read-only proof that Windows print destinations match the monitored device.

Port names, queue labels and driver names are not device identities.  Only the
host configured on a local Standard TCP/IP port is used as evidence.  Inventory
reading and optional bounded DNS lookup are separate from the pure validator.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import re
import socket
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping
from urllib.parse import unquote, urlsplit


class PrinterBindingReadError(RuntimeError):
    """Windows did not return a complete, parseable binding inventory."""


@dataclass(frozen=True, slots=True)
class PrinterQueueBinding:
    name: str
    port_name: str
    host_address: str
    port_monitor: str
    printer_type: str = "Local"


@dataclass(frozen=True, slots=True)
class PrinterBindingInventory:
    queues: tuple[PrinterQueueBinding, ...]


@dataclass(frozen=True, slots=True)
class PrinterBindingValidation:
    target_host: str
    checked_names: tuple[str, ...]
    matched_names: tuple[str, ...] = ()
    missing_names: tuple[str, ...] = ()
    different_names: tuple[str, ...] = ()
    unverifiable_names: tuple[str, ...] = ()
    target_error: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.checked_names) and not (
            self.target_error or self.missing_names or self.different_names or self.unverifiable_names
        )

    @property
    def message(self) -> str:
        if self.target_error:
            return self.target_error
        if not self.checked_names:
            return "검증할 프린터가 없습니다."
        if self.is_valid:
            return f"프린터 연결 확인됨 · {self.target_host} · {len(self.matched_names)}개 큐"
        details: list[str] = []
        if self.missing_names:
            details.append("등록되지 않은 프린터: " + ", ".join(self.missing_names))
        if self.different_names:
            details.append("다른 장비로 연결된 프린터: " + ", ".join(self.different_names))
        if self.unverifiable_names:
            details.append("장비 주소를 확인할 수 없는 프린터: " + ", ".join(self.unverifiable_names))
        return f"상태 확인 장비 {self.target_host}와 인쇄 경로를 확인해 주세요.\n" + "\n".join(details)


# No names or operator values are interpolated into this script.  Both cmdlets
# run once and return only the fields needed for physical-destination checks.
PRINTER_BINDING_INVENTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$printers = @(Get-Printer -ErrorAction Stop)
$ports = @(Get-PrinterPort -ErrorAction Stop)
$portsByName = @{}
foreach ($port in $ports) { $portsByName[[string]$port.Name] = $port }
$bindings = @(
    foreach ($printer in $printers) {
        $port = $portsByName[[string]$printer.PortName]
        [ordered]@{
            name = [string]$printer.Name
            portName = [string]$printer.PortName
            printerHostAddress = [string]$port.PrinterHostAddress
            portMonitor = [string]$port.PortMonitor
            printerType = [string]$printer.Type
        }
    }
)
[ordered]@{ queues = $bindings } | ConvertTo-Json -Depth 4 -Compress
""".strip()


def parse_printer_binding_inventory(raw: str | bytes) -> PrinterBindingInventory:
    """Reject partial/malformed output instead of manufacturing valid bindings."""
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")
        payload = json.loads(text)
        rows = payload.get("queues") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("queues must be an array")
        bindings: list[PrinterQueueBinding] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("queue must be an object")
            values = []
            for key in ("name", "portName", "printerHostAddress", "portMonitor", "printerType"):
                value = row.get(key)
                if value is None and key != "name":
                    value = ""
                if not isinstance(value, str):
                    raise ValueError("queue fields must be text")
                values.append(value.strip())
            name, port_name, host_address, monitor, printer_type = values
            if not name or name.casefold() in seen:
                raise ValueError("queue names must be nonempty and unique")
            seen.add(name.casefold())
            bindings.append(PrinterQueueBinding(name, port_name, host_address, monitor, printer_type))
        return PrinterBindingInventory(tuple(bindings))
    except (ValueError, TypeError, AttributeError, UnicodeError) as error:
        raise PrinterBindingReadError("프린터 연결 정보를 읽을 수 없습니다. Windows 프린터 설정을 확인해 주세요.") from error


def read_printer_binding_inventory(*, timeout: float = 10.0) -> PrinterBindingInventory:
    """One bounded local, noninteractive PowerShell read; no driver/UI mutation."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", PRINTER_BINDING_INVENTORY_SCRIPT],
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PrinterBindingReadError("프린터 연결 조회에 실패했습니다. 잠시 후 다시 시도해 주세요.") from error
    if result.returncode != 0:
        # Never copy arbitrary subprocess output into logs or operator dialogs.
        raise PrinterBindingReadError("Windows가 프린터 연결 정보를 반환하지 않았습니다.")
    return parse_printer_binding_inventory(result.stdout)


def normalize_printer_host(value: str) -> str:
    """Canonical IP/hostname from a host or HTTP(S) URI, excluding credentials."""
    raw = value.strip()
    if not raw:
        raise ValueError("empty printer host")
    candidate = raw
    try:
        # A bare IPv6 address must not first be parsed as an unbracketed URI.
        return _canonical_ip(candidate)
    except ValueError:
        pass
    if "://" in raw:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("unsupported printer URI")
        _ = parsed.port
        candidate = parsed.hostname
    elif raw.startswith("[") or (":" in raw and raw.count(":") == 1):
        parsed = urlsplit("//" + raw)
        if not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("invalid printer host")
        # Accessing port validates malformed/out-of-range numeric ports.
        _ = parsed.port
        candidate = parsed.hostname
    elif any(char in raw for char in "/\\@?#"):
        raise ValueError("invalid printer host")
    candidate = unquote(candidate)
    try:
        return _canonical_ip(candidate)
    except ValueError:
        pass
    candidate = candidate.rstrip(".").encode("idna").decode("ascii").casefold()
    if len(candidate) > 253 or not candidate:
        raise ValueError("invalid printer hostname")
    labels = candidate.split(".")
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("invalid printer hostname")
    # A mistyped all-numeric dotted address is not a hostname identity.
    if len(labels) > 1 and all(label.isdigit() for label in labels):
        raise ValueError("invalid printer IP")
    return candidate


def _canonical_ip(value: str) -> str:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)


def _trusted_queue_host(binding: PrinterQueueBinding) -> str | None:
    port = binding.port_name.casefold()
    if (
        binding.printer_type.casefold() != "local"
        or binding.name.startswith("\\\\")
        or not port
        or port.startswith(("wsd", "usb", "\\\\"))
        or "," in port  # printer pooling cannot be attributed to one device
        or binding.port_monitor.casefold() not in {"tcpmon.dll", "standard tcp/ip port"}
    ):
        return None
    try:
        return normalize_printer_host(binding.host_address)
    except (ValueError, UnicodeError):
        return None


def _single_address(host: str, resolved_hosts: Mapping[str, Iterable[str]]) -> str | None:
    try:
        return _canonical_ip(host)
    except ValueError:
        pass
    addresses: set[str] = set()
    for value in resolved_hosts.get(host, ()):
        try:
            addresses.add(_canonical_ip(value))
        except ValueError:
            return None
    return next(iter(addresses)) if len(addresses) == 1 else None


def validate_printer_binding(
    inventory: PrinterBindingInventory,
    queue_names: Iterable[str],
    monitor_base_url: str,
    *,
    resolved_hosts: Mapping[str, Iterable[str]] | None = None,
) -> PrinterBindingValidation:
    """Validate every effective destination; unsupported connections stay unknown."""
    requested_names = tuple(name.strip() for name in queue_names)
    names = tuple(dict.fromkeys(requested_names))
    if any(not name for name in names):
        return PrinterBindingValidation("", names, target_error="인쇄 경로에 프린터 이름이 없는 작업이 있습니다.")
    try:
        target = normalize_printer_host(monitor_base_url)
    except (ValueError, UnicodeError):
        return PrinterBindingValidation("", names, target_error="프린터 상태 확인 주소가 올바르지 않습니다.")
    by_name = {binding.name.casefold(): binding for binding in inventory.queues}
    matches: list[str] = []
    missing: list[str] = []
    different: list[str] = []
    unknown: list[str] = []
    resolutions = resolved_hosts or {}
    target_address = _single_address(target, resolutions)
    for name in names:
        binding = by_name.get(name.casefold())
        if binding is None:
            missing.append(name)
            continue
        host = _trusted_queue_host(binding)
        if host is None:
            unknown.append(name)
        elif host == target:
            matches.append(name)
        else:
            address = _single_address(host, resolutions)
            if address is None or target_address is None:
                unknown.append(name)
            elif address == target_address:
                matches.append(name)
            else:
                different.append(name)
    return PrinterBindingValidation(target, names, tuple(matches), tuple(missing), tuple(different), tuple(unknown))


def resolve_printer_binding_hosts(
    inventory: PrinterBindingInventory,
    queue_names: Iterable[str],
    monitor_base_url: str,
    *,
    timeout: float = 3.0,
    resolver: Callable[..., list[tuple]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Optional hostname-alias lookup with a bounded caller wait and no login.

    A slow OS DNS call cannot hold up dispatch indefinitely.  The daemon worker
    publishes no state after return; the caller owns this run's immutable result.
    Unresolved and multi-address hosts never establish alias identity.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    try:
        target = normalize_printer_host(monitor_base_url)
    except (ValueError, UnicodeError):
        return {}
    names = {name.casefold() for name in queue_names}
    hosts = {target}
    hosts.update(host for binding in inventory.queues if binding.name.casefold() in names
                 if (host := _trusted_queue_host(binding)) is not None)
    to_resolve: list[str] = []
    for host in sorted(hosts):
        try:
            _canonical_ip(host)
        except ValueError:
            to_resolve.append(host)
    if not to_resolve:
        return {}
    results: queue.Queue[dict[str, tuple[str, ...]]] = queue.Queue(maxsize=1)
    stop = threading.Event()
    lookup = resolver or socket.getaddrinfo

    def resolve() -> None:
        addresses: dict[str, tuple[str, ...]] = {}
        for host in to_resolve:
            if stop.is_set():
                return
            try:
                rows = lookup(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                addresses[host] = tuple(sorted({_canonical_ip(str(row[4][0])) for row in rows}))
            except (OSError, ValueError, IndexError, TypeError):
                continue
        if not stop.is_set():
            results.put(addresses)

    threading.Thread(target=resolve, name="printer-binding-dns", daemon=True).start()
    try:
        return results.get(timeout=timeout)
    except queue.Empty:
        stop.set()
        return {}
