"""Flask JSON API + static frontend for the EBMS simulation."""
import logging
import os
import sqlite3
import sys
import io
import secrets
import uuid
import threading
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, session, g, send_file

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, "..", "frontend")
sys.path.insert(0, HERE)

import db  # noqa: E402
import engine  # noqa: E402
import final_phase  # noqa: E402
import os_bridge  # noqa: E402
from migrate import migrate  # noqa: E402
from rwlock import RWLock  # noqa: E402
from seed import seed  # noqa: E402

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.secret_key = os.environ.get("EBMS_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.environ.get("EBMS_COOKIE_SECURE","0")=="1")
AUTH_REQUIRED = os.environ.get("EBMS_AUTH_REQUIRED", "1").strip().lower() not in ("0", "false", "no")
_SCHEMA_READY = set()
_SCHEMA_LOCK = threading.Lock()
lock = RWLock()  # readers overlap; writers are exclusive and never starve

BRIDGE_STATUS = {"invalid_input": 400, "unavailable": 503, "timeout": 504}


def read_rows(sql, args=()):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def run_write(fn, *args, **kwargs):
    with lock.write():
        conn = db.connect()
        try:
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()


def body():
    return request.get_json(silent=True) or {}


def need_fields(data, *names):
    missing = [n for n in names if data.get(n) in (None, "")]
    if missing:
        raise ValueError("missing: " + ", ".join(missing))


def os_call(fn, *args, **kwargs):
    """Runs a C-engine call and maps bridge failures to honest HTTP codes. Internals never reach the browser."""
    try:
        return jsonify(fn(*args, **kwargs))
    except os_bridge.OSBridgeError as exc:
        return jsonify(error=exc.message), BRIDGE_STATUS.get(exc.kind, 500)


@app.errorhandler(ValueError)
@app.errorhandler(TypeError)
@app.errorhandler(sqlite3.IntegrityError)
def bad_request(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(LookupError)
def not_found(exc):
    return jsonify(error=str(exc)), 404


@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    if filename not in ("app.js", "style.css"):
        return jsonify(error="not found"), 404
    return send_from_directory(FRONTEND, filename)


@app.get("/api/health")
def health():
    return jsonify(ok=True)


@app.get("/api/meta")
def meta():
    with lock.read():
        data = {
            "hospitals": read_rows("SELECT hospital_id AS id, name FROM hospital ORDER BY name"),
            "components": read_rows("SELECT component_id AS id, name FROM component_type ORDER BY name"),
            "groups": [r["blood_group_id"] for r in read_rows(
                "SELECT blood_group_id FROM blood_group ORDER BY blood_group_id")],
            "urgencies": list(engine.DROPDOWN_URGENCIES),
        }
    return jsonify(data)


@app.get("/api/inventory")
def inventory():
    with lock.read():
        if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL":
            rows = read_rows("""SELECT component_id,blood_group_id,COUNT(*) AS available,MIN(expiry_date) AS next_expiry
                FROM blood_unit WHERE status='AVAILABLE' AND expiry_date>=? AND hospital_id=? GROUP BY component_id,blood_group_id ORDER BY component_id,blood_group_id""",(engine.today(),g.user["hospital_id"]))
        else:
            rows = read_rows(
                """SELECT component_id, blood_group_id, COUNT(*) AS available, MIN(expiry_date) AS next_expiry
                   FROM blood_unit WHERE status='AVAILABLE' AND expiry_date >= ?
                   GROUP BY component_id, blood_group_id ORDER BY component_id, blood_group_id""",
                (engine.today(),),
            )
    return jsonify(rows)


@app.get("/api/requests")
def requests_list():
    with lock.read():
        if AUTH_REQUIRED and getattr(g, "user", {}).get("role") == "HOSPITAL":
            rows = read_rows("""SELECT r.request_id,h.name AS hospital,r.component_id,r.required_group_id,r.quantity,r.urgency,r.process_state,r.arrival_seq
                FROM hospital_request r JOIN hospital h ON h.hospital_id=r.hospital_id WHERE r.hospital_id=? ORDER BY r.arrival_seq DESC LIMIT 100""",(g.user["hospital_id"],))
        else:
            rows = read_rows(
                """SELECT r.request_id, h.name AS hospital, r.component_id, r.required_group_id,
                          r.quantity, r.urgency, r.process_state, r.arrival_seq
                   FROM hospital_request r JOIN hospital h ON h.hospital_id = r.hospital_id
                   ORDER BY r.arrival_seq DESC LIMIT 100"""
            )
    for r in rows:
        r["urgency"] = engine.urgency_label(r["urgency"])
    return jsonify(rows)


@app.post("/api/requests")
def create_request():
    d = body()
    need_fields(d, "hospital_id", "component_id", "required_group_id", "quantity", "urgency")
    if AUTH_REQUIRED and getattr(g, "user", {}).get("role") == "HOSPITAL":
        d["hospital_id"] = g.user["hospital_id"]
    rid = run_write(engine.create_request, int(d["hospital_id"]), d["component_id"],
                    d["required_group_id"], int(d["quantity"]), d["urgency"])
    return jsonify(request_id=rid), 201


@app.post("/api/scheduler/tick")
def tick():
    return jsonify(run_write(engine.run_tick))


@app.post("/api/donations")
def donate():
    d = body()
    need_fields(d, "blood_group_id", "component_id", "count", "location")
    count = int(d["count"])
    if not 1 <= count <= 200:
        raise ValueError("count must be between 1 and 200")
    hospital_id=int(d["hospital_id"]) if d.get("hospital_id") else None
    result = run_write(engine.intake_donation, d["blood_group_id"], d["component_id"], count, d["location"], hospital_id=hospital_id)
    return jsonify(result), 201


@app.post("/api/expiry/run")
def expiry():
    return jsonify(run_write(engine.run_expiry_monitor))


@app.get("/api/banker")
def banker_view():
    with lock.read():
        conn = db.connect()
        try:
            comps, avail, rids, maxm, alloc = engine.banker_matrices(conn)
        finally:
            conn.close()
    available = dict(zip(comps, avail))
    if not rids:
        return jsonify(available=available, safe=True, sequence=[], error=None)
    try:
        res = os_bridge.banker_check(avail, maxm, alloc)
    except os_bridge.OSBridgeError as exc:
        # safe=None means "could not check", which the UI must show as an error, not as safe
        return jsonify(available=available, safe=None, sequence=[], error=exc.message)
    sequence = [str(rids[i]) for i in res["safe_sequence"]] if res["safe"] else []
    return jsonify(available=available, safe=res["safe"], sequence=sequence, error=None)


@app.get("/api/audit")
def audit_log():
    with lock.read():
        conn = db.connect()
        try:
            result = final_phase.filtered_audit(conn, request.args)
        finally:
            conn.close()
    fmt = request.args.get("format", "json").lower()
    cols = ["event_id", "actor", "event_type", "entity_type", "entity_id", "txn_id", "reason", "before_state", "after_state", "created_at"]
    if fmt == "csv":
        return send_file(io.BytesIO(final_phase.csv_bytes(result["rows"], cols)), mimetype="text/csv", as_attachment=True, download_name="ebms-audit.csv")
    if fmt == "pdf":
        pdf = final_phase.make_pdf("Audit Trail Report", result["rows"], cols)
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name="ebms-audit.pdf")
    return jsonify(result)


@app.get("/api/query-plan")
def query_plan():
    component = request.args.get("component", "RBC")
    group = request.args.get("group", "O-")
    with lock.read():
        conn = db.connect()
        try:
            plan = engine.explain_matching(conn, component, group)
        finally:
            conn.close()
    return jsonify(plan)


# ---------- OS simulation (C engine) ----------

@app.get("/api/os/status")
def os_status():
    return jsonify(built=os_bridge.engine_status()["built"], modules=sorted(os_bridge.MODULES))


@app.post("/api/os/scheduler")
def os_scheduler():
    d = body()
    return os_call(os_bridge.schedule, d.get("algorithm"), d.get("processes"), d.get("quantum"))


@app.post("/api/os/banker")
def os_banker():
    d = body()
    req = d.get("request") or None
    process = req.get("process") if isinstance(req, dict) else None
    vector = req.get("vector") if isinstance(req, dict) else None
    return os_call(os_bridge.banker_check, d.get("available"), d.get("max"), d.get("allocation"),
                   process, vector)


@app.post("/api/os/deadlock")
def os_deadlock():
    d = body()
    edges = d.get("edges") or []
    if not isinstance(edges, list):
        return jsonify(error="edges must be a list"), 400
    pairs = []
    for e in edges:
        if not isinstance(e, dict):
            return jsonify(error="each edge must be an object with 'from' and 'to'"), 400
        pairs.append((e.get("from"), e.get("to")))
    return os_call(os_bridge.detect_deadlock, d.get("processes"), pairs, d.get("held"),
                   d.get("resource_model") or "single_instance")


@app.post("/api/os/producer-consumer")
def os_producer_consumer():
    d = body()
    return os_call(os_bridge.producer_consumer, d.get("capacity"), d.get("producers"), d.get("consumers"),
                   d.get("items_per_producer"))


@app.post("/api/os/readers-writers")
def os_readers_writers():
    d = body()
    return os_call(os_bridge.readers_writers, d.get("readers"), d.get("writers"), d.get("reads_per_reader"),
                   d.get("writes_per_writer"), d.get("hold_ms", 1))


@app.before_request
def final_schema_and_auth():
    # Safe, additive, idempotent schema upgrades for each active database path.
    if request.path.startswith("/api/"):
        path = db.DEFAULT_DB
        if path not in _SCHEMA_READY:
            with _SCHEMA_LOCK:
                if path not in _SCHEMA_READY:
                    conn = db.connect()
                    try: final_phase.ensure_schema(conn)
                    finally: conn.close()
                    _SCHEMA_READY.add(path)
    if request.path.startswith("/api/") and session.get("user_id"):
        row = read_rows("SELECT user_id,username,role,hospital_id,active FROM users WHERE user_id=?", (session["user_id"],))
        if not row or not row[0]["active"]:
            session.clear()
            if AUTH_REQUIRED: return jsonify(error="account unavailable"), 401
        else:
            g.user = row[0]
    if not AUTH_REQUIRED or not request.path.startswith("/api/") or request.path in ("/api/health", "/api/meta", "/api/auth/setup", "/api/auth/login", "/api/auth/me"):
        return None
    if not getattr(g, "user", None):
        return jsonify(error="authentication required"), 401
    role = g.user["role"]
    if role == "HOSPITAL" and (request.path in ("/api/donations", "/api/scheduler/tick", "/api/expiry/run", "/api/audit", "/api/analytics", "/api/banker", "/api/query-plan") or request.path.startswith(("/api/users", "/api/reports", "/api/os/"))):
        return jsonify(error="forbidden for hospital role"), 403
    if role == "STAFF" and (request.path.startswith("/api/users") or request.path.startswith("/api/reports") or request.path == "/api/audit"):
        return jsonify(error="forbidden for staff role"), 403
    return None


def roles(*allowed):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = getattr(g, "user", None)
            if not user:
                return jsonify(error="authentication required"), 401
            if user["role"] not in allowed:
                return jsonify(error="forbidden"), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def valid_date(value, field):
    if not value:
        return None
    try:
        return __import__("datetime").date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError(field + " must be YYYY-MM-DD")


@app.post("/api/auth/setup")
def auth_setup():
    d = body(); need_fields(d, "username", "password")
    if len(str(d["password"])) < 12:
        raise ValueError("admin password must be at least 12 characters")
    conn = db.connect()
    try:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return jsonify(error="setup is already complete"), 409
        from werkzeug.security import generate_password_hash
        with db.transaction(conn):
            conn.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'ADMIN')",
                         (str(d["username"]).strip(), generate_password_hash(str(d["password"]))))
    finally:
        conn.close()
    return jsonify(created=True, role="ADMIN"), 201


