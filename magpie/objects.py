"""(Stretch) Hold up the real keepsake: webcam snapshot -> which known keepsake is it?

Runs on the active provider, so in the final demo Muse Glimmer's image input does the matching.
"""
from __future__ import annotations

import config

from . import engram, prompts
from .extract import image_to_data_url

MIN_CONFIDENCE = 0.55


def match(image_data_url: str) -> dict:
    """Return {"item_id", "confidence", "seen", "item"} (item_id None if no confident match)."""
    items = [i for i in engram.list_items()]
    data_url = image_to_data_url(image_data_url, max_side=768)
    d = config.chat_json(
        [{"role": "system", "content": prompts.OBJECT_SYSTEM},
         {"role": "user", "content": [
             {"type": "text", "text": prompts.object_user(items)},
             {"type": "image_url", "image_url": {"url": data_url}},
         ]}],
        max_tokens=1024)
    ids = {i["id"] for i in items}
    item_id = d.get("item_id") if d.get("item_id") in ids else None
    try:
        conf = float(d.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < MIN_CONFIDENCE:
        item_id = None
    return {"item_id": item_id, "confidence": conf, "seen": d.get("seen") or "",
            "item": engram.get_item(item_id) if item_id else None}
