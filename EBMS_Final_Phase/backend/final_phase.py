"""Non-destructive final-phase services for EBMS. Academic simulation only."""
import csv
import datetime as dt
import io
import json
import sqlite3
import uuid


def ensure_schema(conn):
    """Idempotent additive migration. Existing tables and rows are never dropped."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS request_history (
      history_id INTEGER PRIMARY KEY AUTOINCREMENT,
      request_id INTEGER NOT NULL REFERENCES hospital_request(request_id),
      status TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'system', reason TEXT,
      txn_id TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_request_history ON request_history(request_id, history_id);
    CREATE TABLE IF NOT EXISTS users (
      user_id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('ADMIN','HOSPITAL','STAFF')),
      hospital_id INTEGER REFERENCES hospital(hospital_id),
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS hospital_transfer (
      transfer_id INTEGER PRIMARY KEY AUTOINCREMENT,
      request_key TEXT UNIQUE,
      source_hospital_id INTEGER NOT NULL REFERENCES hospital(hospital_id),
      destination_hospital_id INTEGER NOT NULL REFERENCES hospital(hospital_id),
      component_id TEXT NOT NULL REFERENCES component_type(component_id),
      blood_group_id TEXT NOT NULL REFERENCES blood_group(blood_group_id),
      quantity INTEGER NOT NULL CHECK(quantity > 0),
      status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','IN_TRANSIT','COMPLETED','FAILED','CANCELLED')),
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      txn_id TEXT NOT NULL,
      CHECK(source_hospital_id <> destination_hospital_id)
    );
    CREATE TABLE IF NOT EXISTS transfer_item (
      transfer_id INTEGER NOT NULL REFERENCES hospital_transfer(transfer_id),
      unit_id INTEGER NOT NULL REFERENCES blood_unit(unit_id),
      previous_location TEXT,
      PRIMARY KEY(transfer_id, unit_id),
      UNIQUE(unit_id, transfer_id)
    );
    CREATE TABLE IF NOT EXISTS transfer_history (
      history_id INTEGER PRIMARY KEY AUTOINCREMENT,
      transfer_id INTEGER NOT NULL REFERENCES hospital_transfer(transfer_id),
      status TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT,
      txn_id TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_transfer_history ON transfer_history(transfer_id, history_id);
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(blood_unit)")}
    if "hospital_id" not in cols:
        conn.execute("ALTER TABLE blood_unit ADD COLUMN hospital_id INTEGER REFERENCES hospital(hospital_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unit_hospital_status ON blood_unit(hospital_id,status,component_id,blood_group_id,expiry_date)")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS donor (
      donor_id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL, phone TEXT NOT NULL,
      blood_group_id TEXT NOT NULL REFERENCES blood_group(blood_group_id), hospital_id INTEGER NOT NULL REFERENCES hospital(hospital_id),
      age INTEGER NOT NULL CHECK(age BETWEEN 18 AND 65), created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_donor_hospital ON donor(hospital_id, donor_id);
    CREATE TABLE IF NOT EXISTS donor_screening (
      screening_id INTEGER PRIMARY KEY AUTOINCREMENT, donor_id INTEGER NOT NULL REFERENCES donor(donor_id),
      hiv TEXT NOT NULL CHECK(hiv IN ('PENDING','NEGATIVE','POSITIVE','REACTIVE')),
      hbv TEXT NOT NULL CHECK(hbv IN ('PENDING','NEGATIVE','POSITIVE','REACTIVE')),
      hcv TEXT NOT NULL CHECK(hcv IN ('PENDING','NEGATIVE','POSITIVE','REACTIVE')),
      syphilis TEXT NOT NULL CHECK(syphilis IN ('PENDING','NEGATIVE','POSITIVE','REACTIVE')),
      malaria TEXT NOT NULL CHECK(malaria IN ('PENDING','NEGATIVE','POSITIVE','REACTIVE')),
      blood_test_notes TEXT, screened_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS donor_token (
      token_id INTEGER PRIMARY KEY AUTOINCREMENT, donor_id INTEGER NOT NULL REFERENCES donor(donor_id),
      token_code TEXT NOT NULL UNIQUE, service_price REAL NOT NULL DEFAULT 0 CHECK(service_price >= 0),
      status TEXT NOT NULL DEFAULT 'ISSUED', created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS service_pricing (
      service_key TEXT PRIMARY KEY, display_name TEXT NOT NULL, price REAL NOT NULL CHECK(price >= 0),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT OR IGNORE INTO service_pricing(service_key,display_name,price) VALUES
      ('DONOR_REGISTRATION','Donor registration token',0),('SCREENING_PANEL','Infectious disease screening panel',0);
    """)



