"""SQLite connection helpers. Swap for psycopg2 when porting to PostgreSQL."""
import os
import sqlite3
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schema.sql")
DEFAULT_DB = os.environ.get("EBMS_DB", os.path.join(HERE, "ebms.db"))


def connect(path=None):
    conn = sqlite3.connect(path or DEFAULT_DB, timeout=5, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        conn.executescript(fh.read())


@contextmanager
def transaction(conn):
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-write is race-free."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
