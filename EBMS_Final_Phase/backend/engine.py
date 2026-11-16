"""Core DBMS + OS logic: matching, reservation transactions, scheduling, expiry, audit.

Academic simulation only. Not for clinical decisions.
"""
import datetime as dt
import sqlite3
import uuid

import logging
import os

import os_bridge
from db import transaction

log = logging.getLogger("ebms.engine")

URGENCY_WEIGHT = {"CRITICAL": 40, "EMERGENCY": 40, "URGENT": 20, "ROUTINE": 10}
# UI label -> stored value. SCHEDULE is stored as ROUTINE, so existing rows and CHECK constraints keep working.
# SCHEDULE is the lowest urgency (weight 10), never the same weight as CRITICAL (40).
URGENCY_ALIASES = {"SCHEDULE": "ROUTINE"}
DROPDOWN_URGENCIES = ("CRITICAL", "URGENT", "SCHEDULE")  # what the UI shows for new requests
AGING_RATE = 1  # priority points gained per arrival tick spent waiting (aging = starvation fix)
SCHEDULABLE = ("READY", "WAITING")
BANKER_REASON = {
    "REJECTED_EXCEEDS_NEED": "request exceeds declared need",
    "REJECTED_INSUFFICIENT": "insufficient available",
    "REJECTED_UNSAFE": "unsafe Banker's state",
}


class Shortfall(Exception):
    """Raised inside a reservation transaction to force a rollback with a reason."""


def today():
    return dt.date.today().isoformat()


def add_days(iso, days):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def audit(conn, actor, event_type, entity_type, entity_id, txn=None, reason=None, before=None, after=None):
    conn.execute(
        "INSERT INTO audit_event (actor, event_type, entity_type, entity_id, txn_id, reason, before_state, after_state)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (actor, event_type, entity_type, str(entity_id), txn, reason, before, after),
    )


def normalize_urgency(value):
    """Accepts UI labels (CRITICAL, URGENT, SCHEDULE) and stored values (incl. legacy ROUTINE, EMERGENCY)."""
    v = str(value or "").strip().upper()
    v = URGENCY_ALIASES.get(v, v)
    if v not in URGENCY_WEIGHT:
        raise ValueError("urgency must be CRITICAL, URGENT or SCHEDULE")
    return v


def urgency_label(stored):
    """What the UI shows for a stored urgency: ROUTINE is displayed as SCHEDULE."""
    return "SCHEDULE" if stored == "ROUTINE" else stored


def compatible_donors(conn, recipient_group, component_id="RBC"):
    """Component-aware compatibility simulation; verify all allocations clinically."""
    from final_phase import compatible_groups
    return compatible_groups(conn, component_id, recipient_group)


def matching_units(conn, component_id, recipient_group):
    """Compatible, AVAILABLE, non-expired units, earliest expiry first (FEFO)."""
    donors = compatible_donors(conn, recipient_group, component_id)
    if not donors:
        return []
    marks = ",".join("?" * len(donors))
    sql = f"""SELECT unit_id, blood_group_id, expiry_date FROM blood_unit
              WHERE component_id = ? AND status = 'AVAILABLE' AND expiry_date >= ?
                AND blood_group_id IN ({marks})
              ORDER BY expiry_date ASC, collection_date ASC, unit_id ASC"""
    return conn.execute(sql, (component_id, today(), *donors)).fetchall()


def explain_matching(conn, component_id, recipient_group):
    """Query plan for the compatibility query (EXPLAIN QUERY PLAN on SQLite)."""
    donors = compatible_donors(conn, recipient_group)
    marks = ",".join("?" * len(donors)) or "NULL"
    sql = f"""EXPLAIN QUERY PLAN SELECT unit_id FROM blood_unit
              WHERE component_id = ? AND status = 'AVAILABLE' AND expiry_date >= ?
                AND blood_group_id IN ({marks})
              ORDER BY expiry_date, collection_date"""
    return [dict(r) for r in conn.execute(sql, (component_id, today(), *donors))]


def banker_matrices(conn):
    """Available vector, Max and Allocation matrices for the active processes (fed to the C Banker's engine).

    Resource types are component types; each process (request) claims one component type.
    """
    comps = [r["component_id"] for r in conn.execute("SELECT component_id FROM component_type ORDER BY component_id")]
    pos = {c: i for i, c in enumerate(comps)}
    available = {c: 0 for c in comps}
    for r in conn.execute(
        "SELECT component_id, COUNT(*) AS n FROM blood_unit WHERE status='AVAILABLE' AND expiry_date >= ? "
        "GROUP BY component_id",
        (today(),),
    ):
        available[r["component_id"]] = r["n"]
    rows = conn.execute(
        "SELECT request_id, component_id, quantity FROM hospital_request "
        "WHERE process_state IN ('READY','RUNNING','WAITING') ORDER BY request_id"
    ).fetchall()
    rids, maxm, alloc = [], [], []
    for r in rows:
        held = conn.execute(
            "SELECT COUNT(*) FROM reservation WHERE request_id = ? AND status = 'ACTIVE'", (r["request_id"],)
        ).fetchone()[0]
        mx = [0] * len(comps)
        al = [0] * len(comps)
        mx[pos[r["component_id"]]] = r["quantity"]
        al[pos[r["component_id"]]] = held
        rids.append(r["request_id"])
        maxm.append(mx)
        alloc.append(al)
    return comps, [available[c] for c in comps], rids, maxm, alloc


