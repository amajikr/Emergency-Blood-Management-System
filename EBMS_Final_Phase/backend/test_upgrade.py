"""Hospital list, urgency (ROUTINE -> SCHEDULE) mapping, and reservation gating through the C Banker's check.
Run from backend/:  python -m unittest test_upgrade -v"""
import os
import shutil
import sqlite3
import tempfile
import unittest

import db
import engine
import migrate
import os_bridge
from seed import HOSPITAL_NAMES, seed

ENGINE_BUILT = os.path.isfile(os_bridge.DEFAULT_EXE)


class HospitalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "h.db")
        conn = db.connect(self.path)
        seed(conn, demo=False)
        # recreate the pre-upgrade demo state: three original names, requests referencing them
        conn.execute("UPDATE hospital SET name='City General' WHERE hospital_id=1")
        conn.execute("UPDATE hospital SET name='St. Mary Emergency' WHERE hospital_id=2")
        conn.execute("UPDATE hospital SET name='Riverside Clinic' WHERE hospital_id=3")
        conn.execute("DELETE FROM hospital WHERE hospital_id > 3")
        engine.create_request(conn, 1, "RBC", "O-", 1, "ROUTINE")
        engine.create_request(conn, 3, "FFP", "AB+", 1, "CRITICAL")
        self.before = self.counts(conn)
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def counts(self, conn):
        q = lambda s: conn.execute(s).fetchone()[0]
        return {"requests": q("SELECT COUNT(*) FROM hospital_request"),
                "reservations": q("SELECT COUNT(*) FROM reservation"),
                "audit": q("SELECT COUNT(*) FROM audit_event"),
                "units": q("SELECT COUNT(*) FROM blood_unit")}

    def test_migration_renames_in_place_and_adds_the_rest(self):
        conn = db.connect(self.path)
        summary = migrate.migrate(conn)
        names = [r["name"] for r in conn.execute("SELECT name FROM hospital ORDER BY hospital_id")]
        self.assertEqual(sorted(names), sorted(HOSPITAL_NAMES))
        self.assertEqual(len(summary["renamed"]), 3)
        self.assertEqual(len(summary["inserted"]), 5)
        self.assertEqual(conn.execute("SELECT name FROM hospital WHERE hospital_id=1").fetchone()[0],
                         "Shri Mahant Indiresh Hospital")
        conn.close()

    def test_all_eight_names_present_and_spelled_exactly(self):
        conn = db.connect(self.path)
        migrate.migrate(conn)
        names = {r["name"] for r in conn.execute("SELECT name FROM hospital")}
        self.assertEqual(len(HOSPITAL_NAMES), 8)
        self.assertIn("Graphic Era Hospital", names)
        self.assertIn("Velmed Hospital", names)
        self.assertIn("Kanishk Surgical & Super Specialty Hospital", names)
        self.assertNotIn("Wilmed Hospital", names)
        self.assertNotIn("City General", names)
        self.assertEqual(migrate.verify(conn), ([], [], []))
        conn.close()

    def test_migration_is_idempotent_and_keeps_user_data(self):
        conn = db.connect(self.path)
        migrate.migrate(conn)
        first = self.counts(conn)
        second = migrate.migrate(conn)
        self.assertEqual(second["renamed"], [])
        self.assertEqual(second["inserted"], [])
        self.assertEqual(self.counts(conn), first)
        self.assertEqual(first, self.before)  # no requests, reservations, audit or units lost
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM hospital").fetchone()[0], 8)
        conn.close()

    def test_rename_history_keeps_original_names(self):
        conn = db.connect(self.path)
        migrate.migrate(conn)
        rows = conn.execute("SELECT hospital_id, old_name, new_name FROM hospital_name_history ORDER BY hospital_id").fetchall()
        self.assertEqual([(r["hospital_id"], r["old_name"]) for r in rows],
                         [(1, "City General"), (2, "St. Mary Emergency"), (3, "Riverside Clinic")])
        conn.close()

    def test_fresh_seed_has_exactly_the_eight_hospitals(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = db.connect(os.path.join(tmp.name, "fresh.db"))
        seed(conn, demo=True)
        self.assertEqual(migrate.verify(conn), ([], [], []))
        conn.close()

    def test_shipped_database_copy_verifies(self):
        shipped = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ebms.db")
        if not os.path.isfile(shipped):
            self.skipTest("no shipped database")
        copy = os.path.join(self.tmp.name, "shipped.db")
        shutil.copy(shipped, copy)
        self.assertEqual(migrate.main(["--path", copy]), 0)
        conn = db.connect(copy)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()


class UrgencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "u.db"))
        seed(self.conn, demo=False)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_dropdown_options_are_critical_urgent_schedule_only(self):
        self.assertEqual(list(engine.DROPDOWN_URGENCIES), ["CRITICAL", "URGENT", "SCHEDULE"])
        self.assertNotIn("ROUTINE", engine.DROPDOWN_URGENCIES)

    def test_schedule_maps_to_routine_and_is_lowest_weight(self):
        self.assertEqual(engine.normalize_urgency("SCHEDULE"), "ROUTINE")
        self.assertEqual(engine.normalize_urgency("schedule"), "ROUTINE")
        self.assertEqual(engine.URGENCY_WEIGHT["ROUTINE"], 10)
        self.assertLess(engine.URGENCY_WEIGHT["ROUTINE"], engine.URGENCY_WEIGHT["CRITICAL"])
        self.assertEqual(engine.urgency_label("ROUTINE"), "SCHEDULE")

    def test_each_dropdown_option_creates_and_completes(self):
        for label in ("CRITICAL", "URGENT", "SCHEDULE"):
            rid = engine.create_request(self.conn, 1, "PLT", "A+", 1, label)
            stored = self.conn.execute("SELECT urgency FROM hospital_request WHERE request_id=?", (rid,)).fetchone()[0]
            self.assertEqual(stored, engine.normalize_urgency(label))
            if ENGINE_BUILT:
                self.assertEqual(engine.reserve_for_request(self.conn, rid)["state"], "COMPLETED")

    def test_unknown_urgency_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.create_request(self.conn, 1, "RBC", "O-", 1, "BOGUS")
        with self.assertRaises(ValueError):
            engine.normalize_urgency("")

    def test_legacy_routine_rows_still_work(self):
        rid = engine.create_request(self.conn, 1, "RBC", "O-", 1, "ROUTINE")
        self.assertIsNotNone(rid)


class ReservationGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "g.db"))
        seed(self.conn, demo=False)
        self._old = os.environ.get("EBMS_OS_BIN")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("EBMS_OS_BIN", None)
        else:
            os.environ["EBMS_OS_BIN"] = self._old
        self.conn.close()
        self.tmp.cleanup()

    def test_missing_engine_never_approves_a_reservation(self):
        os.environ["EBMS_OS_BIN"] = os.path.join(self.tmp.name, "no_such_ebms_os")
        rid = engine.create_request(self.conn, 1, "RBC", "O-", 1, "CRITICAL")
        result = engine.reserve_for_request(self.conn, rid)
        self.assertEqual(result["state"], "WAITING")
        self.assertEqual(result["reason"], "banker check unavailable")
        self.assertEqual(result["units"], [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reservation WHERE request_id=?", (rid,)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM blood_unit WHERE status='RESERVED'").fetchone()[0], 0)
        audit = self.conn.execute(
            "SELECT reason FROM audit_event WHERE event_type='ROLLBACK' AND txn_id=?", (result["txn"],)).fetchone()
        self.assertEqual(audit["reason"], "banker check unavailable")

    @unittest.skipUnless(ENGINE_BUILT, "C engine not built")
    def test_granted_reservation_goes_through_the_c_check(self):
        rid = engine.create_request(self.conn, 1, "RBC", "O-", 1, "CRITICAL")
        result = engine.reserve_for_request(self.conn, rid)
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(len(result["units"]), 1)


