# Magpie

*Keeping their treasures safe.*

**Her family tells her story, Magpie keeps it safe, and she gets to hear it again.**

Magpie turns family keepsakes, photos, handwritten cards and voice notes into a verified memory graph called **Engram**. A gentle session agent then shares her life story with her: it **asks** first, offers a small **hint** if she's close, and **tells** her the story as a gift if she can't recall it, sometimes followed by a grandchild's real voice. Every verified story is stitched into a growing **Memory Quilt**.

> Magpie is not a medical tool and makes no claims about memory or health. It keeps and shares a family's stories.
> **Rose Ellis is a fictional demo persona.** Her pictures are AI-generated illustrations (Muse Image) or staged objects, labeled as such in the app.
> Demo only: Rose’s family is fictional, so their voice notes use AI stock voices. In real use, family voice notes are real recordings.

Track: **Build for Your World** · Meta Global AI Developer Hackathon

---

## Built during the hackathon (Oct 27 – Nov 4, 2026)

Everything in this repository was written during the hackathon:

| Piece | Where | Meta model |
|---|---|---|
| Engram: keepsake → suggested facts → Confirm / Edit / Reject, a source on every fact | `magpie/engram.py`, `magpie/extract.py` | Muse Spark (vision) |
| "Add a story about her": typed or spoken, structured into facts for the family to confirm | `magpie/extract.py`, `magpie/transcribe.py` | Muse Spark, Muse Voice Transcribe |
| Session agent: plan → Ask → Hint (max 2) → Tell → family voice clip → gentle close; "Talk about something else"; fragments saved for review | `magpie/session.py` | **Muse Glimmer (open weights, on Modal)** |
| Live grounding guard: every line is checked against confirmed facts *before* it is spoken; unsupported drafts are rewritten or replaced with the family's own words | `magpie/grounding.py` | **Muse Glimmer** |
| Simulated visits + grounding report ("0 invented facts across 20 sessions") | `scripts/simulate_sessions.py` | Muse Glimmer + Muse Spark (plays Rose) |
| "Talk with Grandma" cards, built only from confirmed facts | `magpie/cards.py` | Muse Glimmer |
| Memory Quilt: one embroidered square per confirmed story, rows by decade, export as one image | `magpie/quilt.py` | Muse Image |
| Hold up a real keepsake → webcam → matched to a known item | `magpie/objects.py` | Muse Glimmer (image input) |
| Muse Glimmer deployment on Modal (Ollama, one A10G, scale-to-zero) | `glimmer_modal.py` | Muse Glimmer |
| Web app: Visit / Family / Quilt | `app.py`, `web/` | |

## How it stays honest

1. **Only confirmed facts are used.** Models only *suggest*. A relative confirms, edits or rejects every fact.
2. **Every fact shows its source:** `from photo, confirmed by Priya`, `from Priya's voice note, Nov 1`, `Rose told this on Nov 2`.
3. **Every spoken line is traced.** `grounding.check()` lists each factual claim and the Engram fact it comes from. A line with an untraced claim is rewritten once; if it still fails, Magpie uses the family's own words instead. The Visit screen shows this live ("✓ 3 facts traced · 1 draft corrected").
4. **She is never quizzed or corrected.** If she remembers it differently, Magpie accepts it warmly and saves her version for the family.
5. **Family voices are real recordings**, never cloned. 
*(Demo only: Rose's family is fictional, so their voice notes use AI stock voices.)*

## Architecture

```
Family view ──► Muse Spark (vision/JSON) ──► suggested facts ──► Confirm / Edit / Reject ──► Engram (SQLite)
voice note ──► Muse Voice Transcribe ──┘                                                        │ confirmed only
                                                                                                ▼
Visit ◄── speechSynthesis ◄── grounding guard ◄── Session agent (Muse Glimmer on Modal) ◄── item context
  │                             (Muse Glimmer)        plan · ask · hint · tell · reflect · switch · close
  └── her reply (typed, or Muse Voice Transcribe) ──► classify ──► fragments + her details ──► review queue
Confirmed story ──► Muse Image ──► quilt square ──► Memory Quilt (quilt.png)
```

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env                  # add MODEL_API_KEY (and Glimmer settings below)

python -m magpie.engram --reset       # load the demo persona (the app also does this on first run)
python scripts/generate_images.py     # keepsakes + quilt squares with Muse Image (~$0.20)
uvicorn app:app --reload              # http://localhost:8000
```

Record the two family voice notes (scripts are in `data/rose.json`) as `data/voice/vn_priya.webm` and `data/voice/vn_sam.webm`, or record them in the Family view.

### Muse Glimmer on Modal (open weights)

```bash
pip install modal && modal setup
modal run glimmer_modal.py::pull      # once: ~18 GB of weights into a Modal Volume
modal deploy glimmer_modal.py         # prints the URL
```

In `.env`: `GLIMMER_URL=<url>/v1`, `MODAL_PROXY_KEY`, `MODAL_PROXY_SECRET` (Modal → Settings → Proxy Auth Tokens), then `MAGPIE_PROVIDER=glimmer`. Self-hosting means a family or care home could run the session agent on their own hardware.

### Check the models and grounding

```bash
python scripts/test_models.py --vision --image          # Muse Spark, Muse Image, Glimmer timings
MAGPIE_PROVIDER=glimmer python scripts/simulate_sessions.py --n 20
# -> data/reports/grounding_report.md
```

## Project layout

```
config.py            all model providers (one env var switches Meta <-> Glimmer)
glimmer_modal.py     Muse Glimmer on Modal
app.py               FastAPI API + web UI
magpie/              prompts, storage, engram, extract, transcribe, session, grounding, cards, quilt, objects
scripts/             test_models, generate_images, simulate_sessions
data/                rose.json (demo persona), image_prompts.json, images/, quilt/, voice/, reports/
web/                 index.html, app.js, style.css
```

## Notes

- Images are resized to 1024px before any model call; each session turn sends only the current keepsake and its confirmed facts.
- `data/magpie.db` and `.env` are gitignored. Generated demo images are committed so the demo runs from a fresh clone.
- Muse Glimmer is Apache 2.0. Muse Spark, Muse Image and Muse Voice Transcribe are used through the Meta Model API.
