from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, TypeAlias, cast

from unit_print_printer_domain import (
    DuplexBinding,
    PrintRouteKind,
    PrintTicketCapability,
    PrinterProfileNames,
    REQUIRED_CAPABILITIES,
    SetupBlockReason,
    ticket_defaults_for,
)


@dataclass(frozen=True, slots=True)
class PowerShellResult:
    return_code: int
    stdout: str
    stderr: str


class PowerShellRunner(Protocol):
    def run(self, script: str) -> PowerShellResult: ...


@dataclass(frozen=True, slots=True)
class BaseQueueInspection:
    queue_name: str
    driver_name: str
    port_name: str
    capabilities: frozenset[PrintTicketCapability]
    can_manage_queues: bool


@dataclass(frozen=True, slots=True)
class DiscoveryFailure:
    reason: SetupBlockReason
    detail: str


BaseQueueDiscovery: TypeAlias = BaseQueueInspection | DiscoveryFailure


@dataclass(frozen=True, slots=True)
class QueueSetupCommand:
    queue_name: str
    mutation_target: str
    script: str


@dataclass(frozen=True, slots=True)
class ProfileSetupRollback:
    created_queue_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileSetupPlan:
    profiles: PrinterProfileNames
    commands: tuple[QueueSetupCommand, ...]
    rollback: ProfileSetupRollback


