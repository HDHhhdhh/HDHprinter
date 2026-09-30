import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from unit_print_device_scheduler import prepare_dispatch_copy, remove_dispatch_copy


class DispatchCleanupTests(unittest.TestCase):
    def test_verified_copy_removed_without_touching_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "원본.pdf"
            original.write_bytes(b"source bytes")
            spool = root / "spool"
            copy = prepare_dispatch_copy(original, spool, "0123456789ABCDEF")
            self.assertTrue(remove_dispatch_copy(copy, spool_dir=spool))
            self.assertFalse(copy.exists())
            self.assertEqual(b"source bytes", original.read_bytes())

    def test_source_and_neighbor_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spool = root / "spool"
            spool.mkdir()
            paths = (spool / "original.pdf", root / "QP_0123456789ABCDEF_exam.pdf")
            for path in paths:
                path.write_bytes(b"must remain")
                self.assertFalse(remove_dispatch_copy(path, spool_dir=spool))
                self.assertEqual(b"must remain", path.read_bytes())

    def test_resolved_parent_and_link_are_checked_before_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "QP_0123456789ABCDEF_exam.pdf"
            target.write_bytes(b"must remain")
            with patch.object(Path, "is_symlink", return_value=True):
                self.assertFalse(remove_dispatch_copy(target, spool_dir=root))
            self.assertTrue(target.exists())
            outside = root / "spool" / ".." / target.name
            self.assertFalse(remove_dispatch_copy(outside, spool_dir=root / "spool"))
            self.assertTrue(target.exists())

    def test_cleanup_error_does_not_turn_completion_into_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "QP_0123456789ABCDEF_exam.pdf"
            with patch.object(Path, "unlink", side_effect=PermissionError("busy")):
                self.assertFalse(remove_dispatch_copy(target, spool_dir=root))


if __name__ == "__main__":
    unittest.main()