@app.post("/api/auth/login")
def auth_login():
    d=body(); need_fields(d,"username","password")
    from werkzeug.security import check_password_hash
    rows=read_rows("SELECT user_id,username,password_hash,role,hospital_id,active FROM users WHERE username=?",(str(d["username"]).strip(),))
    if not rows or not rows[0]["active"] or not check_password_hash(rows[0]["password_hash"],str(d["password"])):
        return jsonify(error="invalid credentials"),401
    u=rows[0]; session.clear(); session["user_id"]=u["user_id"]
    return jsonify(user={"username":u["username"],"role":u["role"],"hospital_id":u["hospital_id"]})


@app.post("/api/auth/logout")
def auth_logout():
    session.clear(); return jsonify(ok=True)


@app.get("/api/auth/me")
def auth_me():
    if not session.get("user_id"): return jsonify(user=None)
    rows=read_rows("SELECT username,role,hospital_id FROM users WHERE user_id=? AND active=1",(session["user_id"],))
    return jsonify(user=rows[0] if rows else None)


@app.get("/api/donors")
def list_donors():
    user=getattr(g,"user",{}) or {}; args=[]
    sql="SELECT d.donor_id,d.full_name,d.phone,d.blood_group_id,d.hospital_id,h.name AS hospital,d.created_at FROM donor d JOIN hospital h ON h.hospital_id=d.hospital_id"
    if AUTH_REQUIRED and user.get("role")=="HOSPITAL": sql += " WHERE d.hospital_id=?"; args.append(user["hospital_id"])
    sql += " ORDER BY d.donor_id DESC"
    return jsonify(read_rows(sql,args))