@dataclass(frozen=True, slots=True)
class ProfileSetupBlocked:
    reason: SetupBlockReason
    detail: str
    commands: tuple[QueueSetupCommand, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileSetupExecution:
    created_queue_names: tuple[str, ...]
    failed_queue_name: str | None
    detail: str

    @property
    def is_complete(self) -> bool:
        return self.failed_queue_name is None


ProfileSetupOutcome: TypeAlias = ProfileSetupPlan | ProfileSetupBlocked | ProfileSetupExecution


def base_queue_discovery_command(base_queue: str) -> str:
    queue_name = _powershell_literal(base_queue)
    return f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Printing
Add-Type -AssemblyName ReachFramework
$printer = Get-Printer -Name {queue_name} -ErrorAction Stop
$configuration = Get-PrintConfiguration -PrinterName {queue_name} -ErrorAction Stop
$capabilitiesXml = $configuration.PrintCapabilitiesXML
$candidates = @(
    @{{ value = 'psk:PageMediaSize/ISOA4'; token = 'ISOA4' }},
    @{{ value = 'psk:DocumentDuplex/TwoSidedLongEdge'; token = 'TwoSidedLongEdge' }},
    @{{ value = 'psk:DocumentDuplex/TwoSidedShortEdge'; token = 'TwoSidedShortEdge' }},
    @{{ value = 'psk:PageOutputColor/Monochrome'; token = 'Monochrome' }},
    @{{ value = 'psk:PageOrientation/Portrait'; token = 'Portrait' }},
    @{{ value = 'psk:PageOrientation/Landscape'; token = 'Landscape' }},
    @{{ value = 'psk:DocumentStaple/StapleTopLeft'; token = 'StapleTopLeft' }},
    @{{ value = 'psk:DocumentStaple/StapleTopRight'; token = 'StapleTopRight' }},
    @{{ value = 'psk:DocumentStaple/StapleBottomLeft'; token = 'StapleBottomLeft' }}
)
$capabilities = @($candidates | Where-Object {{ $capabilitiesXml -match [regex]::Escape($_.token) }} | ForEach-Object {{ $_.value }})
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$payload = [ordered]@{{
    status = 'ready'
    name = $printer.Name
    driverName = $printer.DriverName
    portName = $printer.PortName
    canManageQueues = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    capabilities = $capabilities
}}
$payload | ConvertTo-Json -Compress
""".strip()


def inspect_base_queue(base_queue: str, runner: PowerShellRunner) -> BaseQueueDiscovery:
    result = runner.run(base_queue_discovery_command(base_queue))
    if result.return_code != 0:
        return DiscoveryFailure(SetupBlockReason.DISCOVERY_FAILED, _failure_detail(result))
    try:
        payload = _read_json_payload(result.stdout)
        if payload.get("status") != "ready":
            raise ValueError("Printer inspection did not report ready status.")
        queue_name = _required_text(payload, "name")
        driver_name = _required_text(payload, "driverName")
        port_name = _required_text(payload, "portName")
        can_manage_queues = payload.get("canManageQueues")
        if not isinstance(can_manage_queues, bool):
            raise ValueError("Printer inspection did not report queue-management access.")
        raw_capabilities = payload.get("capabilities")
        if not isinstance(raw_capabilities, list):
            raise ValueError("Printer inspection did not report print capabilities.")
        capability_values: list[PrintTicketCapability] = []
        for raw_capability in cast(list[object], raw_capabilities):
            if not isinstance(raw_capability, str):
                raise ValueError("Printer inspection reported a non-text capability.")
            capability_values.append(PrintTicketCapability(raw_capability))
        capabilities = frozenset(capability_values)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return DiscoveryFailure(SetupBlockReason.MALFORMED_BASE_QUEUE, str(error))
    return BaseQueueInspection(queue_name, driver_name, port_name, capabilities, can_manage_queues)


def plan_profile_setup(
    profiles: PrinterProfileNames,
    discovery: BaseQueueDiscovery,
    existing_queue_names: frozenset[str],
) -> ProfileSetupPlan | ProfileSetupBlocked:
    if isinstance(discovery, DiscoveryFailure):
        return ProfileSetupBlocked(discovery.reason, discovery.detail)
    if discovery.queue_name != profiles.base_queue:
        return ProfileSetupBlocked(
            SetupBlockReason.STALE_BASE_QUEUE,
            f"Selected base queue is '{profiles.base_queue}', but inspection returned '{discovery.queue_name}'.",
        )
    if not discovery.driver_name.strip() or not discovery.port_name.strip():
        return ProfileSetupBlocked(SetupBlockReason.MALFORMED_BASE_QUEUE, "Base queue has no driver or port.")
    if "canon" not in discovery.driver_name.casefold():
        return ProfileSetupBlocked(SetupBlockReason.NON_CANON_DRIVER, "Base queue does not use a Canon driver.")
    normalized_port = discovery.port_name.strip().casefold()
    if normalized_port.startswith(("wsd", "usb")):
        return ProfileSetupBlocked(
            SetupBlockReason.UNSUPPORTED_AUTH_PORT,
            (
                f"Base queue uses '{discovery.port_name}'. Canon Department ID/PIN verification is unavailable "
                "on USB/WSD ports. Reconnect the Canon queue with a Standard TCP/IP port, then run setup again."
            ),
        )
    if not discovery.can_manage_queues:
        return ProfileSetupBlocked(
            SetupBlockReason.PERMISSION_DENIED,
            "Run the app as an administrator to create dedicated printer queues.",
        )
    missing_capabilities = REQUIRED_CAPABILITIES - discovery.capabilities
    if missing_capabilities:
        missing_text = ", ".join(sorted(capability.value for capability in missing_capabilities))
        return ProfileSetupBlocked(SetupBlockReason.MISSING_CAPABILITY, missing_text)

    commands = tuple(
        _queue_setup_command(queue_name, kind, discovery)
        for queue_name, kind in _profile_queue_kinds(profiles)
        if queue_name not in existing_queue_names
    )
    return ProfileSetupPlan(
        profiles=profiles,
        commands=commands,
        rollback=ProfileSetupRollback(tuple(command.queue_name for command in commands)),
    )


def execute_profile_setup(plan: ProfileSetupPlan, runner: PowerShellRunner) -> ProfileSetupExecution:
    created_queue_names: list[str] = []
    for command in plan.commands:
        result = runner.run(command.script)
        if result.return_code != 0:
            return ProfileSetupExecution(tuple(created_queue_names), command.queue_name, _failure_detail(result))
        try:
            payload = _read_json_payload(result.stdout)
            if payload.get("status") != "created":
                raise ValueError("Printer profile creation was not confirmed by the print service.")
            response_name = payload.get("name")
            if response_name is not None and response_name != command.queue_name:
                raise ValueError("Printer profile creation confirmed a different queue name.")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return ProfileSetupExecution(tuple(created_queue_names), command.queue_name, str(error))
        created_queue_names.append(command.queue_name)
    return ProfileSetupExecution(tuple(created_queue_names), None, "Dedicated printer profiles are ready.")


def _profile_queue_kinds(profiles: PrinterProfileNames) -> tuple[tuple[str, PrintRouteKind], ...]:
    return (
        (profiles.portrait_queue, PrintRouteKind.PORTRAIT),
        (profiles.landscape_queue, PrintRouteKind.LANDSCAPE),
        (profiles.separator_queue, PrintRouteKind.SEPARATOR),
        (profiles.able_queue, PrintRouteKind.LANDSCAPE),
    )


def _queue_setup_command(
    queue_name: str,
    kind: PrintRouteKind,
    discovery: BaseQueueInspection,
) -> QueueSetupCommand:
    defaults = ticket_defaults_for(kind)
    queue_literal = _powershell_literal(queue_name)
    driver_literal = _powershell_literal(discovery.driver_name)
    port_literal = _powershell_literal(discovery.port_name)
    orientation = "Portrait" if defaults.orientation.value == "portrait" else "Landscape"
    duplex = (
        "TwoSidedLongEdge"
        if defaults.duplex_binding is DuplexBinding.LONG_EDGE
        else "TwoSidedShortEdge"
    )
    staple = "StapleTopLeft" if defaults.staple_top_left else "None"
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Printing
Add-Type -AssemblyName ReachFramework
$queueName = {queue_literal}
if (Get-Printer -Name $queueName -ErrorAction SilentlyContinue) {{
    throw "Refusing to overwrite existing profile queue: $queueName"
}}
$logicalOrientation = '{orientation}'
$logicalDuplex = '{duplex}'
$logicalStaple = '{staple}'
$created = $false
try {{
    Add-Printer -Name $queueName -DriverName {driver_literal} -PortName {port_literal} -ErrorAction Stop
    $created = $true

    $configuration = Get-PrintConfiguration -PrinterName $queueName -ErrorAction Stop
    [xml]$capabilities = $configuration.PrintCapabilitiesXML
    $capabilityNs = New-Object System.Xml.XmlNamespaceManager($capabilities.NameTable)
    $capabilityNs.AddNamespace('psf', 'http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework')
    $portraitLabel = $capabilities.SelectSingleNode(
        "//psf:Feature[@name='psk:PageOrientation']/psf:Option[@name='psk:Portrait']/psf:Property[@name='psk:DisplayName']/psf:Value",
        $capabilityNs
    ).InnerText
    $landscapeLabel = $capabilities.SelectSingleNode(
        "//psf:Feature[@name='psk:PageOrientation']/psf:Option[@name='psk:Landscape']/psf:Property[@name='psk:DisplayName']/psf:Value",
        $capabilityNs
    ).InnerText
    $actualOrientation = $logicalOrientation
    $actualStaple = $logicalStaple
    if ($portraitLabel -match '가로' -and $landscapeLabel -match '세로') {{
        $actualOrientation = if ($logicalOrientation -eq 'Portrait') {{ 'Landscape' }} else {{ 'Portrait' }}
        if ($logicalStaple -eq 'StapleTopLeft') {{
            # Canon rotates the logical page but does not rotate finishing coordinates with it.
            $actualStaple = if ($logicalOrientation -eq 'Portrait') {{ 'StapleTopRight' }} else {{ 'StapleBottomLeft' }}
        }}
    }}

    [xml]$ticket = $configuration.PrintTicketXML
    $ticketNs = New-Object System.Xml.XmlNamespaceManager($ticket.NameTable)
    $ticketNs.AddNamespace('psf', 'http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework')
    $ticketOptions = @{{
        'psk:PageMediaSize' = 'psk:ISOA4'
        'psk:DocumentDuplex' = "psk:$logicalDuplex"
        'psk:PageOutputColor' = 'psk:Monochrome'
        'psk:PageOrientation' = "psk:$actualOrientation"
        'psk:DocumentStaple' = "psk:$actualStaple"
    }}
    foreach ($featureName in $ticketOptions.Keys) {{
        $option = $ticket.SelectSingleNode("//psf:Feature[@name='$featureName']/psf:Option", $ticketNs)
        if (-not $option) {{
            throw "PrintTicket feature missing: $featureName ($queueName)"
        }}
        $option.SetAttribute('name', $ticketOptions[$featureName])
    }}
    Set-PrintConfiguration -PrinterName $queueName -PrintTicketXml $ticket.OuterXml -ErrorAction Stop
    Set-PrintConfiguration -PrinterName $queueName -PaperSize A4 -Color $false -DuplexingMode $logicalDuplex -ErrorAction Stop

    # Canon keeps a per-user DEVMODE that can override the queue default used by SumatraPDF/UPDF.
    # Store the logical, user-facing values there as well as the compensated system PrintTicket above.
    $printServer = New-Object System.Printing.LocalPrintServer
    $profileQueue = New-Object System.Printing.PrintQueue($printServer, $queueName)
    $profileQueue.Refresh()
    $currentUserTicket = $profileQueue.UserPrintTicket
    if (-not $currentUserTicket) {{
        $currentUserTicket = $profileQueue.DefaultPrintTicket.Clone()
    }}
    $currentUserTicketStream = $currentUserTicket.GetXmlStream()
    $ticketReader = $null
    try {{
        $ticketReader = New-Object IO.StreamReader($currentUserTicketStream)
        [xml]$userTicket = $ticketReader.ReadToEnd()
    }} finally {{
        if ($ticketReader) {{
            $ticketReader.Dispose()
        }} else {{
            $currentUserTicketStream.Dispose()
        }}
    }}
    $userTicketNs = New-Object System.Xml.XmlNamespaceManager($userTicket.NameTable)
    $userTicketNs.AddNamespace('psf', 'http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework')
    $userTicketOptions = @{{
        'psk:PageMediaSize' = 'psk:ISOA4'
        'psk:DocumentDuplex' = "psk:$logicalDuplex"
        'psk:PageOutputColor' = 'psk:Monochrome'
        'psk:PageOrientation' = "psk:$logicalOrientation"
        'psk:DocumentStaple' = "psk:$logicalStaple"
    }}
    foreach ($featureName in $userTicketOptions.Keys) {{
        $option = $userTicket.SelectSingleNode("//psf:Feature[@name='$featureName']/psf:Option", $userTicketNs)
        if (-not $option) {{
            throw "User PrintTicket feature missing: $featureName ($queueName)"
        }}
        $option.SetAttribute('name', $userTicketOptions[$featureName])
    }}
    $userTicketBytes = [Text.Encoding]::UTF8.GetBytes($userTicket.OuterXml)
    $userTicketInput = New-Object IO.MemoryStream(,$userTicketBytes)
    try {{
        $profileQueue.UserPrintTicket = [System.Printing.PrintTicket]::new($userTicketInput)
        $profileQueue.Commit()
    }} finally {{
        $userTicketInput.Dispose()
        $profileQueue.Dispose()
        $printServer.Dispose()
    }}
    [ordered]@{{ status = 'created'; name = $queueName }} | ConvertTo-Json -Compress
}} catch {{
    if ($created) {{
        Remove-Printer -Name $queueName -ErrorAction SilentlyContinue
    }}
    throw
}}
""".strip()
    return QueueSetupCommand(queue_name, f"profile:{kind.value}", script)


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Printer inspection did not report {key}.")
    return value


def _read_json_payload(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if candidate:
            payload = cast(object, json.loads(candidate))
            if isinstance(payload, dict):
                typed_payload: dict[str, object] = {}
                for key, value in cast(dict[object, object], payload).items():
                    if isinstance(key, str):
                        typed_payload[key] = value
                return typed_payload
            raise ValueError("Printer command did not return an object.")
    raise ValueError("Printer command did not return JSON.")


def _failure_detail(result: PowerShellResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip()
    return detail or f"PowerShell exited with code {result.return_code}."
