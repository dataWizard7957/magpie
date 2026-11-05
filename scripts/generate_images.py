"""
Generate keepsake images and quilt squares with Muse Image ($0.01 each).

    python scripts/generate_images.py                     # all items + all quilt squares + empty square
    python scripts/generate_images.py --only gen_cabin obj_key
    python scripts/generate_images.py --variants 3        # extra versions saved as <id>__v2.webp etc.
    python scripts/generate_images.py --items-only        # skip quilt squares
    python scripts/generate_images.py --quilt-only
    python scripts/generate_images.py --force             # overwrite existing images

Pick a variant by renaming it to <id>.webp. Every generated item is labeled
"AI-generated illustration" in data/images/manifest.json, which the app reads.
To use your own photo instead, drop it in data/images/<id>.jpg and remove its manifest entry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from magpie import storage  # noqa: E402
from magpie.quilt import muse_image, save_image  # noqa: E402

PROMPTS = json.loads((storage.DATA / "image_prompts.json").read_text(encoding="utf-8"))
SHEET = json.loads((storage.DATA / "rose.json").read_text(encoding="utf-8"))
MANIFEST = storage.IMAGES / "manifest.json"


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def gen(stem: str, prompt: str, folder: Path, variants: int, force: bool) -> bool:
    if storage.find_file(folder, stem) and not force:
        print(f"  skip {stem} (exists, use --force)")
        return False
    for v in range(1, variants + 1):
        name = stem if v == 1 else f"{stem}__v{v}"
        try:
            p = save_image(muse_image(prompt), folder, name)
            print(f"  saved {p.relative_to(storage.ROOT)}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {name}: {e}")
            return v > 1
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="item ids or story ids to generate")
    ap.add_argument("--variants", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--items-only", action="store_true")
    ap.add_argument("--quilt-only", action="store_true")
    a = ap.parse_args()
    only = set(a.only or [])
    count = 0

    if not a.quilt_only:
        print("Keepsakes and scenes")
        manifest = load_manifest()
        for item_id, prompt in PROMPTS["items"].items():
            if only and item_id not in only:
                continue
            if gen(item_id, prompt, storage.IMAGES, a.variants, a.force):
                manifest[item_id] = "AI-generated illustration"
                count += a.variants
        MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if not a.items_only:
        print("Quilt squares")
        style = PROMPTS["quilt_style"]
        for s in SHEET["stories"]:
            if only and s["id"] not in only:
                continue
            if gen(s["id"], f"{style}\nDesign: {s['quilt_square']}", storage.QUILT, a.variants, a.force):
                count += a.variants
        if not only or "empty" in only:
            if gen("empty", PROMPTS["empty_square"], storage.QUILT, 1, a.force):
                count += 1

    print(f"\nDone. {count} image(s) generated, about ${count * 0.01:.2f}.")


if __name__ == "__main__":
    main()