def compatible_groups(conn, component, recipient):
    """Conservative teaching rules, not transfusion advice. Component compatibility differs."""
    groups = [r[0] for r in conn.execute("SELECT blood_group_id FROM blood_group ORDER BY blood_group_id")]
    if component == "RBC":
        return [r[0] for r in conn.execute(
            "SELECT donor_group_id FROM blood_compatibility WHERE recipient_group_id=?", (recipient,))]
    abo = recipient[:-1] if recipient in groups else None
    # Plasma compatibility is the reverse direction of RBC compatibility; Rh is not used here.
    plasma = {"O": ["O"], "A": ["A", "AB"], "B": ["B", "AB"], "AB": ["AB"]}
    if component == "FFP":
        allowed = plasma.get(abo, [])
        return [g for g in groups if g[:-1] in allowed]
    if component == "PLT":
        # Teaching approximation: ABO-identical preferred, group O may be selected; Rh ignored.
        # Real platelet selection includes antibody titers, Rh sensitization, and product factors.
        return [g for g in groups if g[:-1] in (["O"] if abo == "O" else [abo, "O"])]
    raise ValueError("unknown component")


def audit(conn, actor, event, entity, eid, txn=None, reason=None, before=None, after=None):
    conn.execute("""INSERT INTO audit_event(actor,event_type,entity_type,entity_id,txn_id,reason,before_state,after_state)
                   VALUES(?,?,?,?,?,?,?,?)""", (actor,event,entity,str(eid),txn,reason,before,after))


def request_history(conn, rid, status, actor="system", reason=None, txn=None):
    conn.execute("INSERT INTO request_history(request_id,status,actor,reason,txn_id) VALUES(?,?,?,?,?)",
                 (rid,status,actor,reason,txn))


def dashboard(conn, hospital_id=None):
    today = dt.date.today().isoformat()
    scope = " WHERE hospital_id=?" if hospital_id else ""
    scope_args = (hospital_id,) if hospital_id else ()
    counts = conn.execute("""SELECT COUNT(*) total,
      SUM(status='AVAILABLE' AND expiry_date>=?) available,
      SUM(status='RESERVED') reserved,
      SUM(status='EXPIRED' OR expiry_date<?) expired,
      SUM(status='AVAILABLE' AND expiry_date>=? AND expiry_date<=?) expiring
      FROM blood_unit""" + scope, (today,today,today,(dt.date.today()+dt.timedelta(days=7)).isoformat(),*scope_args)).fetchone()
    rscope = " WHERE hospital_id=?" if hospital_id else ""
    req = {r[0]: r[1] for r in conn.execute("SELECT process_state,COUNT(*) FROM hospital_request"+rscope+" GROUP BY process_state", scope_args)}
    critical = conn.execute("SELECT COUNT(*) FROM hospital_request WHERE process_state IN ('READY','WAITING','RUNNING') AND urgency IN ('CRITICAL','EMERGENCY')" + (" AND hospital_id=?" if hospital_id else ""), scope_args).fetchone()[0]
    return {"units": {k: int(counts[i] or 0) for i,k in enumerate(("total","available","reserved","expired","expiring"))},
            "requests": {"pending": int(req.get("READY",0)+req.get("WAITING",0)+req.get("RUNNING",0)),
                         "completed": int(req.get("COMPLETED",0)), "critical": int(critical), "by_status": req},
            "hospitals": 1 if hospital_id else conn.execute("SELECT COUNT(*) FROM hospital").fetchone()[0],
            "groups": [dict(r) for r in conn.execute("""SELECT blood_group_id,
               SUM(status='AVAILABLE' AND expiry_date>=?) available, SUM(status='RESERVED') reserved,
               COUNT(*) total FROM blood_unit""" + scope + " GROUP BY blood_group_id ORDER BY blood_group_id", (today,*scope_args))],
            "recent_activity": ([dict(r) for r in conn.execute("SELECT event_id,actor,event_type,entity_type,entity_id,reason,created_at FROM audit_event WHERE entity_type='hospital_request' AND entity_id IN (SELECT CAST(request_id AS TEXT) FROM hospital_request WHERE hospital_id=?) ORDER BY event_id DESC LIMIT 8",(hospital_id,))] if hospital_id else [dict(r) for r in conn.execute("SELECT event_id,actor,event_type,entity_type,entity_id,reason,created_at FROM audit_event ORDER BY event_id DESC LIMIT 8")])}


