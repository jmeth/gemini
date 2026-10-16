"""
SQLite-backed command registry: a name, its voice trigger phrases, and the
recording to play when one is heard.

Kept deliberately small — stdlib sqlite3 behind a lock, one table. The
connection is opened lazily and shared (check_same_thread=False) because the
FastAPI endpoints call these from the default executor threads.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "so101.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                triggers     TEXT NOT NULL DEFAULT '[]',
                recording_id TEXT,
                created_at   TEXT NOT NULL,
                enabled      INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        # Migration: add `enabled` to registries created before it existed.
        cols = {r["name"] for r in _conn.execute("PRAGMA table_info(commands)")}
        if "enabled" not in cols:
            _conn.execute(
                "ALTER TABLE commands ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
            )
        # Migration: add `button` (an arcade button index) the same way.
        if "button" not in cols:
            _conn.execute("ALTER TABLE commands ADD COLUMN button INTEGER")
        _conn.commit()
        logger.info("Command registry ready at %s", DB_PATH)
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_triggers(triggers) -> list[str]:
    out: list[str] = []
    for t in triggers or []:
        t = str(t).strip()
        if t and t not in out:
            out.append(t)
    return out


def _row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "triggers": json.loads(r["triggers"] or "[]"),
        "recording_id": r["recording_id"],
        "created_at": r["created_at"],
        "enabled": bool(r["enabled"]),
        "button": r["button"],
    }


def command_for_button(button: int) -> dict | None:
    """The enabled command bound to this arcade button, if any."""
    with _lock:
        r = _db().execute(
            "SELECT * FROM commands WHERE button = ? AND enabled = 1 ORDER BY id LIMIT 1",
            (button,),
        ).fetchone()
    return _row(r) if r else None


def list_commands() -> list[dict]:
    with _lock:
        rows = _db().execute(
            "SELECT * FROM commands ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [_row(r) for r in rows]


def get_command(cmd_id: int) -> dict | None:
    with _lock:
        r = _db().execute("SELECT * FROM commands WHERE id = ?", (cmd_id,)).fetchone()
    return _row(r) if r else None


def create_command(name: str, triggers, recording_id: str | None,
                   enabled: bool = True, button: int | None = None) -> dict:
    with _lock:
        cur = _db().execute(
            "INSERT INTO commands (name, triggers, recording_id, created_at, enabled, button) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                (name or "").strip() or "command",
                json.dumps(_clean_triggers(triggers)),
                recording_id or None,
                _now(),
                1 if enabled else 0,
                button,
            ),
        )
        _db().commit()
        cmd_id = cur.lastrowid
    return get_command(cmd_id)


# Sentinel: recording_id is nullable, so "not passed" must differ from "set to None".
_UNSET = object()


def update_command(
    cmd_id: int,
    name: str | None = None,
    triggers=None,
    recording_id=_UNSET,
    enabled: bool | None = None,
    button=_UNSET,
) -> dict | None:
    existing = get_command(cmd_id)
    if not existing:
        return None
    new_name = existing["name"] if name is None else ((name or "").strip() or existing["name"])
    new_triggers = existing["triggers"] if triggers is None else _clean_triggers(triggers)
    new_rec = existing["recording_id"] if recording_id is _UNSET else (recording_id or None)
    new_enabled = existing["enabled"] if enabled is None else bool(enabled)
    new_button = existing["button"] if button is _UNSET else button
    with _lock:
        _db().execute(
            "UPDATE commands SET name = ?, triggers = ?, recording_id = ?, enabled = ?, "
            "button = ? WHERE id = ?",
            (new_name, json.dumps(new_triggers), new_rec, 1 if new_enabled else 0,
             new_button, cmd_id),
        )
        _db().commit()
    return get_command(cmd_id)


def delete_command(cmd_id: int) -> bool:
    with _lock:
        cur = _db().execute("DELETE FROM commands WHERE id = ?", (cmd_id,))
        _db().commit()
    return cur.rowcount > 0