@app.post("/api/donors")
@roles("ADMIN","STAFF","HOSPITAL")
def create_donor():
    d=body(); need_fields(d,"full_name","phone","blood_group_id","age")
    user=getattr(g,"user",{}) or {}; hid=d.get("hospital_id")
    if AUTH_REQUIRED and user.get("role")=="HOSPITAL": hid=user["hospital_id"]
    if not hid: raise ValueError("hospital_id is required")
    age=int(d["age"])
    if not 18<=age<=65: raise ValueError("Donor age must be between 18 and 65 for this academic simulation")
    conn=db.connect()
    try:
        with db.transaction(conn):
            cur=conn.execute("INSERT INTO donor(full_name,phone,blood_group_id,hospital_id,age) VALUES(?,?,?,?,?)",(str(d["full_name"]).strip(),str(d["phone"]).strip(),d["blood_group_id"],hid,age))
            donor_id=cur.lastrowid
            import secrets
            token="EBMS-"+secrets.token_hex(4).upper()
            price=conn.execute("SELECT price FROM service_pricing WHERE service_key='DONOR_REGISTRATION'").fetchone()[0]
            conn.execute("INSERT INTO donor_token(donor_id,token_code,service_price) VALUES(?,?,?)",(donor_id,token,price))
            final_phase.audit(conn,user.get("username","system"),"DONOR_REGISTER","donor",donor_id,after=f"hospital={hid}; token={token}")
        return jsonify(donor_id=donor_id,token=token,service_price=price),201
    finally: conn.close()

