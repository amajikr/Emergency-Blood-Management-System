"""Schema-level checks for additive donor services migration."""
import tempfile
import unittest
from pathlib import Path
import db
import final_phase

class DonorServiceSchemaTests(unittest.TestCase):
    def test_additive_schema_and_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"test.db"
            conn=db.connect(str(path))
            try:
                db.init_db(conn)
                final_phase.ensure_schema(conn)
                final_phase.ensure_schema(conn)  # idempotency
                tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"donor","donor_screening","donor_token","service_pricing"}.issubset(tables))
                prices=conn.execute("SELECT COUNT(*) FROM service_pricing").fetchone()[0]
                self.assertEqual(prices,2)
            finally: conn.close()

if __name__ == "__main__": unittest.main()
