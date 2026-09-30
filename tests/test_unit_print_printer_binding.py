from __future__ import annotations

import json
import socket
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from unit_print_printer_binding import (
    PRINTER_BINDING_INVENTORY_SCRIPT,
    PrinterBindingInventory,
    PrinterBindingReadError,
    PrinterQueueBinding,
    normalize_printer_host,
    parse_printer_binding_inventory,
    read_printer_binding_inventory,
    resolve_printer_binding_hosts,
    validate_printer_binding,
)


def binding(name="Canon - 세로", host="192.168.0.248", **changes):
    values = dict(name=name, port_name="IP_192.168.0.248", host_address=host,
                  port_monitor="TCPMON.DLL", printer_type="Local")
    values.update(changes)
    return PrinterQueueBinding(**values)


def inventory(*items):
    return PrinterBindingInventory(tuple(items))


class PrinterBindingValidationTests(unittest.TestCase):
    def test_all_effective_routes_must_match_including_override(self):
        records = inventory(binding("Base"), binding("Portrait"), binding("Override", "192.168.0.20"))
        result = validate_printer_binding(records, ["Base", "Portrait", "Override"], "http://192.168.0.248:8000/rps/")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.matched_names, ("Base", "Portrait"))
        self.assertEqual(result.different_names, ("Override",))
        self.assertIn("Override", result.message)

    def test_all_profiles_same_device_allow_completion_tracking(self):
        records = inventory(binding("Base"), binding("Portrait"), binding("Landscape"), binding("Separator"))
        result = validate_printer_binding(records, ["Base", "Portrait", "Landscape", "Separator"], "192.168.0.248")
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.matched_names), 4)

    def test_missing_queue_and_empty_names_fail(self):
        result = validate_printer_binding(inventory(binding()), ["Missing"], "192.168.0.248")
        self.assertEqual(result.missing_names, ("Missing",))
        self.assertFalse(result.is_valid)
        self.assertFalse(validate_printer_binding(inventory(binding()), [], "192.168.0.248").is_valid)
        self.assertFalse(validate_printer_binding(inventory(binding()), ["Canon - 세로", "  "], "192.168.0.248").is_valid)

    def test_windows_queue_name_lookup_casefolded(self):
        self.assertTrue(validate_printer_binding(inventory(binding("Canon")), ["canon"], "192.168.0.248").is_valid)

    def test_port_name_does_not_prove_host(self):
        result = validate_printer_binding(inventory(binding(host="")), ["Canon - 세로"], "192.168.0.248")
        self.assertEqual(result.unverifiable_names, ("Canon - 세로",))

    def test_unsupported_ports_are_unknown_even_if_host_is_reported(self):
        for changes in (
            {"port_name": "WSD-abc"}, {"port_name": "USB001"},
            {"port_name": r"\\server\printer"}, {"port_name": "PORT1,PORT2"},
            {"printer_type": "Connection"}, {"printer_type": ""},
            {"port_monitor": "WSD Port"}, {"port_monitor": ""},
            {"name": r"\\server\Canon"},
        ):
            with self.subTest(changes=changes):
                record = binding(**changes)
                result = validate_printer_binding(inventory(record), [record.name], "192.168.0.248")
                self.assertFalse(result.is_valid)
                self.assertEqual(result.unverifiable_names, (record.name,))

    def test_standard_tcpip_monitor_name_supported(self):
        self.assertTrue(validate_printer_binding(inventory(binding(port_monitor="Standard TCP/IP Port")),
                                                ["Canon - 세로"], "192.168.0.248").is_valid)

    def test_ipv6_compression_and_mapped_ipv4(self):
        record = binding(host="2001:db8:0:0:0:0:0:248")
        self.assertTrue(validate_printer_binding(inventory(record), [record.name], "https://[2001:db8::248]:8000/").is_valid)
        record = binding(host="::ffff:192.168.0.248")
        self.assertTrue(validate_printer_binding(inventory(record), [record.name], "192.168.0.248").is_valid)

    def test_identical_hostname_normalizes_case_and_trailing_dot(self):
        record = binding(host="CANON.Office.")
        self.assertTrue(validate_printer_binding(inventory(record), [record.name], "https://canon.office/rps").is_valid)

    def test_aliases_require_single_identical_resolved_ip(self):
        record = binding(host="canon.office")
        resolutions = {"canon.office": ("192.168.0.248",), "printer.office": ("192.168.0.248",)}
        result = validate_printer_binding(inventory(record), [record.name], "http://printer.office:8000",
                                         resolved_hosts=resolutions)
        self.assertTrue(result.is_valid)
        # Merely overlapping addresses do not identify one physical device.
        resolutions["canon.office"] = ("192.168.0.248", "192.168.0.20")
        result = validate_printer_binding(inventory(record), [record.name], "printer.office", resolved_hosts=resolutions)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.unverifiable_names, (record.name,))

    def test_unknown_hostname_alias_and_different_alias_addresses(self):
        record = binding(host="canon.office")
        result = validate_printer_binding(inventory(record), [record.name], "192.168.0.248")
        self.assertEqual(result.unverifiable_names, (record.name,))
        result = validate_printer_binding(inventory(record), [record.name], "192.168.0.248",
                                         resolved_hosts={"canon.office": ("192.168.0.20",)})
        self.assertEqual(result.different_names, (record.name,))

    def test_invalid_target_does_not_leak_uri_credentials(self):
        target = "garbage://department:secret@printer/"
        result = validate_printer_binding(inventory(binding()), ["Canon - 세로"], target)
        self.assertFalse(result.is_valid)
        self.assertNotIn("secret", result.message)
        self.assertEqual(result.target_host, "")

    def test_invalid_addresses_never_establish_identity(self):
        for host in ("", "192.168.1.999", "printer bad", "printer/path", "http://printer:999999"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    normalize_printer_host(host)


class PrinterBindingInventoryTests(unittest.TestCase):
    def payload(self):
        return {"queues": [{"name": "Canon - 세로", "portName": "IP_192.168.0.248",
                            "printerHostAddress": "192.168.0.248", "portMonitor": "TCPMON.DLL", "printerType": "Local"}]}

    def test_utf8_bom_and_korean_queue_names(self):
        raw = ("\ufeff" + json.dumps(self.payload(), ensure_ascii=False)).encode("utf-8")
        result = parse_printer_binding_inventory(raw)
        self.assertEqual(result.queues, (binding(),))
        self.assertEqual(parse_printer_binding_inventory(raw.decode("utf-8")), result)

    def test_missing_or_null_port_fields_stay_unknown(self):
        payload = {"queues": [{"name": "Canon", "portName": None}]}
        record = parse_printer_binding_inventory(json.dumps(payload)).queues[0]
        self.assertEqual(record.host_address, "")
        self.assertFalse(validate_printer_binding(inventory(record), ["Canon"], "192.168.0.248").is_valid)

    def test_malformed_partial_or_duplicate_payload_is_rejected(self):
        for payload in ("", "[]", '{"queues": null}', '{"queues": {}}', '{"queues": [null]}',
                        '{"queues": [{"name": 123}]}', '{"queues": [{"name": "a"}, {"name": "A"}]}'):
            with self.subTest(payload=payload):
                with self.assertRaises(PrinterBindingReadError):
                    parse_printer_binding_inventory(payload)

    @patch("unit_print_printer_binding.subprocess.run")
    def test_system_reader_uses_one_read_only_utf8_command(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(self.payload()).encode("utf-8"), b"")
        self.assertEqual(read_printer_binding_inventory().queues, (binding(),))
        run.assert_called_once()
        arguments, settings = run.call_args
        self.assertIn("-NonInteractive", arguments[0])
        self.assertEqual(settings["timeout"], 10.0)
        self.assertEqual(PRINTER_BINDING_INVENTORY_SCRIPT.count("Get-Printer -"), 1)
        self.assertEqual(PRINTER_BINDING_INVENTORY_SCRIPT.count("Get-PrinterPort -"), 1)
        self.assertNotIn("Set-Printer", PRINTER_BINDING_INVENTORY_SCRIPT)
        self.assertNotIn("Add-Printer", PRINTER_BINDING_INVENTORY_SCRIPT)

    @patch("unit_print_printer_binding.subprocess.run")
    def test_system_errors_are_sanitized(self, run):
        for error in (OSError("secret detail"), subprocess.TimeoutExpired("secret detail", 10)):
            with self.subTest(error=type(error).__name__):
                run.side_effect = error
                with self.assertRaises(PrinterBindingReadError) as raised:
                    read_printer_binding_inventory()
                self.assertNotIn("secret", str(raised.exception))
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"secret detail")
        with self.assertRaises(PrinterBindingReadError) as raised:
            read_printer_binding_inventory()
        self.assertNotIn("secret", str(raised.exception))