@app.post("/api/donors/<int:donor_id>/screening")
@roles("ADMIN","STAFF")
def donor_screening(donor_id):
    d=body(); keys=("hiv","hbv","hcv","syphilis","malaria"); need_fields(d,*keys)
    vals=[str(d[k]).upper() for k in keys]
    allowed={"PENDING","NEGATIVE","POSITIVE","REACTIVE"}
    if any(v not in allowed for v in vals): raise ValueError("screening results must be PENDING, NEGATIVE, POSITIVE or REACTIVE")
    conn=db.connect(); actor=(getattr(g,"user",{}) or {}).get("username","staff")
    try:
        with db.transaction(conn):
            if not conn.execute("SELECT 1 FROM donor WHERE donor_id=?",(donor_id,)).fetchone(): raise LookupError("donor not found")
            cur=conn.execute("INSERT INTO donor_screening(donor_id,hiv,hbv,hcv,syphilis,malaria,blood_test_notes,screened_by) VALUES(?,?,?,?,?,?,?,?)",(donor_id,*vals,d.get("blood_test_notes"),actor))
            final_phase.audit(conn,actor,"DONOR_SCREENING","donor_screening",cur.lastrowid,after="screening record created")
        return jsonify(screening_id=cur.lastrowid,results=dict(zip(keys,vals))),201
    finally: conn.close()

@app.get("/api/donors/<int:donor_id>/screening")
@roles("ADMIN","STAFF")
def get_donor_screening(donor_id):
    return jsonify(read_rows("SELECT * FROM donor_screening WHERE donor_id=? ORDER BY screening_id DESC",(donor_id,)))

@app.get("/api/pricing")
@roles("ADMIN","STAFF")
def list_pricing(): return jsonify(read_rows("SELECT * FROM service_pricing ORDER BY service_key"))

@app.patch("/api/pricing/<service_key>")
@roles("ADMIN")
def update_pricing(service_key):
    d=body(); need_fields(d,"price"); price=float(d["price"])
    if price<0: raise ValueError("price cannot be negative")
    conn=db.connect()
    try:
        with db.transaction(conn):
            cur=conn.execute("UPDATE service_pricing SET price=?,updated_at=datetime('now') WHERE service_key=?",(price,service_key))
            if not cur.rowcount: raise LookupError("pricing service not found")
            final_phase.audit(conn,g.user["username"],"PRICING_UPDATE","service_pricing",service_key,after=str(price))
        return jsonify(service_key=service_key,price=price)
    finally: conn.close()

@app.get("/api/hospitals")
def list_hospitals():
    return jsonify(read_rows("SELECT hospital_id AS id,name,location FROM hospital ORDER BY name"))


@app.post("/api/hospitals")
@roles("ADMIN")
def create_hospital():
    d=body(); need_fields(d,"name","location")
    name=str(d["name"]).strip(); location=str(d["location"]).strip()
    if not name or len(name)>160 or not location or len(location)>200: raise ValueError("name and location are required and must be within allowed length")
    conn=db.connect()
    try:
        with db.transaction(conn):
            cur=conn.execute("INSERT INTO hospital(name,location) VALUES(?,?)",(name,location))
            final_phase.audit(conn,g.user["username"],"HOSPITAL_CREATE","hospital",cur.lastrowid,after=name)
        return jsonify(hospital_id=cur.lastrowid,name=name,location=location),201
    finally: conn.close()


