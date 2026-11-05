"""Memory Quilt: each confirmed story becomes one embroidered square (Muse Image).

Rows are decades. Each row has room for 3 squares; empty squares wait for stories still to come.
Squares show places and objects only. No faces, no people.
"""
from __future__ import annotations

import base64
import io
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config

from . import engram, prompts, storage

PROMPTS_FILE = storage.DATA / "image_prompts.json"
COLS = 3


def _prompts() -> dict:
    return json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))


def quilt_style() -> str:
    return _prompts()["quilt_style"]


# ---------------------------------------------------------------------------
# Muse Image
# ---------------------------------------------------------------------------

def muse_image(prompt: str, size: str = "1024x1024") -> bytes:
    """Generate one image with Muse Image ($0.01). Returns raw bytes."""
    client, _ = config.get_client("meta")
    r = client.images.generate(model=config.MUSE_IMAGE_MODEL, prompt=prompt, n=1, size=size,
                               response_format="b64_json")
    return base64.b64decode(r.data[0].b64_json)


def save_image(data: bytes, folder: Path, stem: str) -> Path:
    """Save bytes with the right extension (Muse Image usually returns WebP)."""
    fmt = (Image.open(io.BytesIO(data)).format or "WEBP").lower()
    ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt, ".webp")
    for old in folder.glob(f"{stem}.*"):
        old.unlink()
    p = folder / f"{stem}{ext}"
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# Squares
# ---------------------------------------------------------------------------

def square_path(story_id: str) -> Path | None:
    return storage.find_file(storage.QUILT, story_id)


def subject_for(story: dict) -> str:
    """The square's design. Uses the family's subject, or asks Muse Spark for one (places/objects only)."""
    if story.get("quilt_subject"):
        return story["quilt_subject"]
    subject = config.chat(
        [{"role": "system", "content": prompts.QUILT_SUBJECT_SYSTEM},
         {"role": "user", "content": prompts.quilt_subject_user(story["text"])}],
        provider="meta", max_tokens=512).strip().strip('"')
    engram.set_quilt_subject(story["id"], subject)
    return subject


def generate_square(story_id: str, subject: str | None = None, force: bool = False) -> Path:
    """Stitch one square for a confirmed story."""
    existing = square_path(story_id)
    if existing and not force:
        return existing
    if subject is None:
        story = engram.get_story(story_id)
        if not story or story["status"] != "confirmed":
            raise ValueError("Only confirmed stories get a quilt square.")
        subject = subject_for(story)
    prompt = f"{quilt_style()}\nDesign: {subject}"
    return save_image(muse_image(prompt), storage.QUILT, story_id)


def generate_empty_square(force: bool = False) -> Path:
    existing = storage.find_file(storage.QUILT, "empty")
    if existing and not force:
        return existing
    return save_image(muse_image(_prompts()["empty_square"]), storage.QUILT, "empty")


def layout() -> dict:
    """Rows by decade, 3 squares per row, empty squares for stories still to come."""
    stories = engram.list_stories("confirmed")
    by_decade: dict[str, list[dict]] = {}
    for s in stories:
        by_decade.setdefault(s.get("decade") or "Undated", []).append(s)
    rows = []
    for decade in sorted(by_decade, key=lambda d: (d == "Undated", d)):
        group = by_decade[decade]
        squares = []
        for s in group:
            p = square_path(s["id"])
            squares.append({"story_id": s["id"], "title": s["title"], "year": s.get("year"),
                            "url": storage.media_url(storage.rel(p)) if p else None})
        while len(squares) % COLS:
            squares.append({"story_id": None, "title": None, "url": None})
        rows.append({"decade": decade, "squares": squares})
    filled = sum(1 for r in rows for q in r["squares"] if q["url"])
    empty = storage.find_file(storage.QUILT, "empty")
    out_png = storage.QUILT / "quilt.png"
    return {"rows": rows, "filled": filled, "stories": len(stories),
            "empty_url": storage.media_url(storage.rel(empty)) if empty else None,
            "export_url": storage.media_url(storage.rel(out_png)) if out_png.exists() else None,
            "label": "Squares are AI-generated illustrations (Muse Image)."}


# ---------------------------------------------------------------------------
# Export one shareable image
# ---------------------------------------------------------------------------

LINEN = (238, 229, 211)
THREAD = (164, 84, 52)
INK = (70, 58, 48)


def _font(size: int):
    for name in ("DejaVuSerif.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _dashed_rect(d: ImageDraw.ImageDraw, box: tuple, dash: int = 10, gap: int = 7, width: int = 3,
                 color=THREAD) -> None:
    x0, y0, x1, y1 = box
    for x in range(x0, x1, dash + gap):
        d.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=width)
        d.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=width)
    for y in range(y0, y1, dash + gap):
        d.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=width)
        d.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=width)


def export(cell: int = 320, gap: int = 26) -> Path:
    """Stitch every square into data/quilt/quilt.png."""
    lay = layout()
    rows = lay["rows"] or [{"decade": "", "squares": [{"url": None}] * COLS}]
    left, top, bottom = 150, 140, 90
    w = left + COLS * cell + (COLS + 1) * gap
    h = top + len(rows) * (cell + gap) + gap + bottom
    img = Image.new("RGB", (w, h), LINEN)
    d = ImageDraw.Draw(img)

    rnd = random.Random(7)   # subtle linen weave
    for _ in range(w * h // 60):
        x, y = rnd.randrange(w), rnd.randrange(h)
        shade = rnd.randint(-10, 6)
        d.point((x, y), fill=tuple(max(0, min(255, c + shade)) for c in LINEN))

    name = engram.persona().get("preferred_name", "Her")
    d.text((left + gap, 48), f"{name}'s Memory Quilt", font=_font(44), fill=INK)
    empty_path = storage.find_file(storage.QUILT, "empty")

    for r, row in enumerate(rows):
        y = top + gap + r * (cell + gap)
        d.text((28, y + cell // 2 - 14), row["decade"], font=_font(28), fill=INK)
        for c, sq in enumerate(row["squares"]):
            x = left + gap + c * (cell + gap)
            src = storage.abs_path(sq["url"].removeprefix("/media/")) if sq.get("url") else empty_path
            if src and Path(src).exists():
                tile = Image.open(src).convert("RGB").resize((cell, cell))
                if not sq.get("url"):
                    tile = Image.blend(tile, Image.new("RGB", tile.size, LINEN), 0.35)
                img.paste(tile, (x, y))
            else:
                d.rectangle([x, y, x + cell, y + cell], fill=(244, 238, 226))
            _dashed_rect(d, (x - 8, y - 8, x + cell + 8, y + cell + 8))

    d.text((left + gap, h - 60),
           "Demo persona. Squares are AI-generated illustrations (Muse Image). Made with Magpie.",
           font=_font(18), fill=INK)
    out = storage.QUILT / "quilt.png"
    img.save(out)
    return out