class ApiSmokeTests(unittest.TestCase):
    """Route-level checks through Flask's test client on a throwaway database."""

    @classmethod
    def setUpClass(cls):
        cls.saved_auth = os.environ.get("EBMS_AUTH_REQUIRED")
        os.environ["EBMS_AUTH_REQUIRED"] = "0"  # API smoke tests exercise the legacy demo profile
        try:
            import app as app_module
        except ImportError as exc:  # Flask not installed in this interpreter
            raise unittest.SkipTest(f"Flask is not available: {exc}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved_db = db.DEFAULT_DB
        db.DEFAULT_DB = os.path.join(cls.tmp.name, "api.db")
        conn = db.connect()
        seed(conn, demo=True)
        conn.close()
        cls.app = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "saved_db"):
            db.DEFAULT_DB = cls.saved_db
            if cls.saved_auth is None: os.environ.pop("EBMS_AUTH_REQUIRED", None)
            else: os.environ["EBMS_AUTH_REQUIRED"] = cls.saved_auth
            cls.tmp.cleanup()

    def test_meta_lists_eight_hospitals_and_dropdown(self):
        data = self.app.get("/api/meta").get_json()
        self.assertEqual(sorted(h["name"] for h in data["hospitals"]), sorted(HOSPITAL_NAMES))
        self.assertEqual(data["urgencies"], ["CRITICAL", "URGENT", "SCHEDULE"])

    def test_requests_show_routine_as_schedule(self):
        rows = self.app.get("/api/requests").get_json()
        labels = {r["urgency"] for r in rows}
        self.assertNotIn("ROUTINE", labels)
        self.assertIn("SCHEDULE", labels)

    def test_create_request_with_schedule_returns_201(self):
        r = self.app.post("/api/requests", json={"hospital_id": 2, "component_id": "PLT",
                                                 "required_group_id": "A+", "quantity": 1, "urgency": "SCHEDULE"})
        self.assertEqual(r.status_code, 201)
        bad = self.app.post("/api/requests", json={"hospital_id": 2, "component_id": "PLT",
                                                   "required_group_id": "A+", "quantity": 1, "urgency": "NOPE"})
        self.assertEqual(bad.status_code, 400)

    def test_os_status_reports_built_flag_only(self):
        data = self.app.get("/api/os/status").get_json()
        self.assertIn("built", data)
        self.assertNotIn("/", str(data.get("modules")))

    def test_os_routes_validate_input_with_400(self):
        r = self.app.post("/api/os/scheduler", json={"algorithm": "RR", "processes": []})
        self.assertIn(r.status_code, (400, 503))
        r = self.app.post("/api/os/scheduler", json={"algorithm": "FCFS", "processes": "nope"})
        self.assertEqual(r.status_code, 400)
        r = self.app.post("/api/os/deadlock", json={"processes": ["P1"], "edges": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.app.post("/api/os/readers-writers", json={"readers": 99, "writers": 1,
                                                           "reads_per_reader": 1, "writes_per_writer": 1})
        self.assertIn(r.status_code, (400, 503))

    @unittest.skipUnless(ENGINE_BUILT, "C engine not built")
    def test_os_routes_return_real_results(self):
        r = self.app.post("/api/os/scheduler", json={
            "algorithm": "SRTF", "processes": [{"pid": "P1", "arrival": 0, "burst": 8},
                                               {"pid": "P2", "arrival": 1, "burst": 4},
                                               {"pid": "P3", "arrival": 2, "burst": 9},
                                               {"pid": "P4", "arrival": 3, "burst": 5}]})
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.get_json()["averages"]["waiting"], 6.5)
        r = self.app.post("/api/os/banker", json={"available": [3, 3, 2],
                                                  "max": [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]],
                                                  "allocation": [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]],
                                                  "request": {"process": 4, "vector": [3, 3, 0]}})
        self.assertEqual(r.get_json()["request"]["code"], "REJECTED_UNSAFE")
        r = self.app.post("/api/os/deadlock", json={"processes": ["P1", "P2"],
                                                    "edges": [{"from": "P1", "to": "P2"}, {"from": "P2", "to": "P1"}]})
        self.assertTrue(r.get_json()["deadlock"])
        r = self.app.post("/api/os/producer-consumer", json={"capacity": 2, "producers": 2, "consumers": 2,
                                                             "items_per_producer": 50})
        self.assertTrue(r.get_json()["exactly_once"])
        r = self.app.post("/api/os/readers-writers", json={"readers": 2, "writers": 1,
                                                           "reads_per_reader": 5, "writes_per_writer": 3})
        self.assertTrue(r.get_json()["all_operations_completed"])

    def test_missing_engine_is_503_and_hides_paths(self):
        old = os.environ.get("EBMS_OS_BIN")
        os.environ["EBMS_OS_BIN"] = "/nonexistent/secret/ebms_os"
        try:
            r = self.app.post("/api/os/scheduler", json={"algorithm": "FCFS",
                                                         "processes": [{"pid": "P1", "burst": 1}]})
            self.assertEqual(r.status_code, 503)
            self.assertNotIn("secret", r.get_data(as_text=True))
            b = self.app.get("/api/banker").get_json()
            self.assertIsNone(b["safe"])
            self.assertIn("error", b)
        finally:
            if old is None:
                os.environ.pop("EBMS_OS_BIN", None)
            else:
                os.environ["EBMS_OS_BIN"] = old


if __name__ == "__main__":
    unittest.main()