@app.patch("/api/hospitals/<int:hospital_id>")
@roles("ADMIN")
def update_hospital(hospital_id):
    d=body(); need_fields(d,"name","location")
    name=str(d["name"]).strip(); location=str(d["location"]).strip()
    if not name or len(name)>160 or not location or len(location)>200: raise ValueError("name and location are required and must be within allowed length")
    conn=db.connect()
    try:
        with db.transaction(conn):
            old=conn.execute("SELECT name,location FROM hospital WHERE hospital_id=?",(hospital_id,)).fetchone()
            if not old: raise LookupError("hospital not found")
            conn.execute("UPDATE hospital SET name=?,location=? WHERE hospital_id=?",(name,location,hospital_id))
            final_phase.audit(conn,g.user["username"],"HOSPITAL_UPDATE","hospital",hospital_id,before=f"{old['name']} | {old['location']}",after=f"{name} | {location}")
        return jsonify(hospital_id=hospital_id,name=name,location=location)
    finally: conn.close()


@app.post("/api/users")
@roles("ADMIN")
def create_user():
    d=body(); need_fields(d,"username","password","role")
    role=str(d["role"]).upper()
    if role not in ("ADMIN","STAFF","HOSPITAL"): raise ValueError("role must be ADMIN, STAFF or HOSPITAL")
    if len(str(d["password"]))<12: raise ValueError("password must be at least 12 characters")
    hid=d.get("hospital_id")
    if role=="HOSPITAL" and not hid: raise ValueError("hospital_id is required for Hospital users")
    if role!="HOSPITAL": hid=None
    from werkzeug.security import generate_password_hash
    conn=db.connect()
    try:
        with db.transaction(conn):
            cur=conn.execute("INSERT INTO users(username,password_hash,role,hospital_id) VALUES(?,?,?,?)",
                             (str(d["username"]).strip(),generate_password_hash(str(d["password"])),role,hid))
            final_phase.audit(conn,getattr(g,"user",{}).get("username","admin"),"USER_CREATE","user",cur.lastrowid,after=role)
        return jsonify(user_id=cur.lastrowid,role=role),201
    finally: conn.close()


@app.get("/api/users")
@roles("ADMIN")
def list_users():
    return jsonify(read_rows("SELECT user_id,username,role,hospital_id,active,created_at FROM users ORDER BY username"))


@app.get("/api/dashboard")
def dashboard_data():
    # Dashboard polling also runs the idempotent expiry sweep, so stale units are closed out automatically.
    run_write(engine.run_expiry_monitor)
    with lock.read():
        conn=db.connect()
        try:
            hid=g.user["hospital_id"] if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL" else None
            data=final_phase.dashboard(conn,hid)
        finally: conn.close()
    return jsonify(data)


@app.post("/api/matching")
def smart_matching():
    d=body(); need_fields(d,"required_group_id","component_id","quantity")
    qty=int(d["quantity"])
    if qty<1 or qty>200: raise ValueError("quantity must be between 1 and 200")
    group=str(d["required_group_id"]); comp=str(d["component_id"])
    conn=db.connect()
    try:
        donors=final_phase.compatible_groups(conn,comp,group)
        hid=int(d["hospital_id"]) if d.get("hospital_id") else None
        hospital=conn.execute("SELECT name FROM hospital WHERE hospital_id=?",(hid,)).fetchone() if hid else None
        if d.get("hospital_id") and not hospital: raise ValueError("unknown hospital")
        if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL": hid=g.user["hospital_id"]; hospital=conn.execute("SELECT name FROM hospital WHERE hospital_id=?",(hid,)).fetchone()
        today=engine.today(); marks=",".join("?" for _ in donors) or "NULL"
        sql=f"""SELECT unit_id,component_id,blood_group_id,location,hospital_id,collection_date,expiry_date
                 FROM blood_unit WHERE component_id=? AND blood_group_id IN ({marks}) AND status='AVAILABLE' AND expiry_date>=?"""
        args=[comp,*donors,today]
        if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL": sql+=" AND hospital_id=?"; args.append(hid)
        sql+=" ORDER BY expiry_date,collection_date,unit_id"
        rows=[dict(r) for r in conn.execute(sql,args)]
        return jsonify(request={"blood_group":group,"component":comp,"quantity":qty,"hospital_id":hid,"hospital":hospital["name"] if hospital else None,"urgency":d.get("urgency"),"request_id":d.get("request_id")},
                       compatible_groups=donors,available=len(rows),sufficient=len(rows)>=qty,units=rows[:qty],
                       compatibility_note="Academic simulation rules only. Compatibility and allocation require medical verification.")
    finally: conn.close()


