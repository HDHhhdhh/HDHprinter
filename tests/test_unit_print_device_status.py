from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from unit_print_canon_status import CanonStatusClient, parse_health_html, parse_jobs_html, parse_supplies_html
from unit_print_device_credentials import credential_target
from unit_print_device_scheduler import prepare_dispatch_copy
from unit_print_device_monitor import PrinterDeviceMonitor
from datetime import datetime

from unit_print_device_state import DeviceHealth, DeviceJob, DeviceJobState, DeviceSnapshot
from unit_print_device_ui import build_device_status_view
from unit_print_dispatch_state import RollingDispatchWindow
from unit_print_eta import (
    completed_job_seconds_per_sheet,
    estimate_remaining_seconds,
    estimated_sheets,
)


class CanonStatusParserTests(unittest.TestCase):
    def test_reads_a4_sensor_bands_and_finisher_supplies(self) -> None:
        html = """
        <table>
          <tr><th>급지위치</th><th>용지 잔량</th><th>용지 크기</th><th>명칭</th></tr>
          <tr><td>카세트 1</td><td><img alt="66-100%">양호</td><td>A4</td><td>보통용지</td></tr>
          <tr><td>카세트 2</td><td><img title="1-33%">적음</td><td>A4</td><td>보통용지</td></tr>
          <tr><td>카세트 3</td><td><img alt="33-66%"></td><td>B4</td><td>보통용지</td></tr>
        </table>
        <table>
          <tr><th>종류</th><th>상태</th></tr>
          <tr><td>토너 잔량</td><td>50%</td></tr>
          <tr><td>스테이플 잔량</td><td>양호</td></tr>
          <tr><td>중철 스테이플 잔량</td><td>적음</td></tr>
        </table>
        """
        supplies = parse_supplies_html(html)
        self.assertEqual([(66, 100), (1, 33)], [
            (item.level_min_percent, item.level_max_percent)
            for item in supplies.a4_sources
        ])
        self.assertIn("카세트 1 66~100%", supplies.a4_level_text)
        self.assertEqual("50%", supplies.toner_text)
        self.assertEqual("양호", supplies.staple_text)
        self.assertEqual("적음", supplies.saddle_staple_text)

    def test_reads_status_grids_nested_inside_layout_tables(self) -> None:
        html = """
        <table class="layout"><tr><td>소모품
          <table class="status">
            <tr><th>급지위치</th><th>용지 잔량</th><th>용지 크기</th></tr>
            <tr><td>카세트 1</td><td><img src="paper_100.gif"></td><td>A4</td></tr>
          </table>
        </td></tr></table>
        """
        supplies = parse_supplies_html(html)
        self.assertEqual(1, len(supplies.a4_sources))
        self.assertEqual("66~100%", supplies.a4_sources[0].level_text)

    def test_reads_canon_script_backed_cassette_levels(self) -> None:
        html = r'''
        <script>
        var cstInfo = {
          "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"3","totalPapVol":"3"},
          "2":{"cstName":"카세트 2","pSizeName":"A4","remainPapVol":"1","totalPapVol":"3"},
          "3":{"cstName":"카세트 3","pSizeName":"B4","remainPapVol":"2","totalPapVol":"3"}
        };
        var tonerVolInfo = {"tonerKVol":"50"};
        </script>
        '''
        supplies = parse_supplies_html(html)
        self.assertEqual(
            [("카세트 1", "66~100%"), ("카세트 2", "1~33%")],
            [(item.source, item.level_text) for item in supplies.a4_sources],
        )
        self.assertEqual("블랙 50%", supplies.toner_text)

    def test_script_source_is_not_mistaken_for_a_supply_value(self) -> None:
        html = f'''
        <table><tr><td>레이아웃<script>
        var messages = "토너 스테이플 {'아주 긴 설명 ' * 100}";
        var tonerVolInfo = {{"tonerKVol":"50"}};
        </script></td><td>포털</td></tr></table>
        '''
        supplies = parse_supplies_html(html)
        self.assertEqual("블랙 50%", supplies.toner_text)
        self.assertEqual("알 수 없음", supplies.staple_text)

    def test_reads_job_grid_nested_inside_layout_table(self) -> None:
        html = """
        <table><tr><td>작업 이력
          <table>
            <tr><th>접수번호</th><th>결과</th><th>파일명</th><th>종료코드</th></tr>
            <tr><td>77</td><td>확인</td><td>QP_ABCDEF1234567890_exam.pdf</td><td>OK</td></tr>
          </table>
        </td></tr></table>
        """
        jobs = parse_jobs_html(html, active=False)
        self.assertEqual(1, len(jobs))
        self.assertEqual("ABCDEF1234567890", jobs[0].dispatch_token)
        self.assertEqual(DeviceJobState.COMPLETED, jobs[0].state)

    def test_reads_completed_job_by_dispatch_token(self) -> None:
        html = """
        <table>
          <tr><th>접수번호</th><th>결과</th><th>시작시각</th><th>종료시각</th>
              <th>부문 ID</th><th>작업형태</th><th>파일명</th><th>사용자명</th>
              <th>원고 페이지수</th><th>출력 페이지수</th>
              <th>매수 x 부수</th><th>종료코드</th></tr>
          <tr><td>00421</td><td>확인</td><td>2026 09/18 15:00:00</td>
              <td>2026 09/18 15:00:09</td><td>2011</td><td>프린터 인쇄</td>
              <td>QP_AB12CD34EF56AB78_exam.pdf</td><td>operator</td>
              <td>4</td><td>2</td><td>1 x 1</td><td>OK</td></tr>
        </table>
        """
        jobs = parse_jobs_html(html, active=False)
        self.assertEqual(1, len(jobs))
        self.assertEqual(DeviceJobState.COMPLETED, jobs[0].state)
        self.assertEqual("AB12CD34EF56AB78", jobs[0].dispatch_token)
        self.assertEqual(2, jobs[0].output_pages)

    def test_reads_canon_script_header_job_log_rows(self) -> None:
        html = """
        <table><script>MenuMsgSrcDraw();</script><tbody>
          <tr><td>8246</td><td><script>log_rslt("1");</script></td>
              <td>2026 09/18 18:56:24</td><td>2026 09/18 18:56:48</td>
              <td>2011</td><td><script>job_t("8");</script></td>
              <td>C:\\spool\\QP_ABC70E0A18A14CD0_exam.pdf</td><td>operator</td>
              <td>8</td><td>8</td><td>4 x 1</td><td>OK</td></tr>
        </tbody></table>
        """
        jobs = parse_jobs_html(html, active=False)
        self.assertEqual(1, len(jobs))
        self.assertEqual("ABC70E0A18A14CD0", jobs[0].dispatch_token)
        self.assertEqual("8246", jobs[0].receipt_no)
        self.assertEqual(DeviceJobState.COMPLETED, jobs[0].state)
        self.assertEqual(8, jobs[0].output_pages)

    def test_health_error_prevents_ready_state(self) -> None:
        health, _text, errors, _serial = parse_health_html(
            "<h1>에러 정보</h1><p>피니셔의 스테이플을 보급하십시오.</p>"
        )
        self.assertEqual(DeviceHealth.ERROR, health)
        self.assertTrue(errors)

    def test_error_free_page_is_dispatchable_even_without_ready_phrase(self) -> None:
        health, text, errors, _serial = parse_health_html(
            "<h1>에러 정보</h1><p>에러가 없습니다.</p>"
        )
        self.assertEqual(DeviceHealth.READY, health)
        self.assertEqual("오류 없음", text)
        self.assertFalse(errors)

    def test_visible_preparing_status_is_busy_not_ready(self) -> None:
        health, _text, errors, _serial = parse_health_html(
            "<p>에러가 없습니다.</p><p>프린터 준비 중입니다.</p>"
        )
        self.assertEqual(DeviceHealth.BUSY, health)
        self.assertFalse(errors)

    def test_portal_message_catalog_is_not_mistaken_for_current_health(self) -> None:
        html = r'''
        <script>
          var templates = "인쇄 가능합니다 스테이플을 보급하십시오 printer error";
          var prt_status = "0";
          var errCodeTbl = new Array(null);
        </script>
        '''
        health, text, errors, _serial = parse_health_html(html)
        self.assertEqual(DeviceHealth.READY, health)
        self.assertEqual("인쇄 가능", text)
        self.assertFalse(errors)

    def test_portal_status_variable_blocks_dispatch_on_device_error(self) -> None:
        html = r'''
        <script>
          var templates = "인쇄 가능합니다 에러가 없습니다";
          var prt_status = "32";
          var errCodeTbl = new Array(null);
        </script>
        '''
        health, _text, errors, _serial = parse_health_html(html)
        self.assertEqual(DeviceHealth.ERROR, health)
        self.assertTrue(errors)

    def test_portal_secondary_error_bit_blocks_dispatch(self) -> None:
        html = r'''
        <script>
          var prt_status = "4";
          var errCodeTbl = new Array(null);
        </script>
        '''
        health, _text, errors, _serial = parse_health_html(html)
        self.assertEqual(DeviceHealth.ERROR, health)
        self.assertTrue(errors)

    def test_portal_error_code_overrides_ready_status(self) -> None:
        html = r'''
        <script>
          var prt_status = "0";
          var errCodeTbl = new Array("E000123");
        </script>
        '''
        health, _text, errors, _serial = parse_health_html(html)
        self.assertEqual(DeviceHealth.ERROR, health)
        self.assertTrue(errors)

    def test_reads_inline_canon_active_job_status_code(self) -> None:
        html = r'''
        <table>
          <tr><th></th><th>날짜/시각</th><th>작업명</th><th>사용자명</th>
              <th>상황</th><th>작업 조작</th></tr>
          <tr><td></td><td>2026 09/18 20:00:00</td>
              <td>QP_1122334455667788_exam.pdf</td><td>operator</td>
              <td><script>job_s("9", "false");</script></td><td></td></tr>
        </table>
        '''
        jobs = parse_jobs_html(html, active=True)
        self.assertEqual(1, len(jobs))
        self.assertEqual(DeviceJobState.HELD, jobs[0].state)
        self.assertEqual("1122334455667788", jobs[0].dispatch_token)


