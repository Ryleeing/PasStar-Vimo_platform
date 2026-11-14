"""SQLite connection, schema creation, clock and ids. The database file defaults to data/the_pass.db (not in the repo).

Outside a transaction every statement commits on its own. Inside `with transaction():` all statements run on one
connection and commit together; an exception rolls every one of them back."""
from __future__ import annotations

import os
import pathlib
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB_PATH = pathlib.Path(os.environ.get("THE_PASS_DB", ROOT / "data" / "the_pass.db"))
SCHEMA = pathlib.Path(__file__).with_name("schema.sql")


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)   # autocommit per statement
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    """Create whatever tables of schema.sql do not exist yet (the product tables may already be there)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    sql = re.sub(r"CREATE (VIRTUAL TABLE|TABLE|INDEX) ", r"CREATE \1 IF NOT EXISTS ", SCHEMA.read_text(encoding="utf-8"))
    with closing(connect()) as c:
        # an audit_log created before the hold columns existed gets them here, filled from the hashed payload
        columns = {r["name"] for r in c.execute("PRAGMA table_info(audit_log)")}
        for column in ("buyer_id", "agent_id", "reason_code"):
            if columns and column not in columns:
                c.execute(f"ALTER TABLE audit_log ADD COLUMN {column} TEXT")
                c.execute(f"UPDATE audit_log SET {column} = json_extract(payload_json, '$.payload.{column}')")
        c.executescript(sql)


_tx: ContextVar[sqlite3.Connection | None] = ContextVar("_tx", default=None)


@contextmanager
def transaction():
    """One all-or-nothing unit of work. BEGIN IMMEDIATE takes the write lock up front, so reads inside see no
    concurrent writes (the audit hash chain relies on this). A nested transaction joins the outer one."""
    if _tx.get() is not None:
        yield
        return
    with closing(connect()) as c:
        c.execute("BEGIN IMMEDIATE")
        token = _tx.set(c)
        try:
            yield
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            _tx.reset(token)


@contextmanager
def _conn():
    tx = _tx.get()
    if tx is not None:
        yield tx
    else:
        with closing(connect()) as c:
            yield c


def run(sql: str, params: tuple = ()) -> int:
    """Returns the rowid of the inserted row (for INSERT)."""
    with _conn() as c:
        return c.execute(sql, params).lastrowid


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute(sql, params).fetchone()


def all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(sql, params).fetchall()


def now() -> datetime:
    return datetime.now().astimezone()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"
