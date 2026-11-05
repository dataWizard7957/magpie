"""Keepsake photos and family stories -> suggested Engram facts (Muse Spark, JSON)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

import config

from . import engram, prompts

EXTRACT_PROVIDER = "meta"   # Muse Spark reads photos and handwriting best


def image_to_data_url(src: str | Path | bytes, max_side: int = 1024) -> str:
    """Resize to max 1024px and return a JPEG data URL (keeps cost low)."""
    if isinstance(src, str) and src.startswith("data:"):
        src = base64.b64decode(src.split(",", 1)[1])
    img = Image.open(io.BytesIO(src) if isinstance(src, bytes) else src)
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _source_label(item_type: str) -> tuple[str, str]:
    if item_type == "document":
        return "document", "from document"
    return "photo", "from photo"


def extract_from_item(item_id: str, note: str | None = None) -> list[dict]:
    """Read a keepsake photo and add suggested facts for the family to confirm."""
    item = engram.get_item(item_id)
    if not item:
        raise KeyError(item_id)
    path = engram.item_image_path(item)
    if not path:
        raise FileNotFoundError(f"No image for {item_id}. Add one to data/images/{item_id}.jpg")

    msgs = [
        {"role": "system", "content": prompts.EXTRACT_ITEM_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": prompts.extract_item_user(item, engram.people_names(), note)},
            {"type": "image_url", "image_url": {"url": image_to_data_url(path)}},
        ]},
    ]
    data = config.chat_json(msgs, provider=EXTRACT_PROVIDER, max_tokens=2048)
    source_type, label = _source_label(item.get("type", "object"))

    ids = []
    for f in (data.get("facts") or [])[:8]:
        text = (f.get("text") or "").strip()
        if not text:
            continue
        ids.append(engram.add_fact(
            text, kind=f.get("kind") or "object", subject=f.get("subject"), predicate=f.get("predicate"),
            obj=f.get("object"), item_id=item_id, story_id=item.get("story_id"),
            source_type=source_type, source_label=label, contributor="Magpie"))
    engram.set_item_extracted(item_id, data.get("description"))
    return [engram.get_fact(i) for i in ids]


def _year(v) -> int | None:
    try:
        y = int(str(v)[:4])
        return y if 1900 <= y <= 2100 else None
    except (TypeError, ValueError):
        return None


def extract_from_text(text: str, *, contributor: str, source_type: str, source_label: str,
                      item_id: str | None = None, story_id: str | None = None) -> dict:
    """
    Turn a relative's story (typed or transcribed) into a suggested story + suggested facts.
    If story_id is given, the facts are added to that existing story instead.
    """
    p = engram.persona()
    existing = engram.get_story(story_id) if story_id else None
    msgs = [
        {"role": "system", "content": prompts.EXTRACT_TEXT_SYSTEM},
        {"role": "user", "content": prompts.extract_text_user(
            text, engram.contributor_name(contributor) or contributor, p.get("name", "her"),
            engram.people_names(), engram.place_names(), existing["title"] if existing else None)},
    ]
    data = config.chat_json(msgs, provider=EXTRACT_PROVIDER, max_tokens=2048)

    new_story = None
    if not existing and data.get("story"):
        s = data["story"]
        name_to_id = {pp["name"].lower(): pp["id"] for pp in engram.people()}
        name_to_id.update({pp["name"].split()[0].lower(): pp["id"] for pp in engram.people()})
        pids = [name_to_id[n.lower()] for n in (s.get("people") or []) if n and n.lower() in name_to_id]
        sid = engram.add_story(
            title=s.get("title") or "A family story", text=s.get("text") or text, decade=s.get("decade"),
            year=_year(s.get("year")), people_ids=pids, place=s.get("place"), item_id=item_id,
            source_label=source_label, contributor=engram.contributor_name(contributor))
        new_story = engram.get_story(sid)
        story_id = sid

    ids = []
    for f in (data.get("facts") or [])[:8]:
        t = (f.get("text") or "").strip()
        if t:
            ids.append(engram.add_fact(
                t, kind=f.get("kind") or "event", subject=f.get("subject"), predicate=f.get("predicate"),
                obj=f.get("object"), item_id=item_id, story_id=story_id, source_type=source_type,
                source_label=source_label, contributor=engram.contributor_name(contributor)))
    return {"story": new_story, "facts": [engram.get_fact(i) for i in ids]}