class PrinterBindingDnsTests(unittest.TestCase):
    def test_ips_skip_dns(self):
        lookup = Mock(side_effect=AssertionError("DNS must not run"))
        result = resolve_printer_binding_hosts(inventory(binding()), ["Canon - 세로"], "192.168.0.248", resolver=lookup)
        self.assertEqual(result, {})
        lookup.assert_not_called()

    def test_alias_lookup_boundary_returns_canonical_ip_sets(self):
        lookup = Mock(return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:192.168.0.248", 0, 0, 0)),
                                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.248", 0))])
        result = resolve_printer_binding_hosts(inventory(binding(host="CANON.Office.")), ["Canon - 세로"],
                                              "http://printer.office:8000", resolver=lookup)
        self.assertEqual(result, {"canon.office": ("192.168.0.248",), "printer.office": ("192.168.0.248",)})
        self.assertEqual(lookup.call_count, 2)

    def test_dns_failure_does_not_prove_alias(self):
        lookup = Mock(side_effect=socket.gaierror("unavailable"))
        self.assertEqual(resolve_printer_binding_hosts(inventory(binding(host="canon.office")), ["Canon - 세로"],
                                                      "192.168.0.248", resolver=lookup), {})

    def test_dns_caller_timeout_is_bounded(self):
        release = threading.Event()
        entered = threading.Event()

        def blocked_lookup(*args):
            entered.set()
            release.wait(2)
            return []

        try:
            result = resolve_printer_binding_hosts(inventory(binding(host="canon.office")), ["Canon - 세로"],
                                                  "192.168.0.248", timeout=0.02, resolver=blocked_lookup)
            self.assertTrue(entered.is_set())
            self.assertEqual(result, {})
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