@app.get("/api/expiry")
def expiry_list():
    today=engine.today(); until=(__import__("datetime").date.today()+__import__("datetime").timedelta(days=7)).isoformat()
    clauses=[]; args=[]
    group=request.args.get("group"); start=valid_date(request.args.get("from"),"from"); end=valid_date(request.args.get("to"),"to")
    if group: clauses.append("blood_group_id=?"); args.append(group)
    if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL": clauses.append("hospital_id=?"); args.append(g.user["hospital_id"])
    if start: clauses.append("expiry_date>=?"); args.append(start)
    if end: clauses.append("expiry_date<=?"); args.append(end)
    clauses.append("(status='EXPIRED' OR expiry_date<=?)"); args.append(until)
    sql="SELECT unit_id,component_id,blood_group_id,location,hospital_id,expiry_date,status FROM blood_unit WHERE "+" AND ".join(clauses)+" ORDER BY expiry_date,unit_id"
    return jsonify(read_rows(sql,args))


@app.get("/api/requests/<int:request_id>/history")
def request_timeline(request_id):
    with lock.read():
        conn=db.connect()
        try:
            request_row=conn.execute("SELECT hospital_id FROM hospital_request WHERE request_id=?",(request_id,)).fetchone()
            if not request_row: raise LookupError("request not found")
            if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL" and request_row["hospital_id"]!=g.user["hospital_id"]: return jsonify(error="forbidden"),403
            rows=[dict(r) for r in conn.execute("SELECT status,actor,reason,txn_id,created_at FROM request_history WHERE request_id=? ORDER BY history_id",(request_id,))]
            units=[dict(r) for r in conn.execute("SELECT b.unit_id,b.blood_group_id,b.component_id,b.expiry_date,res.status FROM reservation res JOIN blood_unit b ON b.unit_id=res.unit_id WHERE res.request_id=? ORDER BY b.expiry_date",(request_id,))]
        finally: conn.close()
    return jsonify(history=rows,units=units)


@app.get("/api/analytics")
def analytics_data():
    start=valid_date(request.args.get("from"),"from"); end=valid_date(request.args.get("to"),"to"); group=request.args.get("group")
    with lock.read():
        conn=db.connect()
        try: result=final_phase.analytics(conn,start,end,group)
        finally: conn.close()
    return jsonify(result)


@app.get("/api/transfers")
def transfers_list():
    sql="""SELECT t.transfer_id,t.request_key,t.component_id,t.blood_group_id,t.quantity,t.status,t.created_at,
      s.name source,d.name destination,t.txn_id FROM hospital_transfer t JOIN hospital s ON s.hospital_id=t.source_hospital_id
      JOIN hospital d ON d.hospital_id=t.destination_hospital_id"""
    if AUTH_REQUIRED and getattr(g,"user",{}).get("role")=="HOSPITAL":
        sql+=" WHERE t.source_hospital_id=? OR t.destination_hospital_id=?"
        sql+=" ORDER BY t.transfer_id DESC LIMIT 200"
        return jsonify(read_rows(sql,(g.user["hospital_id"],g.user["hospital_id"])))
    return jsonify(read_rows(sql+" ORDER BY t.transfer_id DESC LIMIT 200"))