def banker_gate(conn, request_id, component_id, qty):
    """Ask the C Banker's engine whether this request may proceed. Returns (granted, reason, safe_sequence).

    Any engine failure is a denial: a missing or failing check never approves a reservation.
    """
    comps, avail, rids, maxm, alloc = banker_matrices(conn)
    if request_id not in rids:
        return False, "request is not in the banker's view", []
    vec = [qty if c == component_id else 0 for c in comps]
    try:
        res = os_bridge.banker_check(avail, maxm, alloc, process=rids.index(request_id), request=vec)
    except os_bridge.OSBridgeError as exc:
        log.error("banker check failed (%s) for request %s", exc.kind, request_id)
        return False, "banker check unavailable", []
    decision = res.get("request") or {}
    if decision.get("granted") is True:
        return True, None, [rids[i] for i in decision.get("safe_sequence_after", [])]
    return False, BANKER_REASON.get(decision.get("code"), "banker check denied"), []


def create_request(conn, hospital_id, component_id, required_group_id, quantity, urgency, actor="hospital-ui"):
    """Creating a request creates an OS-style process and places it on the ready queue."""
    urgency = normalize_urgency(urgency)
    with transaction(conn):
        arrival = conn.execute("SELECT COALESCE(MAX(arrival_seq), 0) + 1 FROM hospital_request").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO hospital_request (hospital_id, component_id, required_group_id, quantity, urgency, "
            "arrival_seq, process_state) VALUES (?,?,?,?,?,?,'NEW')",
            (hospital_id, component_id, required_group_id, quantity, urgency, arrival),
        )
        rid = cur.lastrowid
        conn.execute("UPDATE hospital_request SET process_state='READY' WHERE request_id=?", (rid,))
        audit(conn, actor, "CREATE_PROCESS", "hospital_request", rid, None, None, "NEW", "READY")
        _history(conn, rid, "PENDING", actor)
        _history(conn, rid, "SCHEDULED", actor)
    return rid


def pick_next(conn):
    """Use the preserved C Priority-NP scheduler for the live queue, with a safe Python fallback."""
    rows = conn.execute(
        "SELECT request_id, urgency, arrival_seq FROM hospital_request WHERE process_state IN ('READY','WAITING')"
    ).fetchall()
    if not rows:
        return None
    ranked = sorted(rows, key=lambda r: (-URGENCY_WEIGHT.get(r["urgency"], 0), r["arrival_seq"], r["request_id"]))
    # Let the same C OS scheduler used by the UI own the ready-queue decision where its
    # bounded process model applies. Arrival is zero for all ready jobs; the input order
    # encodes FIFO inside a priority tier. Engine failure falls back to the identical ordering.
    if len(ranked) <= 100 and os_bridge.engine_status().get("built"):
        jobs = [{"pid": f"R{r['request_id']}", "arrival": 0, "burst": 1,
                 "priority": 0 if r["urgency"] in ("CRITICAL", "EMERGENCY") else 1 if r["urgency"] == "URGENT" else 2}
                for r in ranked]
        try:
            result = os_bridge.schedule("PRIORITY_NP", jobs)
            first = result.get("gantt", [{}])[0].get("pid")
            if first:
                selected = next((r["request_id"] for r in ranked if f"R{r['request_id']}" == first), None)
                if selected is not None:
                    return selected
        except os_bridge.OSBridgeError as exc:
            log.warning("C emergency scheduler unavailable; using strict priority fallback (%s)", exc.kind)
    return ranked[0]["request_id"]


def _history(conn, request_id, status, actor, reason=None, txn=None):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_history'").fetchone()
    if exists:
        conn.execute("INSERT INTO request_history(request_id,status,actor,reason,txn_id) VALUES(?,?,?,?,?)",
                     (request_id,status,actor,reason,txn))


