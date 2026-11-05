"""'Talk with Grandma' cards: grounded conversation starters for relatives.

Details on a card are picked BY NUMBER from confirmed facts, so they can't be invented.
"""
from __future__ import annotations

import config

from . import engram, prompts, storage


def _card_view(c: dict) -> dict:
    s = engram.get_story(c["story_id"]) or {}
    return {**c, "story_title": s.get("title"), "decade": s.get("decade")}


def list_cards() -> list[dict]:
    return [_card_view(c) for c in storage.query("SELECT * FROM talk_cards ORDER BY id DESC")]


def generate_card(story_id: str) -> dict | None:
    """Make (or remake) the card for one confirmed story."""
    story = engram.get_story(story_id)
    if not story or story["status"] != "confirmed":
        return None
    facts = engram.list_facts("confirmed", story_id=story_id)
    if not facts:
        return None
    name = engram.persona().get("preferred_name", "Grandma")
    d = config.chat_json(
        [{"role": "system", "content": prompts.CARD_SYSTEM},
         {"role": "user", "content": prompts.card_user(story, facts, name)}],
        max_tokens=1024)
    ids = []
    for i in d.get("detail_ids") or []:
        try:
            ids.append(int(i))
        except (TypeError, ValueError):
            continue
    details = [facts[i]["text"] for i in ids if 0 <= i < len(facts)][:4] or [f["text"] for f in facts[:3]]
    opener = (d.get("opener") or "").strip() or f"Would you tell me about {story['title'].lower()}?"
    tip = (d.get("tip") or "").strip() or "If she remembers it differently, go with her version. It's her story."

    storage.execute("DELETE FROM talk_cards WHERE story_id=?", (story_id,))
    cid = storage.execute(
        "INSERT INTO talk_cards(story_id, opener, details, tip, generated_at) VALUES (?,?,?,?,?)",
        (story_id, opener, storage.dumps(details), tip, engram.now()))
    return _card_view(storage.one("SELECT * FROM talk_cards WHERE id=?", (cid,)))


def generate_all() -> list[dict]:
    out = []
    for s in engram.list_stories("confirmed"):
        try:
            c = generate_card(s["id"])
            if c:
                out.append(c)
        except Exception as e:  # noqa: BLE001
            out.append({"story_id": s["id"], "error": str(e)[:200]})
    return out