@app.post("/api/transfers")
@roles("ADMIN","STAFF","HOSPITAL")
def create_transfer():
    d=body(); need_fields(d,"source_hospital_id","destination_hospital_id","component_id","blood_group_id","quantity")
    qty=int(d["quantity"])
    if qty<1 or qty>200: raise ValueError("quantity must be between 1 and 200")
    user=getattr(g,"user",{})
    if AUTH_REQUIRED and user.get("role")=="HOSPITAL":
        d["destination_hospital_id"]=user["hospital_id"]
        if int(d["source_hospital_id"])==int(user["hospital_id"]): raise ValueError("source and destination must differ")
    key=str(d.get("request_key") or uuid.uuid4().hex)
    txn=uuid.uuid4().hex[:12]; conn=db.connect()
    try:
        with db.transaction(conn):
            sources=int(d["source_hospital_id"]); dest=int(d["destination_hospital_id"])
            if sources==dest: raise ValueError("source and destination must differ")
            comp=str(d["component_id"]); group=str(d["blood_group_id"])
            duplicate=conn.execute("SELECT transfer_id FROM hospital_transfer WHERE source_hospital_id=? AND destination_hospital_id=? AND component_id=? AND blood_group_id=? AND quantity=? AND status IN ('PENDING','APPROVED','IN_TRANSIT') LIMIT 1",(sources,dest,comp,group,qty)).fetchone()
            if duplicate: raise ValueError(f"duplicate active transfer already exists ({duplicate['transfer_id']})")
            units=conn.execute("""SELECT unit_id,location FROM blood_unit WHERE hospital_id=? AND component_id=? AND blood_group_id=?
              AND status='AVAILABLE' AND expiry_date>=? ORDER BY expiry_date,collection_date,unit_id LIMIT ?""",
              (sources,comp,group,engine.today(),qty)).fetchall()
            if len(units)<qty: raise ValueError("source hospital has insufficient available, unexpired units")
            cur=conn.execute("INSERT INTO hospital_transfer(request_key,source_hospital_id,destination_hospital_id,component_id,blood_group_id,quantity,status,txn_id) VALUES(?,?,?,?,?,?, 'PENDING',?)",(key,sources,dest,comp,group,qty,txn))
            tid=cur.lastrowid
            for u in units:
                conn.execute("UPDATE blood_unit SET status='RESERVED' WHERE unit_id=? AND status='AVAILABLE'",(u["unit_id"],))
                conn.execute("INSERT INTO transfer_item(transfer_id,unit_id,previous_location) VALUES(?,?,?)",(tid,u["unit_id"],u["location"]))
                final_phase.audit(conn,user.get("username","transfer-ui"),"TRANSFER_RESERVE","blood_unit",u["unit_id"],txn,"transfer pending","AVAILABLE","RESERVED")
            conn.execute("INSERT INTO transfer_history(transfer_id,status,actor,txn_id) VALUES(?,?,?,?)",(tid,"PENDING",user.get("username","transfer-ui"),txn))
            final_phase.audit(conn,user.get("username","transfer-ui"),"TRANSFER_CREATE","hospital_transfer",tid,txn,after="PENDING")
            final_phase.audit(conn,user.get("username","transfer-ui"),"COMMIT","hospital_transfer",tid,txn,"transfer reservation committed",None,"PENDING")
        return jsonify(transfer_id=tid,status="PENDING",txn_id=txn),201
    except Exception as exc:
        try:
            with db.transaction(conn): final_phase.audit(conn,user.get("username","transfer-ui"),"ROLLBACK","hospital_transfer",key,txn,str(exc),None,"FAILED")
        except Exception: pass
        raise
    finally: conn.close()


@app.patch("/api/transfers/<int:transfer_id>")
@roles("ADMIN","STAFF")
def update_transfer(transfer_id):
    d=body(); need_fields(d,"status")
    new=str(d["status"]).upper(); allowed={"PENDING":"APPROVED","APPROVED":"IN_TRANSIT","IN_TRANSIT":"COMPLETED"}
    conn=db.connect(); actor=getattr(g,"user",{}).get("username","transfer-ui")
    try:
        with db.transaction(conn):
            t=conn.execute("SELECT * FROM hospital_transfer WHERE transfer_id=?",(transfer_id,)).fetchone()
            if not t: raise LookupError("transfer not found")
            if allowed.get(t["status"])!=new: raise ValueError("transfer status must follow Pending -> Approved -> In Transit -> Completed")
            txn=t["txn_id"]
            if new=="COMPLETED":
                rows=conn.execute("SELECT unit_id FROM transfer_item WHERE transfer_id=?",(transfer_id,)).fetchall()
                for item in rows:
                    updated=conn.execute("UPDATE blood_unit SET hospital_id=?,status='AVAILABLE' WHERE unit_id=? AND status='RESERVED'",(t["destination_hospital_id"],item["unit_id"]))
                    if updated.rowcount!=1: raise ValueError("reserved transfer unit changed; transfer rolled back")
                    final_phase.audit(conn,actor,"TRANSFER_COMPLETE","blood_unit",item["unit_id"],txn,"transfer delivered","RESERVED","AVAILABLE")
            conn.execute("UPDATE hospital_transfer SET status=? WHERE transfer_id=?",(new,transfer_id))
            conn.execute("INSERT INTO transfer_history(transfer_id,status,actor,txn_id) VALUES(?,?,?,?)",(transfer_id,new,actor,txn))
            final_phase.audit(conn,actor,"TRANSFER_STATUS","hospital_transfer",transfer_id,txn,None,t["status"],new)
            final_phase.audit(conn,actor,"COMMIT","hospital_transfer",transfer_id,txn,"transfer state committed",t["status"],new)
        return jsonify(transfer_id=transfer_id,status=new)
    except Exception as exc:
        try:
            with db.transaction(conn): final_phase.audit(conn,actor,"ROLLBACK","hospital_transfer",transfer_id,reason=str(exc),before=None,after="UNCHANGED")
        except Exception: pass
        raise
    finally: conn.close()