def reserve_for_request(conn, request_id, actor="scheduler"):
    """Reserve all units for one request inside ONE transaction.

    Success: units RESERVED, reservations created, request COMPLETED, single commit.
    Failure: rolled back (no partial reservations); request moves to WAITING and the reason is audited.
    """
    req = conn.execute("SELECT * FROM hospital_request WHERE request_id = ?", (request_id,)).fetchone()
    if req is None:
        raise LookupError(f"request {request_id} not found")
    if req["process_state"] not in SCHEDULABLE:
        raise ValueError(f"request {request_id} is {req['process_state']}, not schedulable")

    txn = uuid.uuid4().hex[:12]
    component, qty = req["component_id"], req["quantity"]
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE hospital_request SET process_state='RUNNING' WHERE request_id=?", (request_id,))
        _history(conn, request_id, "SCHEDULED", actor, txn=txn)
        units = matching_units(conn, component, req["required_group_id"])
        if len(units) < qty:
            raise Shortfall("unavailable inventory")
        granted, reason, sequence = banker_gate(conn, request_id, component, qty)
        if not granted:
            raise Shortfall(reason)

        chosen = [u["unit_id"] for u in units[:qty]]
        _history(conn, request_id, "RESERVED", actor, txn=txn)
        for unit_id in chosen:
            cur = conn.execute(
                "UPDATE blood_unit SET status='RESERVED' WHERE unit_id=? AND status='AVAILABLE'", (unit_id,)
            )
            if cur.rowcount != 1:
                raise Shortfall("unit changed under us")
            conn.execute("INSERT INTO reservation (request_id, unit_id) VALUES (?,?)", (request_id, unit_id))
            audit(conn, actor, "RESERVE", "blood_unit", unit_id, txn, None, "AVAILABLE", "RESERVED")
        conn.execute("UPDATE hospital_request SET process_state='COMPLETED' WHERE request_id=?", (request_id,))
        audit(conn, actor, "REQUEST_COMPLETED", "hospital_request", request_id, txn, None, "RUNNING", "COMPLETED")
        _history(conn, request_id, "COMPLETED", actor, txn=txn)
        audit(conn, actor, "COMMIT", "hospital_request", request_id, txn, "reservation transaction committed", "RUNNING", "COMPLETED")
        conn.execute("COMMIT")
        return {"request_id": request_id, "state": "COMPLETED", "txn": txn, "units": chosen,
                "banker_sequence": sequence}
    except Exception as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if isinstance(exc, Shortfall):
            reason = str(exc)
        elif isinstance(exc, sqlite3.IntegrityError):
            reason = "constraint violation"
        elif isinstance(exc, sqlite3.OperationalError):
            reason = "lock timeout"
        else:
            raise

    with transaction(conn):
        conn.execute("UPDATE hospital_request SET process_state='WAITING' WHERE request_id=?", (request_id,))
        audit(conn, actor, "ROLLBACK", "hospital_request", request_id, txn, reason, req["process_state"], "WAITING")
        _history(conn, request_id, "FAILED", actor, reason, txn)
    return {"request_id": request_id, "state": "WAITING", "txn": txn, "units": [], "reason": reason}


def run_tick(conn, actor="scheduler"):
    rid = pick_next(conn)
    if rid is None:
        return {"message": "ready queue empty"}
    return reserve_for_request(conn, rid, actor)


def intake_donation(conn, blood_group_id, component_id, count, location, actor="intake", hospital_id=None):
    """Producer: new screened units arrive and wake any WAITING requests for that component."""
    shelf = conn.execute(
        "SELECT shelf_life_days FROM component_type WHERE component_id=?", (component_id,)
    ).fetchone()
    if shelf is None:
        raise ValueError("unknown component")
    collected = today()
    expiry = add_days(collected, shelf["shelf_life_days"])
    ids = []
    has_hospital = "hospital_id" in {r[1] for r in conn.execute("PRAGMA table_info(blood_unit)")}
    with transaction(conn):
        for _ in range(count):
            if hospital_id is not None and has_hospital:
                cur = conn.execute(
                    "INSERT INTO blood_unit (component_id,blood_group_id,location,collection_date,expiry_date,hospital_id) VALUES (?,?,?,?,?,?)",
                    (component_id,blood_group_id,location,collected,expiry,hospital_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO blood_unit (component_id, blood_group_id, location, collection_date, expiry_date) VALUES (?,?,?,?,?)",
                    (component_id,blood_group_id,location,collected,expiry),
                )
            ids.append(cur.lastrowid)
            audit(conn, actor, "INTAKE", "blood_unit", cur.lastrowid, None, None, None, "AVAILABLE")
        woken = conn.execute(
            "UPDATE hospital_request SET process_state='READY' WHERE process_state='WAITING' AND component_id=?",
            (component_id,),
        ).rowcount
        if woken:
            audit(conn, actor, "WAKE_WAITING", "hospital_request", component_id, None,
                  "new stock arrived", "WAITING", "READY")
        audit(conn, actor, "COMMIT", "donation_batch", ids[0] if ids else component_id, None,
              "donation intake committed", None, f"{len(ids)} units")
    return {"unit_ids": ids, "woken": woken, "expires": expiry}


def run_expiry_monitor(conn, actor="expiry-monitor"):
    with transaction(conn):
        rows = conn.execute(
            "SELECT unit_id FROM blood_unit WHERE status='AVAILABLE' AND expiry_date < ?", (today(),)
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE blood_unit SET status='EXPIRED' WHERE unit_id=?", (r["unit_id"],))
            audit(conn, actor, "EXPIRE", "blood_unit", r["unit_id"], None, "expired unit", "AVAILABLE", "EXPIRED")
    return {"expired": len(rows)}