def analytics(conn, start=None, end=None, group=None):
    today = dt.date.today().isoformat()
    clauses, args = [], []
    if group:
        clauses.append("blood_group_id=?"); args.append(group)
    unit_where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    inventory = [dict(r) for r in conn.execute("SELECT blood_group_id, status, COUNT(*) count FROM blood_unit"+unit_where+" GROUP BY blood_group_id,status",args)]
    exp_where = ["expiry_date BETWEEN ? AND ?"]
    exp_args = [start or today, end or (dt.date.today()+dt.timedelta(days=30)).isoformat()]
    if group:
        exp_where.append("blood_group_id=?"); exp_args.append(group)
    exp = [dict(r) for r in conn.execute("SELECT expiry_date, COUNT(*) count FROM blood_unit WHERE " + " AND ".join(exp_where) + " GROUP BY expiry_date ORDER BY expiry_date", exp_args)]
    donations = [dict(r) for r in conn.execute("SELECT substr(collection_date,1,7) month,COUNT(*) units FROM blood_unit WHERE collection_date BETWEEN ? AND ?" + (" AND blood_group_id=?" if group else "") + " GROUP BY month ORDER BY month", (start or "1900-01-01", end or today, *((group,) if group else ())))]
    requests = [dict(r) for r in conn.execute("SELECT process_state,urgency,COUNT(*) count FROM hospital_request WHERE date(created_at) BETWEEN ? AND ? GROUP BY process_state,urgency",(start or "1900-01-01",end or today))]
    hospitals = [dict(r) for r in conn.execute("SELECT h.name hospital,COUNT(u.unit_id) units FROM hospital h LEFT JOIN blood_unit u ON u.hospital_id=h.hospital_id" + (" AND u.blood_group_id=?" if group else "") + " GROUP BY h.hospital_id ORDER BY units DESC,h.name", ((group,) if group else ()))]
    return {"inventory":inventory,"expiring":exp,"donations":donations,"requests":requests,"hospitals":hospitals}


