from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import Mock

from unit_print_printer_profiles import (
    BaseQueueInspection,
    DiscoveryFailure,
    DuplexBinding,
    PowerShellResult,
    PrintRouteKind,
    PrintTicketCapability,
    ProfileSetupBlocked,
    ProfileSetupPlan,
    SetupBlockReason,
    derive_profile_names,
    inspect_base_queue,
    plan_profile_setup,
    route_print_job,
    ticket_defaults_for,
)

DEFAULT_PRINTER = "iR-ADV 8585 III"


def required_capabilities() -> frozenset[PrintTicketCapability]:
    return frozenset(PrintTicketCapability)


def ready_base_inspection(base_queue: str = DEFAULT_PRINTER) -> BaseQueueInspection:
    return BaseQueueInspection(
        queue_name=base_queue,
        driver_name="Canon Generic Plus UFR II",
        port_name="IP_192.168.0.248",
        capabilities=required_capabilities(),
        can_manage_queues=True,
    )


def ready_discovery_json(base_queue: str = DEFAULT_PRINTER) -> str:
    return json.dumps(
        {
            "status": "ready",
            "name": base_queue,
            "driverName": "Canon Generic Plus UFR II",
            "portName": "IP_192.168.0.248",
            "canManageQueues": True,
            "capabilities": [capability.value for capability in PrintTicketCapability],
        }
    )


def expect_setup_plan(value: ProfileSetupPlan | ProfileSetupBlocked) -> ProfileSetupPlan:
    if isinstance(value, ProfileSetupPlan):
        return value
    raise AssertionError(f"Expected setup plan, received {value!r}")


def expect_setup_blocked(value: ProfileSetupPlan | ProfileSetupBlocked) -> ProfileSetupBlocked:
    if isinstance(value, ProfileSetupBlocked):
        return value
    raise AssertionError(f"Expected setup block, received {value!r}")


def expect_base_inspection(value: BaseQueueInspection | DiscoveryFailure) -> BaseQueueInspection:
    if isinstance(value, BaseQueueInspection):
        return value
    raise AssertionError(f"Expected base queue inspection, received {value!r}")


def expect_discovery_failure(value: BaseQueueInspection | DiscoveryFailure) -> DiscoveryFailure:
    if isinstance(value, DiscoveryFailure):
        return value
    raise AssertionError(f"Expected discovery failure, received {value!r}")


class PrinterProfileContractTest(unittest.TestCase):
    def test_contract_default_operator_queue_names_derive_from_current_base_queue(self) -> None:
        # Given: the currently configured Canon base queue
        base_queue = DEFAULT_PRINTER

        # When: operators apply the agreed editable profile suffixes
        queue_names = tuple(f"{base_queue} - {suffix}" for suffix in ("세로", "가로", "구분지"))

        # Then: the visible defaults remain stable for later UI wiring
        self.assertEqual(
            ("iR-ADV 8585 III - 세로", "iR-ADV 8585 III - 가로", "iR-ADV 8585 III - 구분지"),
            queue_names,
        )


