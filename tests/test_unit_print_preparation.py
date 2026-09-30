from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from unit_print_preparation import (
    MATERIAL_BUILD_PRIORITY,
    PRINT_PREPARATION_PRIORITY,
    OrderedCommitQueue,
    PrintPreparationService,
    PriorityWorkScheduler,
)


def job(path: Path, pages_per_sheet: int = 4):
    return SimpleNamespace(item=SimpleNamespace(path=path), pages_per_sheet=pages_per_sheet)


class PrintPreparationServiceTests(unittest.TestCase):
    def test_ordered_commit_queue_waits_for_earlier_request(self) -> None:
        commits = OrderedCommitQueue()
        order: list[str] = []
        slow = commits.reserve()
        fast = commits.reserve()

        commits.complete(fast, lambda: order.append("fast"))
        self.assertEqual([], order)

        commits.complete(slow, lambda: order.append("slow"))
        self.assertEqual(["slow", "fast"], order)

    def test_print_preparation_overtakes_queued_material_work(self) -> None:
        scheduler = PriorityWorkScheduler()
        started = threading.Event()
        release = threading.Event()
        order: list[str] = []

        def blocking() -> str:
            started.set()
            release.wait(1)
            order.append("running")
            return "running"

        running = scheduler.submit(blocking, priority=MATERIAL_BUILD_PRIORITY)
        self.assertTrue(started.wait(1))
        material = scheduler.submit(
            lambda: order.append("material") or "material",
            priority=MATERIAL_BUILD_PRIORITY,
        )
        printing = scheduler.submit(
            lambda: order.append("print") or "print",
            priority=PRINT_PREPARATION_PRIORITY,
        )
        release.set()

        self.assertEqual("running", running.result(1))
        self.assertEqual("print", printing.result(1))
        self.assertEqual("material", material.result(1))
        scheduler.close(wait=True)
        self.assertEqual(["running", "print", "material"], order)

    def test_prefetch_is_bounded_to_next_two_and_resolve_keeps_requested_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index in range(4):
                path = root / f"{index}.pdf"
                path.write_bytes(str(index).encode())
                paths.append(path)
            calls = []
            release = threading.Event()

            def prepare(path, cache_dir):
                calls.append(path.name)
                if path.name == "0.pdf":
                    release.wait(1)
                result = cache_dir / (path.stem + "-ready.pdf")
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_bytes(path.read_bytes())
                return result

            service = PrintPreparationService(root / "cache", four_up=prepare)
            jobs = [job(path) for path in paths]
            service.prefetch_jobs(jobs, limit=2)
            self.assertEqual(["0.pdf"], calls)
            release.set()
            current = service.resolve_job(jobs[0])
            second = service.resolve_job(jobs[1])
            service.close()

            self.assertEqual(b"0", current.read_bytes())
            self.assertEqual(b"1", second.read_bytes())
            self.assertNotIn("3.pdf", calls)

    def test_one_up_never_enters_background_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(b"pdf")
            calls = []
            service = PrintPreparationService(
                Path(directory) / "cache",
                four_up=lambda source, cache: calls.append(source) or source,
            )

            resolved = service.resolve_job(job(path, 1))
            service.close()

            self.assertEqual(path, resolved)
            self.assertEqual([], calls)

    def test_source_changed_during_preparation_is_not_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"before")
            started = threading.Event()
            release = threading.Event()

            def prepare(path, cache_dir):
                started.set()
                release.wait(1)
                result = cache_dir / "ready.pdf"
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_bytes(path.read_bytes())
                return result

            service = PrintPreparationService(root / "cache", four_up=prepare)
            errors: list[BaseException] = []

            def resolve() -> None:
                try:
                    service.resolve_job(job(source))
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=resolve)
            worker.start()
            self.assertTrue(started.wait(1))
            source.write_bytes(b"after-and-different")
            release.set()
            worker.join(1)
            service.close()

            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], OSError)


if __name__ == "__main__":
    unittest.main()
