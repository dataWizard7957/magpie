"""
Engram: the verified memory graph.

People, places, keepsakes (items), stories and facts. Every fact carries a source and a status
(suggested | confirmed | rejected). Only confirmed facts and stories ever reach the session agent.

Seed the demo persona:  python -m magpie.engram --reset [--mode full|items]
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime

from . import storage

FACT_SHEET = storage.DATA / "rose.json"
IMAGE_MANIFEST = storage.IMAGES / "manifest.json"

DEFAULT_LABELS = {
    "object": "Photographed keepsake",
    "document": "Photographed keepsake",
    "place": "Photo taken today",
    "photo": "Family photo",
    "generated": "AI-generated illustration",
    "upload": "Photographed by family",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def date_label(iso: str | None = None) -> str:
    """'Nov 2' style label."""
    d = datetime.fromisoformat(iso) if iso else datetime.now()
    return f"{d.strftime('%b')} {d.day}"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def load_fact_sheet() -> dict:
    return json.loads(FACT_SHEET.read_text(encoding="utf-8"))


_persona: dict | None = None


def persona() -> dict:
    """Persona (person + switch-topic lines + demo session + agent rules)."""
    global _persona
    if _persona is None:
        _persona = storage.get_meta("persona") or {}
    return _persona


def _manifest() -> dict:
    try:
        return json.loads(IMAGE_MANIFEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# People and places
# ---------------------------------------------------------------------------

def people() -> list[dict]:
    return storage.query("SELECT * FROM people ORDER BY born")


def people_names() -> list[str]:
    return [f"{p['name']} ({p['relation']})" for p in people()]


def place_names() -> list[str]:
    return [p["name"] for p in storage.query("SELECT name FROM places")]


def person_name(pid: str) -> str:
    row = storage.one("SELECT name FROM people WHERE id = ?", (pid,))
    return row["name"] if row else pid


def contributor_name(pid_or_name: str | None) -> str | None:
    """Accept 'priya' or 'Priya Rao' and return a short display name ('Priya')."""
    if not pid_or_name:
        return None
    row = storage.one("SELECT name FROM people WHERE id = ? OR name = ?", (pid_or_name.lower(), pid_or_name))
    return (row["name"] if row else pid_or_name).split()[0]


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def add_fact(text: str, *, source_type: str, source_label: str, kind: str = "event",
             subject: str | None = None, predicate: str | None = None, obj: str | None = None,
             item_id: str | None = None, story_id: str | None = None, contributor: str | None = None,
             status: str = "suggested", confirmed_by: str | None = None) -> int:
    """Add one fact. Model-made facts start as 'suggested'."""
    return storage.execute(
        """INSERT INTO facts(text, kind, subject, predicate, object, item_id, story_id, source_type,
                             source_label, contributor, status, confirmed_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (text.strip(), kind, subject, predicate, obj, item_id, story_id, source_type, source_label,
         contributor, status, confirmed_by, now()))


def source_text(f: dict) -> str:
    """Human-readable provenance, e.g. 'from photo, confirmed by Priya'."""
    s = f.get("source_label") or f.get("source_type") or "unknown source"
    if f.get("status") == "confirmed" and f.get("confirmed_by"):
        s += f", confirmed by {f['confirmed_by']}"
    elif f.get("status") == "rejected" and f.get("confirmed_by"):
        s += f", rejected by {f['confirmed_by']}"
    elif f.get("status") == "suggested":
        s += ", waiting for family"
    return s


def fact_view(f: dict) -> dict:
    return {**f, "source": source_text(f)}


def get_fact(fact_id: int) -> dict | None:
    f = storage.one("SELECT * FROM facts WHERE id = ?", (fact_id,))
    return fact_view(f) if f else None