def filtered_audit(conn, params):
    where=[]; args=[]
    for key,col in (("event","event_type"),("actor","actor"),("txn_id","txn_id")):
        value=params.get(key)
        if value: where.append(col+" LIKE ?"); args.append("%"+value+"%")
    search=params.get("search")
    if search:
        where.append("(actor LIKE ? OR event_type LIKE ? OR entity_type LIKE ? OR entity_id LIKE ? OR txn_id LIKE ? OR reason LIKE ?)")
        args.extend(["%"+search+"%"]*6)
    for key,op in (("from"," >= "),("to"," <= ")):
        value=params.get(key)
        if value: where.append("date(created_at)"+op+"date(?)"); args.append(value)
    q="SELECT event_id,actor,event_type,entity_type,entity_id,txn_id,reason,before_state,after_state,created_at FROM audit_event"
    if where: q += " WHERE " + " AND ".join(where)
    total=conn.execute("SELECT COUNT(*) FROM ("+q+")",args).fetchone()[0]
    export_all=str(params.get("format","")).lower() in ("csv","pdf")
    limit=max(1,min(int(params.get("limit",50)),200)); offset=max(0,int(params.get("offset",0)))
    if export_all:
        rows=[dict(r) for r in conn.execute(q+" ORDER BY event_id DESC",args)]
    else:
        rows=[dict(r) for r in conn.execute(q+" ORDER BY event_id DESC LIMIT ? OFFSET ?",args+[limit,offset])]
    return {"rows":rows,"total":total,"limit":limit,"offset":offset}


