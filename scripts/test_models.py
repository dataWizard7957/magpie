"""
Smoke test the models Magpie uses, with timings.

    python scripts/test_models.py                  # Muse Spark (Meta) + Glimmer (if GLIMMER_URL is set)
    python scripts/test_models.py --vision         # + Muse Spark reading a test image
    python scripts/test_models.py --image          # + one Muse Image generation ($0.01)
    python scripts/test_models.py --audio note.webm  # + Muse Voice Transcribe
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

import config  # noqa: E402

PROMPT = [
    {"role": "system", "content": "You are Magpie, a warm storyteller. Use only the facts given."},
    {"role": "user", "content": "Fact: Rose spent summers at a lake house in 1972. "
                                "Ask her one gentle, open question about it."},
]


def timed(label: str, fn):
    t = time.time()
    try:
        out = fn()
        print(f"  OK   {label}: {time.time() - t:.1f}s")
        if out:
            print(f"       -> {str(out)[:200]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {label}: {time.time() - t:.1f}s  {e}")
        return False


def test_image_bytes() -> bytes:
    img = Image.new("RGB", (512, 320), (240, 232, 214))
    d = ImageDraw.Draw(img)
    d.text((40, 120), "Blueberry Buckle - for Anne, 1983", fill=(30, 40, 120))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vision", action="store_true")
    ap.add_argument("--image", action="store_true")
    ap.add_argument("--audio")
    ap.add_argument("--skip-glimmer", action="store_true")
    a = ap.parse_args()
    ok = True

    print("Muse Spark (Meta Model API)")
    ok &= timed("chat", lambda: config.chat(PROMPT, provider="meta"))

    if a.vision:
        from magpie.extract import image_to_data_url
        url = image_to_data_url(test_image_bytes())
        ok &= timed("vision", lambda: config.chat([{"role": "user", "content": [
            {"type": "text", "text": "Transcribe any handwriting or text in this image."},
            {"type": "image_url", "image_url": {"url": url}}]}], provider="meta"))

    if a.image:
        from magpie.quilt import muse_image, save_image
        from magpie.storage import DATA
        out = DATA / "reports"
        ok &= timed("Muse Image ($0.01)", lambda: save_image(
            muse_image("Square embroidered quilt patch, folk-art style, a small teacup, no text"), out, "test_image"))

    if a.audio:
        from magpie.transcribe import transcribe
        p = Path(a.audio)
        ok &= timed("Muse Voice Transcribe", lambda: transcribe(p.read_bytes(), p.name))

    if os.getenv("GLIMMER_URL") and not a.skip_glimmer:
        print("Muse Glimmer (Modal)")
        ok &= timed("cold call (may take 1-2 min)", lambda: config.chat(PROMPT, provider="glimmer"))
        ok &= timed("warm call", lambda: config.chat(PROMPT, provider="glimmer"))
    else:
        print("Muse Glimmer: skipped (set GLIMMER_URL in .env)")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
