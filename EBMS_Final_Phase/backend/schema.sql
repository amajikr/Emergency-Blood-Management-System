-- Emergency Blood Management System: SQLite schema (academic simulation).
-- PostgreSQL port notes live in README.md.
CREATE TABLE IF NOT EXISTS blood_group (
  blood_group_id TEXT PRIMARY KEY CHECK (blood_group_id IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')),
  abo TEXT NOT NULL,
  rh TEXT NOT NULL CHECK (rh IN ('+','-'))
);
CREATE TABLE IF NOT EXISTS blood_compatibility (
  recipient_group_id TEXT NOT NULL REFERENCES blood_group(blood_group_id),
  donor_group_id TEXT NOT NULL REFERENCES blood_group(blood_group_id),
  PRIMARY KEY (recipient_group_id, donor_group_id)
);
CREATE TABLE IF NOT EXISTS component_type (
  component_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  storage_temperature TEXT NOT NULL,
  shelf_life_days INTEGER NOT NULL CHECK (shelf_life_days > 0)
);
CREATE TABLE IF NOT EXISTS hospital (
  hospital_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  location TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blood_unit (
  unit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  component_id TEXT NOT NULL REFERENCES component_type(component_id),
  blood_group_id TEXT NOT NULL REFERENCES blood_group(blood_group_id),
  location TEXT NOT NULL,
  collection_date TEXT NOT NULL,
  expiry_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'AVAILABLE'
    CHECK (status IN ('AVAILABLE','RESERVED','ISSUED','EXPIRED')),
  hospital_id INTEGER REFERENCES hospital(hospital_id),
  CHECK (expiry_date > collection_date)
);
CREATE TABLE IF NOT EXISTS hospital_request (
  request_id INTEGER PRIMARY KEY AUTOINCREMENT,
  hospital_id INTEGER NOT NULL REFERENCES hospital(hospital_id),
  component_id TEXT NOT NULL REFERENCES component_type(component_id),
  required_group_id TEXT NOT NULL REFERENCES blood_group(blood_group_id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  urgency TEXT NOT NULL CHECK (urgency IN ('CRITICAL','EMERGENCY','URGENT','ROUTINE')),
  process_state TEXT NOT NULL DEFAULT 'NEW'
    CHECK (process_state IN ('NEW','READY','RUNNING','WAITING','COMPLETED','FAILED')),
  arrival_seq INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reservation (
  reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER NOT NULL REFERENCES hospital_request(request_id),
  unit_id INTEGER NOT NULL REFERENCES blood_unit(unit_id),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ISSUED','RELEASED')),
  reserved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- At most one active reservation per unit (no double booking).
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_unit ON reservation(unit_id) WHERE status = 'ACTIVE';
CREATE TABLE IF NOT EXISTS audit_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  txn_id TEXT,
  reason TEXT,
  before_state TEXT,
  after_state TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_units_match ON blood_unit(blood_group_id, component_id, status, expiry_date);
CREATE INDEX IF NOT EXISTS idx_requests_ready ON hospital_request(urgency, arrival_seq)
  WHERE process_state IN ('READY','WAITING');
CREATE INDEX IF NOT EXISTS idx_audit_entity_time ON audit_event(entity_type, entity_id, created_at DESC);
-- Audit trail for hospital renames (added by the OS upgrade). Keeps the original name for historical screens.
CREATE TABLE IF NOT EXISTS hospital_name_history (
  history_id INTEGER PRIMARY KEY AUTOINCREMENT,
  hospital_id INTEGER NOT NULL REFERENCES hospital(hospital_id),
  old_name TEXT NOT NULL,
  new_name TEXT NOT NULL,
  changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Additive final-phase tables; existing rows are retained by backend/final_phase.py.
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
