from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import Mock

from unit_print_device_state import DeviceHealth, DeviceSnapshot
from unit_print_manager import PrintManagerApp
from unit_print_notifications import (
    NotificationGate,
    NotificationLevel,
    OperatorNotification,
    WindowsBalloonNotifier,
)


class FakeNotificationBackend:
    def __init__(self) -> None:
        self.shown: list[tuple[OperatorNotification, bool]] = []
        self.hide_calls = 0
        self.close_calls = 0

    def show(self, notification: OperatorNotification, *, replace: bool) -> bool:
        self.shown.append((notification, replace))
        return True

    def hide(self) -> None:
        self.hide_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class NotificationTests(unittest.TestCase):
    def test_gate_delivers_once_per_transition_and_rearms_after_clear(self) -> None:
        gate = NotificationGate()

        self.assertTrue(gate.allow("printer", "staple-empty"))
        self.assertFalse(gate.allow("printer", "staple-empty"))
        self.assertTrue(gate.allow("printer", "paper-empty"))
        gate.clear("printer")
        self.assertTrue(gate.allow("printer", "paper-empty"))

    def test_notifier_replaces_visible_balloon_and_resets_after_hide(self) -> None:
        backend = FakeNotificationBackend()
        notifier = WindowsBalloonNotifier(1, backend=backend)

        self.assertTrue(notifier.show("완료", "첫 작업 완료"))
        self.assertTrue(
            notifier.show(
                "오류",
                "스테이플을 확인하세요",
                NotificationLevel.ERROR,
            )
        )
        notifier.hide()
        self.assertTrue(notifier.show("복구", "프린터 정상"))

        self.assertEqual([False, True, False], [replace for _item, replace in backend.shown])
        self.assertEqual(NotificationLevel.ERROR, backend.shown[1][0].level)
        self.assertEqual(1, backend.hide_calls)

    def test_close_releases_backend(self) -> None:
        backend = FakeNotificationBackend()
        notifier = WindowsBalloonNotifier(1, backend=backend)
        notifier.show("완료", "완료")

        notifier.close()

        self.assertEqual(1, backend.close_calls)

    def test_status_completion_distinguishes_physical_and_windows_queue(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.notify_operator = Mock(return_value=True)

        app.handle_status_notification("실제 인쇄 완료")
        app.handle_status_notification("전송 완료 / 인쇄 완료")

        first = app.notify_operator.call_args_list[0].args
        second = app.notify_operator.call_args_list[1].args
        self.assertEqual("빠른 인쇄 완료", first[2])
        self.assertEqual("빠른 인쇄 대기열 완료", second[2])
        self.assertIn("Windows", second[3])

    def test_idle_connection_fault_and_recovery_each_notify_once(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.last_device_notification_health = DeviceHealth.READY
        app.device_fault_notification_active = False
        app.device_hold_active = False
        app.is_printing = False
        app.notification_gate = NotificationGate()
        app.notify_device_fault = Mock()
        app.notify_operator = Mock(return_value=True)
        offline = DeviceSnapshot.unavailable("프린터 연결 실패")

        app.handle_device_snapshot_notification(offline)

        app.notify_device_fault.assert_called_once_with(offline)
        app.device_fault_notification_active = True
        ready = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.READY,
            status_text="인쇄 가능",
            authenticated=True,
        )
        app.handle_device_snapshot_notification(ready)

        self.assertEqual("프린터 정상 복구", app.notify_operator.call_args.args[2])
        self.assertFalse(app.device_fault_notification_active)


if __name__ == "__main__":
    unittest.main()