class _ScriptedCanonClient(CanonStatusClient):
    def __init__(self, responses: dict[str, list[str] | str]) -> None:
        super().__init__("http://printer.invalid")
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in responses.items()
        }
        self.calls: list[str] = []
        self._authenticated = True
        self.login_calls = 0

    def _request(self, key_or_path: str, data: dict[str, str] | None = None) -> str:
        del data
        self.calls.append(key_or_path)
        values = self.responses.get(key_or_path)
        if not values:
            raise AssertionError(f"unexpected request: {key_or_path}")
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    def login(self) -> None:
        self.login_calls += 1
        self._authenticated = True
        self._job_monitor_initialized = False


class CanonStatusClientTests(unittest.TestCase):
    def test_job_page_expiry_reinitializes_monitor_before_retry(self) -> None:
        client = _ScriptedCanonClient(
            {
                "/sysmonitor": ["ok", "ok"],
                "active_jobs": ["페이지를 열 수 없습니다", "<table></table>"],
            }
        )
        self.assertEqual("<table></table>", client._request_job_page("active_jobs"))
        self.assertEqual(
            ["/sysmonitor", "active_jobs", "/sysmonitor", "active_jobs"],
            client.calls,
        )

    def test_job_page_login_form_reauthenticates_before_retry(self) -> None:
        login_form = '<form><input name="dept_id"><input type="password" name="pin"></form>'
        client = _ScriptedCanonClient(
            {
                "/sysmonitor": ["ok", "ok"],
                "active_jobs": [login_form, "<table></table>"],
            }
        )
        self.assertEqual("<table></table>", client._request_job_page("active_jobs"))
        self.assertEqual(1, client.login_calls)

    def test_snapshot_prefers_portal_without_requesting_invalid_detail_pages(self) -> None:
        portal = r'''
        <script>
          var prt_status = "0";
          var errCodeTbl = new Array(null);
          var cstInfo = {
            "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"3","totalPapVol":"3"}
          };
          var tonerVolInfo = {"tonerKVol":"50"};
        </script>
        '''
        client = _ScriptedCanonClient(
            {
                "portal": portal,
                "/sysmonitor": "ok",
                "active_jobs": "<table></table>",
                "job_log": "<table></table>",
            }
        )
        snapshot = client.read_snapshot()
        self.assertEqual(DeviceHealth.READY, snapshot.health)
        self.assertEqual("A4 카세트 1 66~100%", snapshot.consumables.a4_level_text)
        self.assertNotIn("health", client.calls)
        self.assertNotIn("supplies", client.calls)

    def test_one_empty_a4_cassette_does_not_block_dispatch(self) -> None:
        portal = r'''
        <script>
          var prt_status = "32";
          var errCodeTbl = new Array(
            0x1060202,
            null
          );
          var cstInfo = {
            "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"0","totalPapVol":"3"},
            "2":{"cstName":"카세트 2","pSizeName":"A4","remainPapVol":"3","totalPapVol":"3"}
          };
        </script>
        '''
        client = _ScriptedCanonClient(
            {
                "portal": portal,
                "/sysmonitor": "ok",
                "active_jobs": "<table></table>",
                "job_log": "<table></table>",
            }
        )

        snapshot = client.read_snapshot()

        self.assertEqual(DeviceHealth.READY, snapshot.health)
        self.assertTrue(snapshot.can_dispatch)
        self.assertFalse(snapshot.errors)
        self.assertIn("카세트 1 비어 있음", snapshot.status_text)

    def test_all_empty_a4_cassettes_block_dispatch(self) -> None:
        portal = r'''
        <script>
          var prt_status = "32";
          var errCodeTbl = new Array(0x1060202, null);
          var cstInfo = {
            "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"0","totalPapVol":"3"},
            "2":{"cstName":"카세트 2","pSizeName":"A4","remainPapVol":"0","totalPapVol":"3"}
          };
        </script>
        '''
        client = _ScriptedCanonClient(
            {
                "portal": portal,
                "/sysmonitor": "ok",
                "active_jobs": "<table></table>",
                "job_log": "<table></table>",
            }
        )

        snapshot = client.read_snapshot()

        self.assertEqual(DeviceHealth.ERROR, snapshot.health)
        self.assertFalse(snapshot.can_dispatch)
        self.assertIn("모든 카세트", snapshot.errors[0])

    def test_unknown_error_code_still_blocks_with_one_available_a4_cassette(self) -> None:
        portal = r'''
        <script>
          var prt_status = "32";
          var errCodeTbl = new Array(0x1060202, "E000123");
          var cstInfo = {
            "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"0","totalPapVol":"3"},
            "2":{"cstName":"카세트 2","pSizeName":"A4","remainPapVol":"3","totalPapVol":"3"}
          };
        </script>
        '''
        client = _ScriptedCanonClient(
            {
                "portal": portal,
                "/sysmonitor": "ok",
                "active_jobs": "<table></table>",
                "job_log": "<table></table>",
            }
        )

        snapshot = client.read_snapshot()

        self.assertEqual(DeviceHealth.ERROR, snapshot.health)
        self.assertFalse(snapshot.can_dispatch)
        self.assertIn("E000123", snapshot.errors[0])

    def test_hard_device_status_still_blocks_with_paper_warning_code(self) -> None:
        portal = r'''
        <script>
          var prt_status = "36";
          var errCodeTbl = new Array(0x1060202);
          var cstInfo = {
            "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"0","totalPapVol":"3"},
            "2":{"cstName":"카세트 2","pSizeName":"A4","remainPapVol":"3","totalPapVol":"3"}
          };
        </script>
        '''
        client = _ScriptedCanonClient(
            {
                "portal": portal,
                "/sysmonitor": "ok",
                "active_jobs": "<table></table>",
                "job_log": "<table></table>",
            }
        )

        snapshot = client.read_snapshot()

        self.assertEqual(DeviceHealth.ERROR, snapshot.health)
        self.assertFalse(snapshot.can_dispatch)

    def test_non_paper_fault_still_blocks_with_one_available_a4_cassette(self) -> None:
        portal = r'''
        <p>피니셔의 스테이플을 보급하십시오.</p>
        <script>
          var prt_status = "32";
          var errCodeTbl = new Array(null);
          var cstInfo = {
            "1":{"cstName":"카세트 1","pSizeName":"A4","remainPapVol":"0","totalPapVol":"3"},
            "2":{"cstName":"카세트 2","pSizeName":"A4","remainPapVol":"3","totalPapVol":"3"}
          };
        </script>
        '''
        client = _ScriptedCanonClient(
            {
                "portal": portal,
                "/sysmonitor": "ok",
                "active_jobs": "<table></table>",
                "job_log": "<table></table>",
            }
        )

        snapshot = client.read_snapshot()

        self.assertEqual(DeviceHealth.ERROR, snapshot.health)
        self.assertFalse(snapshot.can_dispatch)
        self.assertIn("스테이플", snapshot.errors[0])

    def test_lightweight_active_job_read_skips_portal_and_job_log(self) -> None:
        active_html = r'''
        <table>
          <tr><th></th><th>날짜/시각</th><th>작업명</th><th>사용자명</th>
              <th>상황</th><th>작업 조작</th></tr>
          <tr><td></td><td>2026 09/19 10:00:00</td>
              <td>QP_ABCDEF1234567890_exam.pdf</td><td>operator</td>
              <td><script>job_s("3", "false");</script></td><td></td></tr>
        </table>
        '''
        client = _ScriptedCanonClient(
            {
                "/sysmonitor": "ok",
                "active_jobs": active_html,
            }
        )
        jobs = client.read_active_jobs()
        self.assertEqual(1, len(jobs))
        self.assertEqual("ABCDEF1234567890", jobs[0].dispatch_token)
        self.assertEqual(["/sysmonitor", "active_jobs"], client.calls)


