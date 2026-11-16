"""Upgrade an existing EBMS database to the eight-hospital list. Safe to run any number of times.

What it does (inside one BEGIN IMMEDIATE transaction):
  1. Renames the three original demo hospitals IN PLACE, so hospital_id values do not change and every
     request, reservation, and audit row that points at them stays valid.
  2. Writes each rename to hospital_name_history, so the original name is never lost.
  3. Inserts any of the eight names that are still missing (fresh IDs, no duplicates).
  4. Never deletes rows. Hospitals that are not in the target list are reported, not removed.

Run:  python migrate.py            (uses EBMS_DB or backend/ebms.db)
      python migrate.py --path X   (explicit database file)
"""
import argparse
import sys

import db
import final_phase
from seed import HOSPITAL_NAMES

# original demo name -> target name, matched by position in the original seed data
OLD_TO_NEW = {
    "City General": "Shri Mahant Indiresh Hospital",
    "St. Mary Emergency": "Graphic Era Hospital",
    "Riverside Clinic": "Max Super Speciality Hospital",
}


def _names(conn):
    return {r["name"]: r["hospital_id"] for r in conn.execute("SELECT hospital_id, name FROM hospital")}


def migrate(conn):
    db.init_db(conn)  # idempotent: CREATE ... IF NOT EXISTS
    final_phase.ensure_schema(conn)  # additive final-phase migration, preserves existing rows
    summary = {"renamed": [], "inserted": [], "already_present": [], "unexpected": []}
    with db.transaction(conn):
        names = _names(conn)
        for old, new in OLD_TO_NEW.items():
            if new in names:
                continue
            if old in names:
                hid = names[old]
                conn.execute("UPDATE hospital SET name = ? WHERE hospital_id = ?", (new, hid))
                conn.execute(
                    "INSERT INTO hospital_name_history (hospital_id, old_name, new_name) VALUES (?,?,?)",
                    (hid, old, new),
                )
                summary["renamed"].append((hid, old, new))
        names = _names(conn)
        for new in HOSPITAL_NAMES:
            if new in names:
                summary["already_present"].append(new)
                continue
            cur = conn.execute("INSERT INTO hospital (name, location) VALUES (?, 'Not specified')", (new,))
            summary["inserted"].append((cur.lastrowid, new))
        summary["unexpected"] = sorted(n for n in names if n not in HOSPITAL_NAMES)
    return summary


def verify(conn):
    """Returns (missing, extra, duplicates) relative to the eight-name target list."""
    rows = [r["name"] for r in conn.execute("SELECT name FROM hospital")]
    missing = [n for n in HOSPITAL_NAMES if n not in rows]
    extra = [n for n in rows if n not in HOSPITAL_NAMES]
    duplicates = sorted({n for n in rows if rows.count(n) > 1})
    return missing, extra, duplicates


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=None)
    args = ap.parse_args(argv)
    conn = db.connect(args.path)
    try:
        summary = migrate(conn)
        missing, extra, duplicates = verify(conn)
    finally:
        conn.close()
    for hid, old, new in summary["renamed"]:
        print(f"renamed hospital {hid}: '{old}' -> '{new}'")
    for hid, new in summary["inserted"]:
        print(f"added hospital {hid}: '{new}'")
    print(f"already present: {len(summary['already_present'])}")
    if summary["unexpected"]:
        print("left unchanged (not in target list):", ", ".join(summary["unexpected"]))
    ok = not missing and not extra and not duplicates
    print("hospital list verified" if ok else f"verification problems: missing={missing} extra={extra} dup={duplicates}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