def list_facts(status: str | None = None, item_id: str | None = None, story_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM facts WHERE 1=1", []
    if status:
        sql += " AND status = ?"; args.append(status)
    if item_id:
        sql += " AND item_id = ?"; args.append(item_id)
    if story_id:
        sql += " AND story_id = ?"; args.append(story_id)
    return [fact_view(f) for f in storage.query(sql + " ORDER BY id", tuple(args))]


def confirm_fact(fact_id: int, by: str) -> dict:
    storage.execute("UPDATE facts SET status='confirmed', confirmed_by=? WHERE id=?",
                    (contributor_name(by), fact_id))
    return get_fact(fact_id)


def edit_fact(fact_id: int, text: str, by: str) -> dict:
    """Edit and confirm in one step (the family's wording wins)."""
    storage.execute("UPDATE facts SET text=?, status='confirmed', confirmed_by=? WHERE id=?",
                    (text.strip(), contributor_name(by), fact_id))
    return get_fact(fact_id)


def reject_fact(fact_id: int, by: str) -> dict:
    storage.execute("UPDATE facts SET status='rejected', confirmed_by=? WHERE id=?",
                    (contributor_name(by), fact_id))
    return get_fact(fact_id)


# ---------------------------------------------------------------------------
# Items (keepsakes)
# ---------------------------------------------------------------------------

def item_image_path(item: dict):
    """Absolute image path: stored path, else data/images/<id>.<ext> if it exists."""
    p = storage.abs_path(item.get("image_path"))
    if p and p.exists():
        return p
    return storage.find_file(storage.IMAGES, item["id"])


def item_view(item: dict) -> dict:
    path = item_image_path(item)
    label = _manifest().get(item["id"]) or item.get("label") or DEFAULT_LABELS.get(item.get("type"), "Keepsake")
    story = storage.one("SELECT id, title, status, decade FROM stories WHERE id = ?", (item.get("story_id"),)) \
        if item.get("story_id") else None
    return {
        "id": item["id"], "type": item.get("type"), "title": item.get("title") or "A keepsake",
        "description": item.get("description"), "ai_description": item.get("ai_description"),
        "label": label, "story_id": item.get("story_id"), "story": story,
        "image_url": storage.media_url(storage.rel(path)) if path else None,
        "extracted": bool(item.get("extracted")),
    }


def get_item(item_id: str) -> dict | None:
    row = storage.one("SELECT * FROM items WHERE id = ?", (item_id,))
    return item_view(row) | {"image_path": row.get("image_path")} if row else None


def list_items() -> list[dict]:
    counts = {r["item_id"]: r for r in storage.query(
        """SELECT item_id, SUM(status='suggested') AS suggested, SUM(status='confirmed') AS confirmed
           FROM facts WHERE item_id IS NOT NULL GROUP BY item_id""")}
    out = []
    for row in storage.query("SELECT * FROM items ORDER BY created_at, id"):
        v = item_view(row)
        c = counts.get(row["id"], {})
        v["suggested"] = int(c.get("suggested") or 0)
        v["confirmed"] = int(c.get("confirmed") or 0)
        out.append(v)
    return out


def add_item(*, title: str, type_: str, image_rel: str | None, story_id: str | None = None,
             description: str | None = None, label: str | None = None, source: str = "upload",
             item_id: str | None = None) -> dict:
    item_id = item_id or new_id("item")
    storage.execute(
        """INSERT OR REPLACE INTO items(id, type, title, description, image_path, label, story_id, source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (item_id, type_, title, description, image_rel, label, story_id or None, source, now()))
    return get_item(item_id)


def set_item_extracted(item_id: str, ai_description: str | None) -> None:
    storage.execute("UPDATE items SET extracted=1, ai_description=COALESCE(?, ai_description) WHERE id=?",
                    (ai_description, item_id))


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------

def story_view(s: dict) -> dict:
    src = s.get("source_label") or "family"
    if s.get("status") == "confirmed" and s.get("confirmed_by"):
        src += f", confirmed by {s['confirmed_by']}"
    return {**s, "source": src, "people_names": [person_name(p) for p in (s.get("people") or [])]}


def get_story(story_id: str) -> dict | None:
    s = storage.one("SELECT * FROM stories WHERE id = ?", (story_id,))
    return story_view(s) if s else None


def list_stories(status: str | None = None) -> list[dict]:
    if status:
        rows = storage.query("SELECT * FROM stories WHERE status=? ORDER BY year, id", (status,))
    else:
        rows = storage.query("SELECT * FROM stories ORDER BY year, id")
    return [story_view(s) for s in rows]


def add_story(*, title: str, text: str, source_label: str, contributor: str | None = None,
              decade: str | None = None, year: int | None = None, people_ids: list[str] | None = None,
              place: str | None = None, item_id: str | None = None, hints: list[str] | None = None,
              quilt_subject: str | None = None, status: str = "suggested", confirmed_by: str | None = None,
              story_id: str | None = None) -> str:
    story_id = story_id or new_id("story")
    storage.execute(
        """INSERT OR REPLACE INTO stories(id, title, decade, year, text, people, place, item_id, hints, quilt_subject,
                                          status, source_label, contributor, confirmed_by, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (story_id, title, decade, year, text, storage.dumps(people_ids or []), place, item_id,
         storage.dumps(hints or []), quilt_subject, status, source_label, contributor, confirmed_by, now()))
    return story_id


def confirm_story(story_id: str, by: str) -> dict:
    """Confirm a story and its suggested facts. Links the keepsake to it if it had none."""
    who = contributor_name(by)
    s = get_story(story_id)
    if not s:
        raise KeyError(story_id)
    storage.execute("UPDATE stories SET status='confirmed', confirmed_by=? WHERE id=?", (who, story_id))
    storage.execute("UPDATE facts SET status='confirmed', confirmed_by=? WHERE story_id=? AND status='suggested'",
                    (who, story_id))
    if not storage.one("SELECT id FROM facts WHERE story_id=? AND status='confirmed'", (story_id,)):
        for sent in sentences(s["text"]):
            add_fact(sent, source_type=s.get("source_type") or "family_text", source_label=s["source_label"],
                     story_id=story_id, contributor=s.get("contributor"), status="confirmed", confirmed_by=who)
    if s.get("item_id"):
        storage.execute("UPDATE items SET story_id=? WHERE id=? AND (story_id IS NULL OR story_id='')",
                        (story_id, s["item_id"]))
    return get_story(story_id)


def reject_story(story_id: str, by: str) -> dict:
    who = contributor_name(by)
    storage.execute("UPDATE stories SET status='rejected', confirmed_by=? WHERE id=?", (who, story_id))
    storage.execute("UPDATE facts SET status='rejected', confirmed_by=? WHERE story_id=? AND status='suggested'",
                    (who, story_id))
    return get_story(story_id)


def set_quilt_subject(story_id: str, subject: str) -> None:
    storage.execute("UPDATE stories SET quilt_subject=? WHERE id=?", (subject, story_id))


# ---------------------------------------------------------------------------
# Voice notes
# ---------------------------------------------------------------------------

def voice_view(v: dict) -> dict:
    p = storage.abs_path(v.get("audio_path"))
    exists = bool(p and p.exists())
    return {**v, "speaker_name": contributor_name(v.get("speaker")), "has_audio": exists,
            "audio_url": storage.media_url(v.get("audio_path")) if exists else None,
            "play_in_tell": bool(v.get("play_in_tell"))}


def list_voice_notes() -> list[dict]:
    return [voice_view(v) for v in storage.query("SELECT * FROM voice_notes ORDER BY date, id")]


def get_voice_note(vid: str) -> dict | None:
    v = storage.one("SELECT * FROM voice_notes WHERE id=?", (vid,))
    return voice_view(v) if v else None


def add_voice_note(*, speaker: str, audio_rel: str, story_ids: list[str], play_in_tell: bool,
                   tell_intro: str | None = None, vid: str | None = None, date: str | None = None) -> dict:
    vid = vid or new_id("vn")
    storage.execute(
        """INSERT OR REPLACE INTO voice_notes(id, speaker, audio_path, transcript, story_ids, date, play_in_tell, tell_intro)
           VALUES (?,?,?,?,?,?,?,?)""",
        (vid, speaker, audio_rel, None, storage.dumps(story_ids), date or now()[:10], int(play_in_tell), tell_intro))
    return get_voice_note(vid)


def update_voice_note(vid: str, **fields) -> dict:
    for k, v in fields.items():
        if k == "story_ids":
            v = storage.dumps(v)
        storage.execute(f"UPDATE voice_notes SET {k}=? WHERE id=?", (v, vid))
    return get_voice_note(vid)


# ---------------------------------------------------------------------------
# Fragments
# ---------------------------------------------------------------------------

def add_fragment(session_id: str, item_id: str, story_id: str | None, text: str) -> int:
    return storage.execute(
        "INSERT INTO fragments(session_id, item_id, story_id, text, created_at) VALUES (?,?,?,?,?)",
        (session_id, item_id, story_id, text.strip(), now()))


def review_fragment(fragment_id: int, action: str, by: str) -> dict:
    """action: 'keep' (saved as her words in the archive) or 'dismiss'."""
    res = "kept" if action == "keep" else "dismissed"
    storage.execute("UPDATE fragments SET reviewed=1, resolution=?, reviewed_by=? WHERE id=?",
                    (res, contributor_name(by), fragment_id))
    return storage.one("SELECT * FROM fragments WHERE id=?", (fragment_id,))


# ---------------------------------------------------------------------------
# Views used by the session agent and the family view
# ---------------------------------------------------------------------------

def item_context(item_id: str) -> dict:
    """Everything the session agent may use for one keepsake: confirmed facts only."""
    item = get_item(item_id)
    if not item:
        raise KeyError(item_id)
    story = None
    if item.get("story_id"):
        s = storage.one("SELECT * FROM stories WHERE id=? AND status='confirmed'", (item["story_id"],))
        story = story_view(s) if s else None

    facts = storage.query("SELECT * FROM facts WHERE status='confirmed' AND item_id=?", (item_id,))
    if story:
        facts += storage.query("SELECT * FROM facts WHERE status='confirmed' AND story_id=?", (story["id"],))
        for pid in story.get("people") or []:
            facts += storage.query(
                "SELECT * FROM facts WHERE status='confirmed' AND predicate='relation' AND subject=?", (pid,))
    seen, unique = set(), []
    for f in facts:
        if f["id"] not in seen:
            seen.add(f["id"]); unique.append(f)

    clips = []
    if story:
        for v in list_voice_notes():
            if story["id"] in (v.get("story_ids") or []) and v["play_in_tell"] and v["has_audio"]:
                clips.append(v)
    return {"item": item, "story": story, "facts": unique,
            "hints": (story or {}).get("hints") or [], "voice_clips": clips}


def session_candidates(include_sim: bool = False) -> list[dict]:
    """Keepsakes that have a confirmed story, with how often each was shared before."""
    kinds = "('live','sim')" if include_sim else "('live')"
    counts: dict[str, int] = {}
    for s in storage.query(f"SELECT items FROM sessions WHERE kind IN {kinds}"):
        for i in s.get("items") or []:
            counts[i] = counts.get(i, 0) + 1
    out = []
    for row in storage.query(
            """SELECT i.*, s.title AS story_title, s.decade AS decade FROM items i
               JOIN stories s ON s.id = i.story_id WHERE s.status='confirmed' ORDER BY s.year"""):
        out.append({"id": row["id"], "title": row["title"], "story_id": row["story_id"],
                    "story_title": row["story_title"], "decade": row["decade"],
                    "has_image": item_image_path(row) is not None, "times_discussed": counts.get(row["id"], 0)})
    return out


def review_queue() -> dict:
    return {
        "stories": [{**s, "facts": list_facts("suggested", story_id=s["id"])} for s in list_stories("suggested")],
        "facts": [f for f in list_facts("suggested") if not _story_is_suggested(f.get("story_id"))],
        "fragments": storage.query(
            """SELECT f.*, i.title AS item_title FROM fragments f LEFT JOIN items i ON i.id=f.item_id
               JOIN sessions s ON s.id=f.session_id WHERE f.reviewed=0 AND s.kind='live' ORDER BY f.id"""),
    }


def _story_is_suggested(story_id: str | None) -> bool:
    if not story_id:
        return False
    s = storage.one("SELECT status FROM stories WHERE id=?", (story_id,))
    return bool(s and s["status"] == "suggested")


def archive() -> list[dict]:
    """Confirmed stories with their facts, her own words, voice notes and keepsakes."""
    voices = list_voice_notes()
    out = []
    for s in list_stories("confirmed"):
        out.append({
            **s,
            "facts": list_facts("confirmed", story_id=s["id"]),
            "her_words": storage.query(
                "SELECT text, created_at FROM fragments WHERE story_id=? AND resolution='kept' ORDER BY id", (s["id"],)),
            "items": [item_view(i) for i in storage.query("SELECT * FROM items WHERE story_id=?", (s["id"],))],
            "voice_notes": [v for v in voices if s["id"] in (v.get("story_ids") or [])],
        })
    return out


def graph() -> dict:
    """Nodes and edges for a simple Engram view."""
    nodes, edges = [{"id": "rose", "type": "person", "label": persona().get("name", "Rose")}], []
    for p in people():
        nodes.append({"id": p["id"], "type": "person", "label": p["name"]})
        edges.append({"from": "rose", "to": p["id"], "label": p["relation"]})
    for pl in storage.query("SELECT * FROM places"):
        nodes.append({"id": pl["id"], "type": "place", "label": pl["name"]})
    for s in list_stories("confirmed"):
        nodes.append({"id": s["id"], "type": "story", "label": s["title"]})
        for pid in s.get("people") or []:
            edges.append({"from": s["id"], "to": pid, "label": "with"})
        if s.get("place"):
            edges.append({"from": s["id"], "to": s["place"], "label": "at"})
    for i in list_items():
        nodes.append({"id": i["id"], "type": "item", "label": i["title"]})
        if i.get("story_id"):
            edges.append({"from": i["id"], "to": i["story_id"], "label": "keeps"})
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Seeding from data/rose.json
# ---------------------------------------------------------------------------

def seed(mode: str = "full", reset: bool = False) -> dict:
    """
    Load the demo persona.
      full  = people, places, keepsakes, voice notes AND confirmed stories (ready for sessions)
      items = everything except stories (build Engram live from keepsakes)
    """
    global _persona
    if reset:
        storage.reset()
    storage.init_db()
    sheet = load_fact_sheet()
    person = sheet["person"]
    first = person.get("preferred_name") or person["name"].split()[0]

    storage.set_meta("persona", {
        **person, "label": "Demo persona",
        "switch_topic": sheet.get("switch_topic", {}),
        "demo_session": sheet.get("demo_session", {}),
        "agent_rules": sheet.get("agent_rules", []),
    })
    _persona = None

    tree = "from the family tree"
    for p in sheet.get("people", []):
        storage.execute("INSERT OR REPLACE INTO people(id, name, relation, born, details) VALUES (?,?,?,?,?)",
                        (p["id"], p["name"], p["relation"], p.get("born"), p.get("details")))
        if not storage.one("SELECT id FROM facts WHERE predicate='relation' AND subject=?", (p["id"],)):
            add_fact(f"{p['name']} is {first}'s {p['relation']}.", kind="person", subject=p["id"],
                     predicate="relation", obj=p["relation"], source_type="family_text",
                     source_label=tree, status="confirmed", confirmed_by="Priya")

    for pl in sheet.get("places", []):
        storage.execute("INSERT OR REPLACE INTO places(id, name, location, details) VALUES (?,?,?,?)",
                        (pl["id"], pl["name"], pl.get("where"), pl.get("details")))

    for it in sheet.get("items", []):
        img = storage.find_file(storage.IMAGES, it["id"])
        add_item(item_id=it["id"], title=it.get("title") or it["what"], type_=it["type"],
                 image_rel=storage.rel(img) if img else None,
                 story_id=it.get("story") if mode == "full" else None,
                 description=it.get("what"), label=it.get("label"), source=it.get("source", "fact sheet"))

    for vn in sheet.get("voice_notes", []):
        add_voice_note(vid=vn["id"], speaker=vn["speaker"], audio_rel=vn["audio_path"],
                       story_ids=vn.get("story_ids", []) if mode == "full" else [],
                       play_in_tell=vn.get("play_in_tell", False), tell_intro=vn.get("tell_intro"),
                       date=vn.get("date"))

    if mode == "full":
        for s in sheet.get("stories", []):
            add_story(story_id=s["id"], title=s["title"], text=s["story"], decade=s.get("decade"),
                      year=s.get("year"), people_ids=s.get("people"), place=s.get("place"),
                      item_id=s.get("object_item"), hints=s.get("hints"), quilt_subject=s.get("quilt_square"),
                      status="confirmed", source_label="from the family fact sheet", contributor="Priya",
                      confirmed_by="Priya")
            if not storage.one("SELECT id FROM facts WHERE story_id=?", (s["id"],)):
                for sent in sentences(s["story"]):
                    add_fact(sent, story_id=s["id"], source_type="family_text",
                             source_label="from the family fact sheet", contributor="Priya",
                             status="confirmed", confirmed_by="Priya")

    return {"mode": mode, "items": len(sheet.get("items", [])), "stories": len(sheet.get("stories", []))
            if mode == "full" else 0}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed Engram with the demo persona from data/rose.json")
    ap.add_argument("--mode", choices=["full", "items"], default="full")
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    print(seed(**vars(ap.parse_args())))
