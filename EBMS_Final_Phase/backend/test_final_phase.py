"""Final-phase compatibility, analytics, reporting, and additive migration checks."""
import io
import os
import tempfile
import unittest

import db
import engine
import final_phase
from seed import seed


class FinalPhaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "final.db"))
        seed(self.conn, demo=False)
        final_phase.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_additive_migration_is_idempotent_and_preserves_units(self):
        count = self.conn.execute("SELECT COUNT(*) FROM blood_unit").fetchone()[0]
        final_phase.ensure_schema(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM blood_unit").fetchone()[0], count)
        self.assertIn("hospital_id", {r[1] for r in self.conn.execute("PRAGMA table_info(blood_unit)")})
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_component_compatibility_is_not_shared(self):
        rbc = set(final_phase.compatible_groups(self.conn, "RBC", "A+"))
        plasma = set(final_phase.compatible_groups(self.conn, "FFP", "A+"))
        platelets = set(final_phase.compatible_groups(self.conn, "PLT", "A+"))
        self.assertIn("O-", rbc)
        self.assertIn("AB+", plasma)
        self.assertNotIn("O-", plasma)
        self.assertIn("O+", platelets)
        self.assertNotEqual(rbc, plasma)

    def test_dashboard_and_analytics_are_database_backed(self):
        d = final_phase.dashboard(self.conn)
        self.assertEqual(d["units"]["total"], self.conn.execute("SELECT COUNT(*) FROM blood_unit").fetchone()[0])
        self.assertEqual(d["hospitals"], 8)
        self.assertEqual(len(d["groups"]), 8)
        a = final_phase.analytics(self.conn)
        self.assertTrue({"inventory", "expiring", "donations", "requests", "hospitals"}.issubset(a))

    def test_pdf_is_valid_and_paginated(self):
        rows = [{"unit_id": i, "status": "AVAILABLE"} for i in range(100)]
        pdf = final_phase.make_pdf("Inventory report", rows, ["unit_id", "status"])
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("optional pypdf validator is not installed")
        reader = PdfReader(io.BytesIO(pdf))
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertIn("Page 1 of", reader.pages[0].extract_text())

    def test_migrated_empty_database_can_be_seeded(self):
        p = os.path.join(self.tmp.name, "fresh-after-migrate.db")
        conn = db.connect(p)
        from migrate import migrate
        migrate(conn)
        self.assertTrue(seed(conn, demo=False))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM hospital").fetchone()[0], 8)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM blood_unit").fetchone()[0], 0)
        conn.close()

    def test_strict_priority_then_fifo(self):
        normal = engine.create_request(self.conn, 1, "RBC", "O-", 1, "SCHEDULE")
        engine.create_request(self.conn, 1, "RBC", "O-", 1, "URGENT")
        critical = engine.create_request(self.conn, 1, "RBC", "O-", 1, "CRITICAL")
        self.assertEqual(engine.pick_next(self.conn), critical)
        # Within equal priority, arrival order wins.
        next_critical = engine.create_request(self.conn, 1, "RBC", "O-", 1, "CRITICAL")
        self.assertEqual(engine.pick_next(self.conn), critical)
        self.assertNotEqual(normal, critical)
        self.assertGreater(next_critical, critical)


if __name__ == "__main__":
    unittest.main()

class ApiFinalPhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import app as app_module
            import werkzeug  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"Flask/Werkzeug not installed: {exc}")
        cls.app_module = app_module

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "api.db")
        self.saved_db = db.DEFAULT_DB
        self.saved_auth = self.app_module.AUTH_REQUIRED
        db.DEFAULT_DB = self.path
        self.app_module.AUTH_REQUIRED = True
        self.app_module._SCHEMA_READY.clear()
        conn = db.connect(self.path)
        seed(conn, demo=False)
        from migrate import migrate
        migrate(conn)
        conn.close()
        self.app_module.app.config.update(TESTING=True, SECRET_KEY="test-only-secret")
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        db.DEFAULT_DB = self.saved_db
        self.app_module.AUTH_REQUIRED = self.saved_auth
        self.tmp.cleanup()

    def admin_login(self):
        self.assertEqual(self.client.post("/api/auth/setup", json={"username":"admin","password":"long-test-password-123"}).status_code,201)
        self.assertEqual(self.client.post("/api/auth/login", json={"username":"admin","password":"long-test-password-123"}).status_code,200)

    def test_login_is_required_and_hospital_role_is_scoped(self):
        self.assertEqual(self.client.get("/api/dashboard").status_code,401)
        self.admin_login()
        meta=self.client.get("/api/meta").get_json(); hid=meta["hospitals"][0]["id"]
        user=self.client.post("/api/users",json={"username":"hospital-a","password":"hospital-password-123","role":"HOSPITAL","hospital_id":hid})
        self.assertEqual(user.status_code,201)
        hospital=self.app_module.app.test_client()
        self.assertEqual(hospital.post("/api/auth/login",json={"username":"hospital-a","password":"hospital-password-123"}).status_code,200)
        created=hospital.post("/api/requests",json={"hospital_id":999,"component_id":"RBC","required_group_id":"O-","quantity":1,"urgency":"CRITICAL"})
        self.assertEqual(created.status_code,201)
        conn=db.connect(self.path)
        actual=conn.execute("SELECT hospital_id FROM hospital_request WHERE request_id=?",(created.get_json()["request_id"],)).fetchone()[0]
        conn.close()
        self.assertEqual(actual,hid)
        self.assertEqual(hospital.post("/api/donations",json={"blood_group_id":"O-","component_id":"RBC","count":1,"location":"Fridge"}).status_code,403)
        self.assertEqual(hospital.get("/api/audit").status_code,403)

    def test_admin_can_create_and_update_hospital(self):
        self.admin_login()
        created=self.client.post("/api/hospitals",json={"name":"Test Hospital","location":"Central"})
        self.assertEqual(created.status_code,201)
        hid=created.get_json()["hospital_id"]
        updated=self.client.patch(f"/api/hospitals/{hid}",json={"name":"Test Hospital North","location":"North Wing"})
        self.assertEqual(updated.status_code,200)
        self.assertIn("Test Hospital North",[h["name"] for h in self.client.get("/api/hospitals").get_json()])

    def test_transfer_transaction_and_pdf_report(self):
        self.admin_login()
        hospitals=self.client.get("/api/meta").get_json()["hospitals"]
        src,dst=hospitals[0]["id"],hospitals[1]["id"]
        donated=self.client.post("/api/donations",json={"blood_group_id":"O-","component_id":"RBC","count":2,"location":"Cold room","hospital_id":src})
        self.assertEqual(donated.status_code,201)
        payload={"source_hospital_id":src,"destination_hospital_id":dst,"component_id":"RBC","blood_group_id":"O-","quantity":1}
        created=self.client.post("/api/transfers",json=payload)
        self.assertEqual(created.status_code,201)
        self.assertEqual(self.client.post("/api/transfers",json=payload).status_code,400)
        tid=created.get_json()["transfer_id"]
        for status in ("APPROVED","IN_TRANSIT","COMPLETED"):
            self.assertEqual(self.client.patch(f"/api/transfers/{tid}",json={"status":status}).status_code,200)
        pdf=self.client.get("/api/reports/inventory?group=O-")
        self.assertEqual(pdf.status_code,200)
        self.assertEqual(pdf.mimetype,"application/pdf")