REPORTS={
 "inventory":("Blood Inventory Report","SELECT unit_id,component_id,blood_group_id,status,location,expiry_date,collection_date FROM blood_unit ORDER BY component_id,blood_group_id,expiry_date",["unit_id","component_id","blood_group_id","status","location","expiry_date","collection_date"],"collection_date","blood_group_id"),
 "group-stock":("Blood Group Stock Report","SELECT blood_group_id,status,COUNT(*) units FROM blood_unit GROUP BY blood_group_id,status ORDER BY blood_group_id,status",["blood_group_id","status","units"],"collection_date","blood_group_id"),
 "requests":("Hospital Request Report","SELECT request_id,h.name hospital,component_id,required_group_id,quantity,urgency,process_state,created_at FROM hospital_request r JOIN hospital h USING(hospital_id) ORDER BY r.request_id DESC",["request_id","hospital","component_id","required_group_id","quantity","urgency","process_state","created_at"],"created_at","required_group_id"),
 "emergency":("Emergency Request Report","SELECT request_id,h.name hospital,component_id,required_group_id,quantity,urgency,process_state,created_at FROM hospital_request r JOIN hospital h USING(hospital_id) WHERE urgency IN ('CRITICAL','EMERGENCY','URGENT') ORDER BY r.created_at DESC",["request_id","hospital","component_id","required_group_id","quantity","urgency","process_state","created_at"],"created_at","required_group_id"),
 "expiry":("Blood Expiry Report","SELECT unit_id,component_id,blood_group_id,status,expiry_date,location FROM blood_unit WHERE (status='EXPIRED' OR expiry_date<=date('now','+7 day')) ORDER BY expiry_date",["unit_id","component_id","blood_group_id","status","expiry_date","location"],"expiry_date","blood_group_id"),
 "transfers":("Hospital Transfer Report","SELECT transfer_id,s.name source,d.name destination,component_id,blood_group_id,quantity,status,created_at FROM hospital_transfer t JOIN hospital s ON s.hospital_id=t.source_hospital_id JOIN hospital d ON d.hospital_id=t.destination_hospital_id ORDER BY t.transfer_id DESC",["transfer_id","source","destination","component_id","blood_group_id","quantity","status","created_at"],"created_at","blood_group_id"),
 "donations":("Monthly Donation Report","SELECT substr(collection_date,1,7) month,component_id,blood_group_id,COUNT(*) units FROM blood_unit GROUP BY month,component_id,blood_group_id ORDER BY month DESC",["month","component_id","blood_group_id","units"],"collection_date","blood_group_id"),
 "audit":("Audit Trail Report","SELECT event_id,actor,event_type,entity_type,entity_id,txn_id,reason,before_state,after_state,created_at FROM audit_event ORDER BY event_id DESC",["event_id","actor","event_type","entity_type","entity_id","txn_id","reason","before_state","after_state","created_at"],"created_at",None)
}

@app.get("/api/reports/<report_type>")
@roles("ADMIN","STAFF","HOSPITAL")
def generate_report(report_type):
    spec=REPORTS.get(report_type)
    if not spec: raise LookupError("unknown report type")
    title,sql,cols,date_col,group_col=spec
    start=valid_date(request.args.get("from"),"from"); end=valid_date(request.args.get("to"),"to")
    clauses=[]; args=[]
    if start: clauses.append("date("+date_col+")>=date(?)"); args.append(start)
    if end: clauses.append("date("+date_col+")<=date(?)"); args.append(end)
    group=request.args.get("group")
    if group and group_col: clauses.append(group_col+"=?"); args.append(group)
    if clauses:
        # Place filters before GROUP/ORDER clauses, preserving the report query's aggregation.
        upper=sql.upper()
        cuts=[i for i in (upper.find(" GROUP BY "),upper.find(" ORDER BY ")) if i>=0]
        cut=min(cuts) if cuts else len(sql)
        head,tail=sql[:cut],sql[cut:]
        joiner=" AND " if " WHERE " in head.upper() else " WHERE "
        sql=head+joiner+" AND ".join(clauses)+tail
    rows=read_rows(sql,args)
    pdf=final_phase.make_pdf(title,rows,cols)
    return send_file(io.BytesIO(pdf),mimetype="application/pdf",as_attachment=True,download_name="ebms-"+report_type+"-report.pdf")


if __name__ == "__main__":
    conn = db.connect()
    seed(conn)
    conn.close()
    mconn = db.connect()
    migrate(mconn)
    mconn.close()
    app.run(port=int(os.environ.get("PORT", 5000)), threaded=True)
