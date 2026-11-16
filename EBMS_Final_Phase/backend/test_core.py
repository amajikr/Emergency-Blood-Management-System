"""Tests for the core logic. Run: python -m unittest test_core -v (from backend/)"""
import os
import sqlite3
import tempfile
import threading
import unittest

import banker
import db
import engine
from rwlock import RWLock
from seed import seed


class DBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "t.db"))
        seed(self.conn, demo=False)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]


class EngineTests(DBTestCase):
    def test_matching_respects_compatibility_and_earliest_expiry(self):
        units = engine.matching_units(self.conn, "RBC", "O-")
        self.assertTrue(units)
        self.assertTrue(all(u["blood_group_id"] == "O-" for u in units))  # O- recipients: O- donors only
        expiries = [u["expiry_date"] for u in units]
        self.assertEqual(expiries, sorted(expiries))
        self.assertTrue(all(e >= engine.today() for e in expiries))  # expired units excluded

        rid = engine.create_request(self.conn, 1, "RBC", "O-", 1, "ROUTINE")
        result = engine.reserve_for_request(self.conn, rid)
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(result["units"], [units[0]["unit_id"]])  # earliest expiry picked

    def test_expired_units_are_never_selected_and_monitor_records_them(self):
        result = engine.run_expiry_monitor(self.conn)
        self.assertEqual(result["expired"], 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM blood_unit WHERE status='EXPIRED'"), 2)
        self.assertGreaterEqual(self.count("SELECT COUNT(*) FROM audit_event WHERE event_type='EXPIRE'"), 2)

    def test_no_double_reservation(self):
        n = len(engine.matching_units(self.conn, "RBC", "AB+"))
        first = engine.create_request(self.conn, 1, "RBC", "AB+", n // 2 + 1, "URGENT")
        second = engine.create_request(self.conn, 2, "RBC", "AB+", n // 2 + 1, "URGENT")
        self.assertEqual(engine.reserve_for_request(self.conn, first)["state"], "COMPLETED")
        # not enough left for the second request: it must wait, not steal units
        self.assertEqual(engine.reserve_for_request(self.conn, second)["state"], "WAITING")
        reserved = [r["unit_id"] for r in self.conn.execute("SELECT unit_id FROM reservation WHERE status='ACTIVE'")]
        self.assertEqual(len(reserved), len(set(reserved)))
        # a direct double-booking attempt is blocked by the database itself
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO reservation (request_id, unit_id) VALUES (?,?)", (second, reserved[0]))

    def test_intake_wakes_waiting_request_and_it_completes(self):
        n = len(engine.matching_units(self.conn, "RBC", "AB+"))
        rid = engine.create_request(self.conn, 1, "RBC", "AB+", n // 2 + 1, "URGENT")
        engine.create_request(self.conn, 1, "RBC", "AB+", n - (n // 2 + 1) + 1, "URGENT")
        engine.reserve_for_request(self.conn, rid)
        blocked = engine.create_request(self.conn, 2, "RBC", "AB+", n - (n // 2 + 1) + 1, "URGENT")
        self.assertEqual(engine.reserve_for_request(self.conn, blocked)["state"], "WAITING")
        intake = engine.intake_donation(self.conn, "O-", "RBC", 2, "Fridge A")
        self.assertEqual(intake["woken"], 1)
        self.assertEqual(engine.run_tick(self.conn)["state"], "COMPLETED")

    def test_failed_reservation_leaves_no_partial_state(self):
        before = self.count("SELECT COUNT(*) FROM blood_unit WHERE status='AVAILABLE'")
        rid = engine.create_request(self.conn, 1, "RBC", "O-", 10000, "CRITICAL")
        result = engine.reserve_for_request(self.conn, rid)
        self.assertEqual(result["state"], "WAITING")
        self.assertEqual(result["reason"], "unavailable inventory")
        self.assertEqual(self.count("SELECT COUNT(*) FROM blood_unit WHERE status='AVAILABLE'"), before)
        self.assertEqual(self.count("SELECT COUNT(*) FROM reservation WHERE request_id=?", rid), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_event WHERE event_type='ROLLBACK' AND txn_id=?",
                                    result["txn"]), 1)

    def test_reservation_writes_audit_trail(self):
        rid = engine.create_request(self.conn, 1, "RBC", "O-", 1, "ROUTINE")
        result = engine.reserve_for_request(self.conn, rid)
        self.assertGreaterEqual(
            self.count("SELECT COUNT(*) FROM audit_event WHERE txn_id=? AND event_type='RESERVE'", result["txn"]), 1)

    def test_old_normal_never_overtakes_fresh_critical(self):
        old = engine.create_request(self.conn, 1, "RBC", "O-", 1, "ROUTINE")
        for _ in range(35):  # filler arrivals advance the logical clock
            self.conn.execute(
                "INSERT INTO hospital_request (hospital_id, component_id, required_group_id, quantity, urgency, "
                "arrival_seq, process_state) VALUES (1,'RBC','O-',1,'ROUTINE',"
                "(SELECT MAX(arrival_seq)+1 FROM hospital_request),'NEW')")
        engine.create_request(self.conn, 1, "RBC", "O-", 1, "CRITICAL")
        critical = self.conn.execute("SELECT request_id FROM hospital_request WHERE urgency='CRITICAL' ORDER BY arrival_seq DESC LIMIT 1").fetchone()[0]
        self.assertEqual(engine.pick_next(self.conn), critical)

    def test_fresh_critical_wins_without_aging(self):
        engine.create_request(self.conn, 1, "RBC", "O-", 1, "ROUTINE")
        critical = engine.create_request(self.conn, 1, "RBC", "O-", 1, "CRITICAL")
        self.assertEqual(engine.pick_next(self.conn), critical)


class BankerTests(unittest.TestCase):
    def test_textbook_state_is_safe(self):
        available = {"A": 3, "B": 3, "C": 2}
        allocation = {"P0": {"A": 0, "B": 1, "C": 0}, "P1": {"A": 2, "B": 0, "C": 0},
                      "P2": {"A": 3, "B": 0, "C": 2}, "P3": {"A": 2, "B": 1, "C": 1}, "P4": {"A": 0, "B": 0, "C": 2}}
        need = {"P0": {"A": 7, "B": 4, "C": 3}, "P1": {"A": 1, "B": 2, "C": 2}, "P2": {"A": 6, "B": 0, "C": 0},
                "P3": {"A": 0, "B": 1, "C": 1}, "P4": {"A": 4, "B": 3, "C": 1}}
        safe, seq = banker.is_safe(available, allocation, need)
        self.assertTrue(safe)
        self.assertEqual(sorted(seq), ["P0", "P1", "P2", "P3", "P4"])

    def test_unsafe_request_is_denied(self):
        available = {"RBC": 1}
        allocation = {"p1": {"RBC": 1}, "p2": {"RBC": 1}}
        need = {"p1": {"RBC": 2}, "p2": {"RBC": 2}}
        granted, reason, _ = banker.request_resources("p1", {"RBC": 1}, available, allocation, need)
        self.assertFalse(granted)
        self.assertEqual(reason, "unsafe Banker's state")

    def test_safe_request_is_granted_with_sequence(self):
        granted, reason, seq = banker.request_resources(
            "p1", {"RBC": 1}, {"RBC": 2}, {"p1": {"RBC": 0}}, {"p1": {"RBC": 1}})
        self.assertTrue(granted)
        self.assertIsNone(reason)
        self.assertEqual(seq, ["p1"])

    def test_cycle_detection_and_victim(self):
        cycle = banker.find_cycle({"a": ["b"], "b": ["c"], "c": ["a"], "d": ["a"]})
        self.assertEqual(set(cycle), {"a", "b", "c"})
        self.assertEqual(banker.find_cycle({"a": ["b"], "b": []}), [])
        self.assertEqual(banker.choose_victim(["a", "b", "c"], {"a": 3, "b": 1, "c": 2}), "b")


class RWLockTests(unittest.TestCase):
    def test_readers_overlap(self):
        lock = RWLock()
        barrier = threading.Barrier(3, timeout=5)  # only passes if all 3 readers hold the lock at once
        errors = []

        def reader():
            try:
                with lock.read():
                    barrier.wait()
            except threading.BrokenBarrierError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_writer_excludes_readers(self):
        lock = RWLock()
        lock.acquire_write()
        got = threading.Event()

        def reader():
            with lock.read():
                got.set()

        t = threading.Thread(target=reader)
        t.start()
        self.assertFalse(got.wait(0.2))
        lock.release_write()
        self.assertTrue(got.wait(2))
        t.join()


if __name__ == "__main__":
    unittest.main()