class PrinterProfileSetupTest(unittest.TestCase):
    def test_profile_defaults_use_left_hand_book_binding_for_each_orientation(self) -> None:
        portrait = ticket_defaults_for(PrintRouteKind.PORTRAIT)
        landscape = ticket_defaults_for(PrintRouteKind.LANDSCAPE)
        separator = ticket_defaults_for(PrintRouteKind.SEPARATOR)

        self.assertIs(DuplexBinding.LONG_EDGE, portrait.duplex_binding)
        self.assertIs(DuplexBinding.SHORT_EDGE, landscape.duplex_binding)
        self.assertIs(DuplexBinding.SHORT_EDGE, separator.duplex_binding)

    def test_executes_only_the_profile_commands_created_by_the_plan(self) -> None:
        # Given: a setup plan for missing dedicated profile queues.
        given_plan = expect_setup_plan(
            plan_profile_setup(derive_profile_names(DEFAULT_PRINTER), ready_base_inspection(), frozenset())
        )

        class Runner:
            def __init__(self) -> None:
                self.scripts: list[str] = []

            def run(self, script: str) -> PowerShellResult:
                self.scripts.append(script)
                return PowerShellResult(return_code=0, stdout='{"status":"created"}', stderr="")

        runner = Runner()

        # When: the application executes the approved setup plan.
        outcome = __import__("unit_print_printer_profiles").execute_profile_setup(given_plan, runner)

        # Then: it runs exactly the derived profile mutations and reports them for rollback.
        self.assertEqual(given_plan.rollback.created_queue_names, outcome.created_queue_names)
        self.assertEqual(len(given_plan.commands), len(runner.scripts))
        self.assertTrue(all(DEFAULT_PRINTER not in command.mutation_target for command in given_plan.commands))

    def test_stops_profile_setup_after_first_failed_command(self) -> None:
        # Given: a setup plan whose second profile command fails.
        given_plan = expect_setup_plan(
            plan_profile_setup(derive_profile_names(DEFAULT_PRINTER), ready_base_inspection(), frozenset())
        )

        class Runner:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, _script: str) -> PowerShellResult:
                self.calls += 1
                return PowerShellResult(
                    return_code=1 if self.calls == 2 else 0,
                    stdout='{"status":"created"}' if self.calls != 2 else "",
                    stderr="access denied" if self.calls == 2 else "",
                )

        runner = Runner()

        # When: setup encounters the first failing profile creation.
        outcome = __import__("unit_print_printer_profiles").execute_profile_setup(given_plan, runner)

        # Then: only confirmed profile queues are tracked and later mutations are not attempted.
        self.assertEqual((given_plan.commands[0].queue_name,), outcome.created_queue_names)
        self.assertEqual(given_plan.commands[1].queue_name, outcome.failed_queue_name)
        self.assertEqual(2, runner.calls)

    def test_routes_each_print_kind_to_its_named_profile(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)

        when_routes = tuple(route_print_job(given_profiles, kind) for kind in PrintRouteKind)

        self.assertEqual(
            (
                (PrintRouteKind.PORTRAIT, "iR-ADV 8585 III - 세로"),
                (PrintRouteKind.LANDSCAPE, "iR-ADV 8585 III - 가로"),
                (PrintRouteKind.SEPARATOR, "iR-ADV 8585 III - 구분지"),
            ),
            tuple((route.kind, route.queue_name) for route in when_routes),
        )

    def test_discovers_driver_port_and_capabilities_through_read_only_command(self) -> None:
        given_runner = Mock()
        given_runner.run.return_value = PowerShellResult(
            return_code=0,
            stdout=ready_discovery_json(),
            stderr="",
        )

        when_discovery = expect_base_inspection(inspect_base_queue(DEFAULT_PRINTER, given_runner))

        self.assertEqual("Canon Generic Plus UFR II", when_discovery.driver_name)
        script = given_runner.run.call_args.args[0]
        self.assertIn("Get-Printer", script)
        self.assertIn("Get-PrintConfiguration", script)
        self.assertIn("PrintCapabilitiesXML", script)
        self.assertNotIn("Add-Printer", script)
        self.assertNotIn("Remove-Printer", script)

    def test_plans_four_missing_profiles_with_required_print_ticket_values(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)
        given_inspection = ready_base_inspection()

        when_plan = expect_setup_plan(plan_profile_setup(given_profiles, given_inspection, frozenset()))
        self.assertEqual(
            given_profiles.profile_queues,
            tuple(command.queue_name for command in when_plan.commands),
        )
        self.assertEqual(given_profiles.profile_queues, when_plan.rollback.created_queue_names)
        self.assertNotIn(DEFAULT_PRINTER, when_plan.rollback.created_queue_names)
        for command in when_plan.commands:
            self.assertNotEqual(DEFAULT_PRINTER, command.mutation_target)
            self.assertIn("Add-Printer", command.script)
            self.assertIn("Remove-Printer", command.script)
            self.assertIn("Get-PrintConfiguration", command.script)
            self.assertIn("Set-PrintConfiguration", command.script)
            self.assertIn("psk:ISOA4", command.script)
            self.assertIn('"psk:$logicalDuplex"', command.script)
            self.assertIn("psk:Monochrome", command.script)
            self.assertIn("psk:PageOrientation", command.script)
            self.assertIn("$portraitLabel -match '가로'", command.script)
            self.assertIn("UserPrintTicket", command.script)
            self.assertIn("DefaultPrintTicket.Clone()", command.script)
            self.assertIn("$profileQueue.Commit()", command.script)
            self.assertIn('"psk:$logicalOrientation"', command.script)
            self.assertIn('"psk:$logicalStaple"', command.script)
        self.assertIn("$logicalOrientation = 'Portrait'", when_plan.commands[0].script)
        self.assertIn("$logicalDuplex = 'TwoSidedLongEdge'", when_plan.commands[0].script)
        self.assertIn("$logicalStaple = 'StapleTopLeft'", when_plan.commands[0].script)
        self.assertIn("'StapleTopRight'", when_plan.commands[0].script)
        self.assertIn("$logicalOrientation = 'Landscape'", when_plan.commands[1].script)
        self.assertIn("$logicalDuplex = 'TwoSidedShortEdge'", when_plan.commands[1].script)
        self.assertIn("$logicalStaple = 'StapleTopLeft'", when_plan.commands[1].script)
        self.assertIn("'StapleBottomLeft'", when_plan.commands[1].script)
        self.assertIn("$logicalOrientation = 'Landscape'", when_plan.commands[2].script)
        self.assertIn("$logicalDuplex = 'TwoSidedShortEdge'", when_plan.commands[2].script)
        self.assertIn("$logicalStaple = 'None'", when_plan.commands[2].script)
        self.assertEqual(given_profiles.able_queue, when_plan.commands[3].queue_name)
        self.assertIn("$logicalOrientation = 'Landscape'", when_plan.commands[3].script)
        self.assertIn("$logicalDuplex = 'TwoSidedShortEdge'", when_plan.commands[3].script)
        self.assertIn("$logicalStaple = 'StapleTopLeft'", when_plan.commands[3].script)

    def test_returns_rollback_metadata_for_only_queues_created_by_this_plan(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)
        given_existing = frozenset((given_profiles.portrait_queue, given_profiles.landscape_queue))

        when_plan = expect_setup_plan(plan_profile_setup(given_profiles, ready_base_inspection(), given_existing))
        expected = (given_profiles.separator_queue, given_profiles.able_queue)
        self.assertEqual(expected, when_plan.rollback.created_queue_names)
        self.assertEqual(expected, tuple(command.queue_name for command in when_plan.commands))

    def test_returns_no_mutation_plan_when_required_capability_is_missing(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)
        given_inspection = BaseQueueInspection(
            queue_name=DEFAULT_PRINTER,
            driver_name="Canon Generic Plus UFR II",
            port_name="IP_192.168.0.248",
            capabilities=required_capabilities() - {PrintTicketCapability.STAPLE_TOP_LEFT},
            can_manage_queues=True,
        )

        when_outcome = expect_setup_blocked(plan_profile_setup(given_profiles, given_inspection, frozenset()))
        self.assertEqual(SetupBlockReason.MISSING_CAPABILITY, when_outcome.reason)
        self.assertEqual((), when_outcome.commands)

    def test_blocks_ports_that_cannot_verify_canon_department_credentials(self) -> None:
        for port_name in ("WSD-12345678", "USB001"):
            with self.subTest(port_name=port_name):
                given_inspection = BaseQueueInspection(
                    queue_name=DEFAULT_PRINTER,
                    driver_name="Canon Generic Plus UFR II",
                    port_name=port_name,
                    capabilities=required_capabilities(),
                    can_manage_queues=True,
                )

                when_outcome = expect_setup_blocked(
                    plan_profile_setup(derive_profile_names(DEFAULT_PRINTER), given_inspection, frozenset())
                )

                self.assertEqual(SetupBlockReason.UNSUPPORTED_AUTH_PORT, when_outcome.reason)
                self.assertIn("Standard TCP/IP", when_outcome.detail)
                self.assertEqual((), when_outcome.commands)

    def test_returns_no_mutation_plan_when_queue_management_permission_is_absent(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)
        given_inspection = BaseQueueInspection(
            queue_name=DEFAULT_PRINTER,
            driver_name="Canon Generic Plus UFR II",
            port_name="IP_192.168.0.248",
            capabilities=required_capabilities(),
            can_manage_queues=False,
        )

        when_outcome = expect_setup_blocked(plan_profile_setup(given_profiles, given_inspection, frozenset()))
        self.assertEqual(SetupBlockReason.PERMISSION_DENIED, when_outcome.reason)
        self.assertEqual((), when_outcome.commands)

    def test_blocks_stale_profile_input_for_a_different_base_queue(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)
        given_inspection = ready_base_inspection("Another Canon Queue")

        when_outcome = expect_setup_blocked(plan_profile_setup(given_profiles, given_inspection, frozenset()))
        self.assertEqual(SetupBlockReason.STALE_BASE_QUEUE, when_outcome.reason)
        self.assertEqual((), when_outcome.commands)

    def test_blocks_malformed_base_queue_description_before_mutation(self) -> None:
        given_profiles = derive_profile_names(DEFAULT_PRINTER)
        given_inspection = BaseQueueInspection(
            queue_name=DEFAULT_PRINTER,
            driver_name="",
            port_name="IP_192.168.0.248",
            capabilities=required_capabilities(),
            can_manage_queues=True,
        )

        when_outcome = expect_setup_blocked(plan_profile_setup(given_profiles, given_inspection, frozenset()))
        self.assertEqual(SetupBlockReason.MALFORMED_BASE_QUEUE, when_outcome.reason)
        self.assertEqual((), when_outcome.commands)

    def test_rejects_misleading_success_output_that_omits_printer_description(self) -> None:
        given_runner = Mock()
        given_runner.run.return_value = PowerShellResult(
            return_code=0,
            stdout=json.dumps({"status": "success", "name": DEFAULT_PRINTER}),
            stderr="",
        )

        when_discovery = expect_discovery_failure(inspect_base_queue(DEFAULT_PRINTER, given_runner))

        self.assertEqual(SetupBlockReason.MALFORMED_BASE_QUEUE, when_discovery.reason)

    def test_cli_route_demo_reports_routes_without_mutation_commands(self) -> None:
        # Given: an operator wants a non-printing route preview from the CLI
        command = [sys.executable, "-m", "unit_print_printer_profiles", DEFAULT_PRINTER]

        # When: the module is invoked directly
        completed = subprocess.run(command, check=True, capture_output=True, text=True)

        # Then: stdout contains route data only, not queue mutation commands
        payload = json.loads(completed.stdout)
        self.assertEqual(DEFAULT_PRINTER, payload["base_queue"])
        self.assertEqual(
            {
                "portrait": "iR-ADV 8585 III - 세로",
                "landscape": "iR-ADV 8585 III - 가로",
                "separator": "iR-ADV 8585 III - 구분지",
            },
            payload["routes"],
        )
        self.assertNotIn("Add-Printer", completed.stdout)
        self.assertNotIn("Remove-Printer", completed.stdout)


if __name__ == "__main__":
    unittest.main()
