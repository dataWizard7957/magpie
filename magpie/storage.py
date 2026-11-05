"""SQLite storage for Engram, sessions and the review queue (data/magpie.db)."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IMAGES = DATA / "images"
QUILT = DATA / "quilt"
VOICE = DATA / "voice"
REPORTS = DATA / "reports"
DB_PATH = Path(os.getenv("MAGPIE_DB", str(DATA / "magpie.db")))

for _d in (IMAGES, QUILT, VOICE, REPORTS):
    _d.mkdir(parents=True, exist_ok=True)

IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png")
AUDIO_EXTS = (".webm", ".ogg", ".m4a", ".mp3", ".wav")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY, name TEXT, relation TEXT, born INTEGER, details TEXT);

CREATE TABLE IF NOT EXISTS places (
    id TEXT PRIMARY KEY, name TEXT, location TEXT, details TEXT);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, type TEXT, title TEXT, description TEXT, ai_description TEXT,
    image_path TEXT, label TEXT, story_id TEXT, source TEXT,
    extracted INTEGER DEFAULT 0, created_at TEXT);

CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY, title TEXT, decade TEXT, year INTEGER, text TEXT,
    people TEXT, place TEXT, item_id TEXT, hints TEXT, quilt_subject TEXT,
    status TEXT, source_label TEXT, contributor TEXT, confirmed_by TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT,
    subject TEXT, predicate TEXT, object TEXT, item_id TEXT, story_id TEXT,
    source_type TEXT, source_label TEXT, contributor TEXT,
    status TEXT, confirmed_by TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, item_id TEXT, story_id TEXT,
    text TEXT, created_at TEXT, reviewed INTEGER DEFAULT 0, resolution TEXT, reviewed_by TEXT);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, kind TEXT, theme TEXT, items TEXT, state TEXT,
    summary TEXT, started_at TEXT, ended_at TEXT);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, text TEXT,
    mode TEXT, item_id TEXT, audio_url TEXT, grounding TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS voice_notes (
    id TEXT PRIMARY KEY, speaker TEXT, audio_path TEXT, transcript TEXT,
    story_ids TEXT, date TEXT, play_in_tell INTEGER DEFAULT 0, tell_intro TEXT);

CREATE TABLE IF NOT EXISTS talk_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, story_id TEXT, opener TEXT,
    details TEXT, tip TEXT, generated_at TEXT);

CREATE INDEX IF NOT EXISTS idx_facts_item ON facts(item_id);
CREATE INDEX IF NOT EXISTS idx_facts_story ON facts(story_id);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
"""

JSON_COLS = {"people", "hints", "story_ids", "items", "state", "details", "grounding"}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def reset() -> None:
    """Delete the database file (and WAL files)."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def query(sql: str, args: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        return [_decode(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    """Run a write statement and return lastrowid."""
    conn = connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def set_meta(key: str, value) -> None:
    execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, dumps(value)))


def get_meta(key: str, default=None):
    row = one("SELECT value FROM meta WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def is_empty() -> bool:
    return one("SELECT COUNT(*) AS n FROM items")["n"] == 0


def find_file(folder: Path, stem: str, exts: tuple = IMAGE_EXTS) -> Path | None:
    """Find data/<folder>/<stem>.<ext> for any allowed extension."""
    for ext in exts:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def rel(path: Path | str | None) -> str | None:
    """Path relative to data/, e.g. 'images/obj_key.webp'."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    try:
        return p.relative_to(DATA).as_posix()
    except ValueError:
        return p.as_posix()


def abs_path(rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    p = Path(rel_path)
    if p.is_absolute():
        return p
    if rel_path.startswith("data/"):
        return ROOT / rel_path
    return DATA / rel_path


def media_url(rel_path: str | None) -> str | None:
    """Browser URL for a file under data/ (served at /media/...)."""
    if not rel_path:
        return None
    r = rel(abs_path(rel_path))
    return f"/media/{r}" if r else None
