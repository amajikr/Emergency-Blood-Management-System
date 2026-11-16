"""Load demo data: blood groups, compatibility rules, components, hospitals, units, sample requests."""
from datetime import date, timedelta

import db
import engine

GROUPS = ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"]
# recipient -> compatible donors (simplified academic rule set, per the proposal's assumptions)
COMPAT = {
    "O-": ["O-"], "O+": ["O+", "O-"], "A-": ["A-", "O-"], "A+": ["A+", "A-", "O+", "O-"],
    "B-": ["B-", "O-"], "B+": ["B+", "B-", "O+", "O-"], "AB-": ["A-", "B-", "AB-", "O-"], "AB+": GROUPS,
}
COMPONENTS = [
    ("RBC", "Red blood cells", "2-6 C", 42),
    ("PLT", "Platelets", "20-24 C", 5),
    ("FFP", "Fresh frozen plasma", "-18 C or colder", 365),
]
# The active hospital list. Exactly these eight names; see migrate.py for upgrading an older database.
HOSPITAL_NAMES = [
    "Shri Mahant Indiresh Hospital",
    "Graphic Era Hospital",
    "Max Super Speciality Hospital",
    "Kanishk Surgical & Super Specialty Hospital",
    "Health Line Hospital",
    "Power Life Care Hospital",
    "MS Galaxy Hospital",
    "Velmed Hospital",
]
HOSPITALS = [(name, "Not specified") for name in HOSPITAL_NAMES]
LOCATIONS = {"RBC": "Fridge A", "PLT": "Agitator 1", "FFP": "Freezer 1"}


def _insert_unit(conn, component, group, collected, expiry):
    conn.execute(
        "INSERT INTO blood_unit (component_id, blood_group_id, location, collection_date, expiry_date) "
        "VALUES (?,?,?,?,?)",
        (component, group, LOCATIONS[component], collected.isoformat(), expiry.isoformat()),
    )


def seed(conn, demo=True):
    db.init_db(conn)
    if conn.execute("SELECT COUNT(*) FROM blood_group").fetchone()[0]:
        return False
    today = date.today()
    with db.transaction(conn):
        for g in GROUPS:
            conn.execute("INSERT INTO blood_group VALUES (?,?,?)", (g, g[:-1], g[-1]))
        for recipient, donors in COMPAT.items():
            for d in donors:
                conn.execute("INSERT INTO blood_compatibility VALUES (?,?)", (recipient, d))
        for c in COMPONENTS:
            conn.execute("INSERT INTO component_type VALUES (?,?,?,?)", c)
        existing_hospitals = {r[0] for r in conn.execute("SELECT name FROM hospital")}
        for name, loc in HOSPITALS:
            if name not in existing_hospitals:
                conn.execute("INSERT INTO hospital (name, location) VALUES (?,?)", (name, loc))
        for i, g in enumerate(GROUPS):
            for comp, _, _, shelf in COMPONENTS:
                for k in range(2):
                    age = (i + 3 * k) % shelf
                    collected = today - timedelta(days=age)
                    _insert_unit(conn, comp, g, collected, collected + timedelta(days=shelf))
        # two already-expired RBC units so the expiry monitor has work to do
        for _ in range(2):
            collected = today - timedelta(days=45)
            _insert_unit(conn, "RBC", "O-", collected, collected + timedelta(days=42))
    if demo:
        engine.create_request(conn, 1, "RBC", "O-", 2, "CRITICAL")
        engine.create_request(conn, 2, "FFP", "AB+", 1, "ROUTINE")  # stored value; shown as SCHEDULE
        engine.create_request(conn, 3, "PLT", "A+", 1, "URGENT")
        engine.create_request(conn, 1, "RBC", "B+", 3, "EMERGENCY")
    return True


if __name__ == "__main__":
    conn = db.connect()
    print("seeded" if seed(conn) else "already seeded")
    conn.close()
