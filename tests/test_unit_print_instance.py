from __future__ import annotations

import unittest

from unit_print_instance import (
    InstanceLockError, SingleInstance, WAIT_ABANDONED, WAIT_FAILED,
    WAIT_OBJECT_0, WAIT_TIMEOUT, instance_mutex_name,
)


class FakeMutexAPI:
    def __init__(self, result=WAIT_OBJECT_0):
        self.result = result
        self.created = []
        self.closed = []
        self.released = []

    def user_sid(self):
        return "S-1-5-21-111-222-333-1001"

    def create(self, name):
        self.created.append(name)
        return len(self.created)

    def wait(self, handle):
        return self.result

    def release(self, handle):
        self.released.append(handle)

    def close(self, handle):
        self.closed.append(handle)


class InstanceTests(unittest.TestCase):
    def test_name_is_global_user_identity_without_install_path_or_session_id(self):
        api = FakeMutexAPI()
        first = SingleInstance(api)
        second = SingleInstance(FakeMutexAPI())
        self.assertEqual("Global\\QuickPrinter.UnitPrint.S-1-5-21-111-222-333-1001", first.name)
        self.assertEqual(first.name, second.name)
        self.assertNotEqual(first.name, instance_mutex_name("S-1-5-21-111-222-333-1002"))

    def test_invalid_identity_does_not_weaken_to_path_or_local_mutex(self):
        for sid in ("", "username", "S-1\\override", "S-1-5-21\n"):
            with self.subTest(sid=sid), self.assertRaises(InstanceLockError):
                instance_mutex_name(sid)

    def test_lease_keeps_handle_until_close_and_releases_once(self):
        api = FakeMutexAPI()
        lease = SingleInstance(api)
        self.assertTrue(lease.acquire())
        self.assertEqual([], api.closed)
        self.assertEqual([], api.released)
        lease.close()
        lease.close()
        self.assertEqual([1], api.released)
        self.assertEqual([1], api.closed)

    def test_duplicate_only_closes_its_handle_never_releases_original_owner(self):
        api = FakeMutexAPI(WAIT_TIMEOUT)
        lease = SingleInstance(api)
        self.assertFalse(lease.acquire())
        lease.close()
        self.assertEqual([1], api.closed)
        self.assertEqual([], api.released)

    def test_abandoned_mutex_result_acquires_new_lifetime_ownership(self):
        api = FakeMutexAPI(WAIT_ABANDONED)
        lease = SingleInstance(api)
        self.assertTrue(lease.acquire())
        self.assertEqual(1, lease.handle)
        lease.close()
        self.assertEqual([1], api.released)

    def test_os_wait_failure_closes_handle_and_fails_closed(self):
        for result in (WAIT_FAILED, 17):
            api = FakeMutexAPI(result)
            with self.subTest(result=result), self.assertRaises(InstanceLockError):
                SingleInstance(api).acquire()
            self.assertEqual([1], api.closed)
            self.assertEqual([], api.released)

    def test_access_denial_never_treated_as_free_instance(self):
        api = FakeMutexAPI()
        api.create = lambda name: (_ for _ in ()).throw(PermissionError("denied"))
        with self.assertRaises(PermissionError):
            SingleInstance(api).acquire()
        self.assertEqual([], api.closed)

    def test_reentrant_acquire_same_lease_is_rejected(self):
        api = FakeMutexAPI()
        lease = SingleInstance(api)
        lease.acquire()
        with self.assertRaises(InstanceLockError):
            lease.acquire()
        self.assertEqual(1, len(api.created))
        lease.close()

    def test_close_releases_handle_even_if_release_api_fails(self):
        api = FakeMutexAPI()
        lease = SingleInstance(api)
        lease.acquire()
        api.release = lambda handle: (_ for _ in ()).throw(OSError("failed"))
        with self.assertRaises(OSError):
            lease.close()
        self.assertEqual([1], api.closed)
        self.assertIsNone(lease.handle)


if __name__ == "__main__":
    unittest.main()