class _FastProbeClient:
    base_url = "http://printer.invalid"

    def __init__(
        self,
        active_responses: list[tuple[DeviceJob, ...]],
        recent_responses: list[tuple[DeviceJob, ...]] | None = None,
    ) -> None:
        self.active_responses = list(active_responses)
        self.recent_responses = list(recent_responses or [()])
        self.active_calls = 0
        self.recent_calls = 0

    @staticmethod
    def _next(values: list[tuple[DeviceJob, ...]]) -> tuple[DeviceJob, ...]:
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    def read_active_jobs(self) -> tuple[DeviceJob, ...]:
        self.active_calls += 1
        return self._next(self.active_responses)

    def read_recent_jobs(self) -> tuple[DeviceJob, ...]:
        self.recent_calls += 1
        return self._next(self.recent_responses)

    def read_snapshot(self) -> DeviceSnapshot:
        return DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.READY,
            authenticated=True,
        )


class DeviceDispatchTests(unittest.TestCase):
    def test_device_status_headline_never_duplicates_printer_prefix(self) -> None:
        snapshot = DeviceSnapshot.unavailable("프린터 상태 연결 전")
        self.assertEqual("프린터 상태 연결 전", build_device_status_view(snapshot).headline)

    def test_matching_copy_contains_unique_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "원본 시험.pdf"
            source.write_bytes(b"pdf placeholder")
            target = prepare_dispatch_copy(source, root / "spool", "0123456789ABCDEF")
            self.assertEqual(b"pdf placeholder", target.read_bytes())
            self.assertTrue(target.name.startswith("QP_0123456789ABCDEF_"))

    def test_unobserved_submission_keeps_its_slot_and_blocks_order_barrier(self) -> None:
        window = RollingDispatchWindow(1)
        entry = window.register("job", "0123456789ABCDEF")
        snapshot = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.READY,
            authenticated=True,
        )
        reconciled = window.reconcile(snapshot)
        self.assertFalse(reconciled.completed)
        self.assertEqual((entry,), window.entries)
        self.assertEqual((entry,), window.unobserved_entries(snapshot))
        self.assertEqual(0, window.slots_available)

    def test_rolling_window_refills_one_slot_per_exact_completion(self) -> None:
        window = RollingDispatchWindow(3)
        first = window.register("job-1", "AAAABBBBCCCC1111")
        window.register("job-2", "AAAABBBBCCCC2222")
        window.register("job-3", "AAAABBBBCCCC3333")
        self.assertEqual(0, window.slots_available)
        snapshot = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.BUSY,
            authenticated=True,
            recent_jobs=(
                DeviceJob(
                    "QP_AAAABBBBCCCC1111_exam.pdf",
                    state=DeviceJobState.COMPLETED,
                    dispatch_token="AAAABBBBCCCC1111",
                ),
            ),
        )
        result = window.reconcile(snapshot)
        self.assertEqual((first,), result.completed)
        self.assertEqual(1, window.slots_available)
        window.register("job-4", "AAAABBBBCCCC4444")
        self.assertEqual(["job-2", "job-3", "job-4"], [entry.job_id for entry in window.entries])

    def test_default_target_allows_one_printing_plus_five_waiting(self) -> None:
        window = RollingDispatchWindow(6)
        for index in range(6):
            window.register(f"job-{index}", f"TOKEN{index:011d}")
        self.assertEqual(6, window.outstanding_count)
        self.assertEqual(0, window.slots_available)

    def test_fast_acceptance_probe_observes_exact_active_token(self) -> None:
        token = "ABCDEF1234567890"
        accepted_job = DeviceJob(
            f"QP_{token}_exam.pdf",
            state=DeviceJobState.QUEUED,
            dispatch_token=token,
        )
        client = _FastProbeClient([(), (accepted_job,)])
        monitor = PrinterDeviceMonitor(client)
        result = monitor.wait_for_job_acceptance(
            token,
            cancel_event=threading.Event(),
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
            recent_interval_seconds=5.0,
        )
        self.assertFalse(result.timed_out)
        self.assertEqual(accepted_job, result.job)
        self.assertEqual(2, client.active_calls)

    def test_fast_acceptance_probe_checks_recent_log_for_short_job(self) -> None:
        token = "0123456789ABCDEF"
        completed_job = DeviceJob(
            f"QP_{token}_separator.pdf",
            state=DeviceJobState.COMPLETED,
            dispatch_token=token,
        )
        client = _FastProbeClient([()], [(completed_job,)])
        monitor = PrinterDeviceMonitor(client)
        result = monitor.wait_for_job_acceptance(
            token,
            cancel_event=threading.Event(),
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
            recent_interval_seconds=0.02,
        )
        self.assertEqual(completed_job, result.job)
        self.assertEqual(1, client.recent_calls)

    def test_next_submission_waits_until_canon_observes_prior_token(self) -> None:
        window = RollingDispatchWindow(3)
        first = window.register("job-1", "AAAABBBBCCCC1111")
        unseen = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.READY,
            authenticated=True,
        )
        self.assertEqual((first,), window.unobserved_entries(unseen))
        observed = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.BUSY,
            authenticated=True,
            active_jobs=(
                DeviceJob(
                    "QP_AAAABBBBCCCC1111_exam.pdf",
                    state=DeviceJobState.PRINTING,
                    dispatch_token="AAAABBBBCCCC1111",
                ),
            ),
        )
        self.assertFalse(window.unobserved_entries(observed))

    def test_held_job_stays_in_window_during_recovery(self) -> None:
        window = RollingDispatchWindow(3)
        held = window.register("job-1", "AAAABBBBCCCC1111")
        snapshot = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.ERROR,
            errors=("스테이플을 보급하십시오",),
            authenticated=True,
            active_jobs=(
                DeviceJob(
                    "QP_AAAABBBBCCCC1111_exam.pdf",
                    state=DeviceJobState.HELD,
                    dispatch_token="AAAABBBBCCCC1111",
                ),
            ),
        )
        result = window.reconcile(snapshot)
        self.assertEqual((held,), result.held)
        self.assertEqual(1, window.outstanding_count)
        self.assertEqual(2, window.slots_available)

    def test_eta_uses_sheet_count_and_stays_a_range_before_learning(self) -> None:
        self.assertEqual(3, estimated_sheets(5, 1, duplex=True))
        estimate = estimate_remaining_seconds(100, [], remaining_jobs=2)
        self.assertEqual("초기 추정", estimate.confidence)
        self.assertGreater(estimate.seconds_high, estimate.seconds_low)

    def test_eta_rejects_submission_latency_and_learns_device_print_time(self) -> None:
        started = datetime(2026, 9, 20, 10, 0, 0).astimezone()
        ended = datetime(2026, 9, 20, 10, 0, 24).astimezone()

        self.assertIsNone(completed_job_seconds_per_sheet(started, ended, 1))
        self.assertAlmostEqual(
            0.8,
            completed_job_seconds_per_sheet(started, ended, 20),
        )

    def test_eta_ignores_legacy_queue_wait_rates(self) -> None:
        estimate = estimate_remaining_seconds(
            366,
            [18.0] * 50,
            remaining_jobs=83,
        )

        self.assertEqual("초기 추정", estimate.confidence)
        self.assertEqual("약 14~29분", estimate.display_text)
        self.assertLess(estimate.seconds_high, 40 * 60)

    def test_credential_target_never_contains_address_or_secret(self) -> None:
        target = credential_target("http://192.168.0.248:8000")
        self.assertTrue(target.startswith("QuickPrinter/CanonRemoteUI/"))
        self.assertNotIn("192.168", target)


if __name__ == "__main__":
    unittest.main()
