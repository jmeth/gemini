"""
Storage for the Directives tab: poses, sounds and rules.

Same SQLite file as the command registry, a separate connection. Rules keep their
triggers and actions as JSON, validated on the way in so the engine can trust what
it reads back. Sound files live in sounds/, next to recordings/.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "so101.db"
SOUNDS_DIR = Path(__file__).parent.parent.parent / "sounds"

SIDES = ("left", "right")
SOUND_EXTS = {".wav", ".mp3", ".ogg", ".m4a", ".aac", ".aiff", ".aif", ".flac"}
MAX_SOUND_BYTES = 25 * 1024 * 1024

TRIGGER_TYPES = ("voice", "button", "timer", "object", "startup")
ACTION_TYPES = ("movement", "pose", "sound", "wait")
BUSY_POLICIES = ("skip", "queue", "interrupt")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS poses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                arms TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                duration_s REAL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                triggers TEXT NOT NULL DEFAULT '[]',
                actions TEXT NOT NULL DEFAULT '[]',
                busy_policy TEXT NOT NULL DEFAULT 'skip',
                cooldown_s REAL NOT NULL DEFAULT 0,
                source TEXT,
                created_at TEXT NOT NULL,
                last_fired_at TEXT
            );
            """
        )
        _conn.commit()
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Directives store ready at %s", DB_PATH)
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _name(value, fallback: str) -> str:
    return (str(value or "").strip() or fallback)[:80]


# --------------------------------------------------------------------------- poses

def _pose(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "arms": json.loads(r["arms"] or "{}"),
            "created_at": r["created_at"]}


def _clean_arms(arms) -> dict[str, dict[str, float]]:
    """{side: {joint: degrees}} with unknown sides and non-numbers dropped."""
    out: dict[str, dict[str, float]] = {}
    for side, joints in (arms or {}).items():
        if side not in SIDES or not isinstance(joints, dict):
            continue
        clean = {str(j): round(float(v), 2) for j, v in joints.items()
                 if isinstance(v, (int, float))}
        if clean:
            out[side] = clean
    return out


def list_poses() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM poses ORDER BY name COLLATE NOCASE").fetchall()
    return [_pose(r) for r in rows]


def get_pose(pose_id: int) -> dict | None:
    with _lock:
        r = _db().execute("SELECT * FROM poses WHERE id = ?", (pose_id,)).fetchone()
    return _pose(r) if r else None


def create_pose(name: str, arms: dict) -> dict:
    with _lock:
        cur = _db().execute(
            "INSERT INTO poses (name, arms, created_at) VALUES (?, ?, ?)",
            (_name(name, "pose"), json.dumps(_clean_arms(arms)), _now()))
        _db().commit()
        pid = cur.lastrowid
    return get_pose(pid)


def update_pose(pose_id: int, name=None, arms=None) -> dict | None:
    cur = get_pose(pose_id)
    if not cur:
        return None
    with _lock:
        _db().execute("UPDATE poses SET name = ?, arms = ? WHERE id = ?", (
            cur["name"] if name is None else _name(name, cur["name"]),
            json.dumps(cur["arms"] if arms is None else _clean_arms(arms)), pose_id))
        _db().commit()
    return get_pose(pose_id)


def delete_pose(pose_id: int) -> bool:
    with _lock:
        n = _db().execute("DELETE FROM poses WHERE id = ?", (pose_id,)).rowcount
        _db().commit()
    return n > 0


# --------------------------------------------------------------------------- sounds

def _sound(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "filename": r["filename"],
            "size": r["size"], "duration_s": r["duration_s"], "created_at": r["created_at"]}


def sound_path(sound: dict) -> Path:
    return SOUNDS_DIR / sound["filename"]


def list_sounds() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM sounds ORDER BY name COLLATE NOCASE").fetchall()
    return [_sound(r) for r in rows]


def get_sound(sound_id: int) -> dict | None:
    with _lock:
        r = _db().execute("SELECT * FROM sounds WHERE id = ?", (sound_id,)).fetchone()
    return _sound(r) if r else None