def make_pdf(title, rows, columns):
    """Create a branded, paginated ReportLab PDF; keep a tiny fallback for minimal installs."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.pdfgen import canvas as reportlab_canvas
        from xml.sax.saxutils import escape
        output=io.BytesIO()
        doc=SimpleDocTemplate(output,pagesize=landscape(A4),rightMargin=15*mm,leftMargin=15*mm,topMargin=22*mm,bottomMargin=17*mm,title=title,author="EBMS")
        styles=getSampleStyleSheet()
        styles.add(ParagraphStyle(name="EBMSHeader",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=17,leading=21,textColor=colors.HexColor("#08766f"),alignment=TA_LEFT,spaceAfter=4))
        styles.add(ParagraphStyle(name="EBMSMeta",parent=styles["Normal"],fontSize=8,textColor=colors.HexColor("#60757a"),leading=11))
        styles.add(ParagraphStyle(name="EBMSTable",parent=styles["Normal"],fontSize=7,leading=9,textColor=colors.HexColor("#263f45")))
        data=[[Paragraph("<b>"+escape(str(c))+"</b>",styles["EBMSTable"]) for c in columns]]
        data += [[Paragraph(escape(str(row.get(c,"")) if row.get(c) is not None else ""),styles["EBMSTable"]) for c in columns] for row in rows]
        usable=landscape(A4)[0]-30*mm
        col_widths=[usable/max(1,len(columns))]*len(columns)
        story=[Paragraph("EBMS | Emergency Blood Management System",styles["EBMSHeader"]),Paragraph(escape(title),styles["Heading2"]),Paragraph("Generated: "+dt.datetime.now().strftime("%Y-%m-%d %H:%M")+" | Academic simulation only, not for clinical use | Records: "+str(len(rows)),styles["EBMSMeta"]),Spacer(1,8*mm)]
        if rows:
            table=Table(data,colWidths=col_widths,repeatRows=1,hAlign="LEFT")
            table.setStyle(TableStyle([
              ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e5f4f0")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#075f59")),
              ("GRID",(0,0),(-1,-1),.35,colors.HexColor("#dce8e7")),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f7faf9")]),
              ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
              ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ])); story.append(table)
        else:
            story.append(Paragraph("No records matched the selected filters.",styles["Normal"]))
        def page_decor(canvas,document):
            canvas.saveState(); width,height=document.pagesize
            canvas.setFillColor(colors.HexColor("#08766f")); canvas.rect(0,height-7*mm,width,7*mm,stroke=0,fill=1)
            canvas.setStrokeColor(colors.HexColor("#dce8e7")); canvas.line(15*mm,12*mm,width-15*mm,12*mm)
            canvas.setFont("Helvetica",8); canvas.setFillColor(colors.HexColor("#70818b")); canvas.drawString(15*mm,8*mm,"EBMS | Academic simulation only")
            canvas.restoreState()

        class NumberedCanvas(reportlab_canvas.Canvas):
            """Buffer pages so the footer can render an accurate Page X of Y."""
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs)
                self._page_states=[]
            def showPage(self):
                self._page_states.append(dict(self.__dict__))
                self._startPage()
            def save(self):
                # The last page has not received showPage() yet.
                self._page_states.append(dict(self.__dict__))
                total=len(self._page_states)
                for index,state in enumerate(self._page_states,1):
                    self.__dict__.update(state)
                    self.saveState()
                    self.setFont("Helvetica",8)
                    self.setFillColor(colors.HexColor("#70818b"))
                    self.drawRightString(landscape(A4)[0]-15*mm,8*mm,f"Page {index} of {total}")
                    self.restoreState()
                    reportlab_canvas.Canvas.showPage(self)
                reportlab_canvas.Canvas.save(self)
        doc.build(story,onFirstPage=page_decor,onLaterPages=page_decor,canvasmaker=NumberedCanvas)
        return output.getvalue()
    except ImportError:
        pass
    def clean(v):
        s=str(v if v is not None else "")
        return s.encode("ascii","replace").decode("ascii").replace("\\","\\\\").replace("(","\\(").replace(")","\\)")
    lines=["EBMS | Emergency Blood Management System",title,"Generated: "+dt.datetime.now().strftime("%Y-%m-%d %H:%M"),"Academic simulation only. Not for clinical use.",""]
    lines.append(" | ".join(columns))
    lines.extend(" | ".join(str(row.get(c,"")) for c in columns) for row in rows)
    pages=[lines[i:i+42] for i in range(0,max(1,len(lines)),42)]
    objects=[]
    font_id=3+2*len(pages)
    kids=[]
    for index,page in enumerate(pages):
        page_id=3+index*2; stream_id=page_id+1; kids.append(f"{page_id} 0 R")
        page_lines=page+[f"Page {index+1} of {len(pages)}"]
        content="BT /F1 9 Tf 42 800 Td 12 TL\n"+"\n".join("("+clean(line[:150])+") Tj T*" for line in page_lines)+" ET"
        stream=content.encode("latin-1","replace")
        objects.extend([(page_id,f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {stream_id} 0 R >>"),
                        (stream_id,b"<< /Length "+str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream")])
    root=f"<< /Type /Catalog /Pages 2 0 R >>"; pages_obj="<< /Type /Pages /Kids ["+" ".join(kids)+"] /Count "+str(len(pages))+" >>"; font="<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    parts=[b"%PDF-1.4\n%EBMS\n",b"1 0 obj\n"+root.encode()+b"\nendobj\n",b"2 0 obj\n"+pages_obj.encode()+b"\nendobj\n"]
    for oid,obj in objects:
        if isinstance(obj,bytes): parts.append(str(oid).encode()+b" 0 obj\n"+obj+b"\nendobj\n")
        else: parts.append(str(oid).encode()+b" 0 obj\n"+obj.encode()+b"\nendobj\n")
    parts.append(str(font_id).encode()+b" 0 obj\n"+font.encode()+b"\nendobj\n")
    data=b"".join(parts); offsets=[]; pos=0
    # Rebuild offsets from object starts for a proper xref table.
    for chunk in parts:
        if b" 0 obj\n" in chunk:
            try: offsets.append((int(chunk.split(b" 0 obj\n",1)[0]),pos))
            except ValueError: pass
        pos+=len(chunk)
    xref=len(data); size=font_id+1; table=[b"xref\n0 "+str(size).encode()+b"\n0000000000 65535 f \n"]
    mapping=dict(offsets)
    for n in range(1,size): table.append((f"{mapping.get(n,0):010d} 00000 n \n" if n in mapping else "0000000000 00000 f \n").encode())
    trailer=b"trailer\n<< /Size "+str(size).encode()+b" /Root 1 0 R >>\nstartxref\n"+str(xref).encode()+b"\n%%EOF"
    return data+b"".join(table)+trailer


def csv_bytes(rows, columns):
    out=io.StringIO(); writer=csv.DictWriter(out, fieldnames=columns, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")
