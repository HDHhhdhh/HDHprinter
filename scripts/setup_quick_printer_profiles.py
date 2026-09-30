from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from unit_print_printer_domain import derive_profile_names
from unit_print_printer_setup import (
    BaseQueueInspection,
    PowerShellResult,
    ProfileSetupBlocked,
    execute_profile_setup,
    inspect_base_queue,
    plan_profile_setup,
)


class PowerShellRunner:
    def run(self, script: str) -> PowerShellResult:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return PowerShellResult(result.returncode, result.stdout, result.stderr)


def load_printer_names(runner: PowerShellRunner) -> frozenset[str]:
    result = runner.run("Get-Printer | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress")
    if result.return_code != 0:
        raise RuntimeError(result.stderr.strip() or "Windows 프린터 목록을 읽지 못했습니다.")
    payload = json.loads(result.stdout or "[]")
    if isinstance(payload, str):
        payload = [payload]
    return frozenset(str(name) for name in payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the four Quick Printer Canon profile queues.")
    parser.add_argument("--base-printer", default="iR-ADV 8585 III")
    args = parser.parse_args()

    runner = PowerShellRunner()
    profiles = derive_profile_names(args.base_printer)
    discovery = inspect_base_queue(profiles.base_queue, runner)
    if isinstance(discovery, BaseQueueInspection):
        print(
            "기본 큐 확인: "
            f"{discovery.queue_name} | 드라이버={discovery.driver_name} | 포트={discovery.port_name}"
        )
    plan = plan_profile_setup(profiles, discovery, load_printer_names(runner))
    if isinstance(plan, ProfileSetupBlocked):
        print(f"프로필 생성 불가: {plan.reason.value}: {plan.detail}")
        print("관리자 PowerShell인지, Canon 기본 큐/드라이버/포트가 준비됐는지 확인하세요.")
        return 2
    if not plan.commands:
        print("OK: 세로/가로/구분지/A:ble 전용 큐 4개가 이미 준비되어 있습니다.")
        return 0

    result = execute_profile_setup(plan, runner)
    if not result.is_complete:
        print(f"프로필 생성 실패: {result.failed_queue_name}: {result.detail}")
        return 1
    print("OK: " + ", ".join(result.created_queue_names))
    print("각 큐의 Canon 드라이버에 부서 ID/PIN을 저장한 뒤 종이 테스트를 진행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