def create_sound(name: str, original_filename: str, data: bytes) -> dict:
    """Save an uploaded file. Raises ValueError for a type or size we will not keep."""
    ext = Path(original_filename or "").suffix.lower()
    if ext not in SOUND_EXTS:
        raise ValueError(f"Unsupported sound type '{ext or '?'}' — use {', '.join(sorted(SOUND_EXTS))}")
    if not data:
        raise ValueError("The file is empty")
    if len(data) > MAX_SOUND_BYTES:
        raise ValueError(f"Sound is larger than {MAX_SOUND_BYTES // (1024 * 1024)} MB")

    stem = _name(name, Path(original_filename).stem or "sound")
    with _lock:
        cur = _db().execute(
            "INSERT INTO sounds (name, filename, size, created_at) VALUES (?, '', ?, ?)",
            (stem, len(data), _now()))
        sid = cur.lastrowid
        filename = f"{sid}{ext}"          # our own name: nothing from the client reaches the path
        (SOUNDS_DIR / filename).write_bytes(data)
        _db().execute("UPDATE sounds SET filename = ?, duration_s = ? WHERE id = ?",
                      (filename, _wav_duration(SOUNDS_DIR / filename), sid))
        _db().commit()
    return get_sound(sid)


def _wav_duration(path: Path) -> float | None:
    if path.suffix.lower() != ".wav":
        return None
    try:
        import wave
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return None


def rename_sound(sound_id: int, name: str) -> dict | None:
    cur = get_sound(sound_id)
    if not cur:
        return None
    with _lock:
        _db().execute("UPDATE sounds SET name = ? WHERE id = ?", (_name(name, cur["name"]), sound_id))
        _db().commit()
    return get_sound(sound_id)


def delete_sound(sound_id: int) -> bool:
    cur = get_sound(sound_id)
    if not cur:
        return False
    with _lock:
        _db().execute("DELETE FROM sounds WHERE id = ?", (sound_id,))
        _db().commit()
    try:
        sound_path(cur).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove %s: %s", cur["filename"], exc)
    return True


# --------------------------------------------------------------------------- rules

def _rule(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]),
        "triggers": json.loads(r["triggers"] or "[]"),
        "actions": json.loads(r["actions"] or "[]"),
        "busy_policy": r["busy_policy"], "cooldown_s": r["cooldown_s"],
        "source": r["source"], "created_at": r["created_at"],
        "last_fired_at": r["last_fired_at"],
    }


def _sides(value) -> list[str]:
    """Which arms an action targets. Absent means both."""
    if value is None:
        return list(SIDES)
    sides = [s for s in SIDES if s in value]
    if not sides:
        raise ValueError("An action needs at least one arm (left, right)")
    return sides


def _num(value, lo: float, hi: float, default: float, what: str) -> float:
    try:
        v = float(default if value is None else value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return v


def clean_trigger(t: dict) -> dict:
    kind = t.get("type")
    if kind not in TRIGGER_TYPES:
        raise ValueError(f"Unknown trigger type '{kind}'")
    if kind == "voice":
        phrases = []
        for p in t.get("phrases") or []:
            p = str(p).strip()
            if p and p not in phrases:
                phrases.append(p)
        if not phrases:
            raise ValueError("A voice trigger needs at least one phrase")
        return {"type": "voice", "phrases": phrases}
    if kind == "button":
        try:
            return {"type": "button", "button": int(t["button"])}
        except (KeyError, TypeError, ValueError):
            raise ValueError("A button trigger needs a button number")
    if kind == "timer":
        if t.get("mode") == "daily":
            at = str(t.get("at") or "")
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
                raise ValueError("A daily timer needs a time like 14:30")
            return {"type": "timer", "mode": "daily", "at": at}
        return {"type": "timer", "mode": "interval",
                "seconds": _num(t.get("seconds"), 1, 86400, 60, "Timer interval")}
    if kind == "object":
        label = str(t.get("label") or "").strip().lower()
        if not label:
            raise ValueError("An object trigger needs a label (e.g. scissors)")
        event = t.get("event") if t.get("event") in ("appears", "disappears") else "appears"
        return {"type": "object", "label": label, "event": event,
                "min_conf": _num(t.get("min_conf"), 0.05, 1.0, 0.5, "Minimum confidence")}
    return {"type": "startup"}


def clean_action(a: dict) -> dict:
    kind = a.get("type")
    if kind not in ACTION_TYPES:
        raise ValueError(f"Unknown action type '{kind}'")
    if kind == "movement":
        if not a.get("id"):
            raise ValueError("A movement action needs a movement")
        return {"type": "movement", "id": str(a["id"]), "arms": _sides(a.get("arms")),
                "speed": _num(a.get("speed"), 0.1, 4.0, 1.0, "Speed")}
    if kind == "pose":
        try:
            pid = int(a["id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("A pose action needs a pose")
        return {"type": "pose", "id": pid, "arms": _sides(a.get("arms")),
                "duration_s": _num(a.get("duration_s"), 0.1, 30, 1.5, "Duration")}
    if kind == "sound":
        try:
            sid = int(a["id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("A sound action needs a sound")
        return {"type": "sound", "id": sid, "volume": _num(a.get("volume"), 0, 1, 1.0, "Volume"),
                "wait": bool(a.get("wait", False))}
    return {"type": "wait", "seconds": _num(a.get("seconds"), 0, 600, 1.0, "Wait")}


def clean_rule(body: dict) -> dict:
    triggers = [clean_trigger(t) for t in body.get("triggers") or []]
    actions = [clean_action(a) for a in body.get("actions") or []]
    if not triggers:
        raise ValueError("A rule needs at least one trigger")
    if not actions:
        raise ValueError("A rule needs at least one action")
    policy = body.get("busy_policy", "skip")
    if policy not in BUSY_POLICIES:
        raise ValueError(f"busy_policy must be one of {', '.join(BUSY_POLICIES)}")
    return {"name": _name(body.get("name"), "rule"), "triggers": triggers, "actions": actions,
            "busy_policy": policy, "cooldown_s": _num(body.get("cooldown_s"), 0, 86400, 0, "Cooldown")}


def list_rules() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM rules ORDER BY name COLLATE NOCASE").fetchall()
    return [_rule(r) for r in rows]


def get_rule(rule_id: int) -> dict | None:
    with _lock:
        r = _db().execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    return _rule(r) if r else None


def create_rule(body: dict, enabled: bool = True, source: str | None = None) -> dict:
    c = clean_rule(body)
    with _lock:
        cur = _db().execute(
            "INSERT INTO rules (name, enabled, triggers, actions, busy_policy, cooldown_s, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (c["name"], 1 if enabled else 0, json.dumps(c["triggers"]), json.dumps(c["actions"]),
             c["busy_policy"], c["cooldown_s"], source, _now()))
        _db().commit()
        rid = cur.lastrowid
    return get_rule(rid)


def update_rule(rule_id: int, body: dict) -> dict | None:
    cur = get_rule(rule_id)
    if not cur:
        return None
    merged = {**{k: cur[k] for k in ("name", "triggers", "actions", "busy_policy", "cooldown_s")}, **body}
    c = clean_rule(merged)
    enabled = cur["enabled"] if "enabled" not in body else bool(body["enabled"])
    with _lock:
        _db().execute(
            "UPDATE rules SET name = ?, enabled = ?, triggers = ?, actions = ?, busy_policy = ?, "
            "cooldown_s = ? WHERE id = ?",
            (c["name"], 1 if enabled else 0, json.dumps(c["triggers"]), json.dumps(c["actions"]),
             c["busy_policy"], c["cooldown_s"], rule_id))
        _db().commit()
    return get_rule(rule_id)


def set_rule_enabled(rule_id: int, enabled: bool) -> dict | None:
    with _lock:
        _db().execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
        _db().commit()
    return get_rule(rule_id)


def touch_rule(rule_id: int) -> None:
    with _lock:
        _db().execute("UPDATE rules SET last_fired_at = ? WHERE id = ?", (_now(), rule_id))
        _db().commit()


def delete_rule(rule_id: int) -> bool:
    with _lock:
        n = _db().execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount
        _db().commit()
    return n > 0
