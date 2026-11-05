# Magpie

*Keeping their treasures safe.*

Magpie is a hackathon project (Meta Global AI Developer Hackathon, track: **Build for Your World**). A family turns keepsakes, photos, documents and voice notes into a verified memory graph called **Engram**. A gentle session agent then shares her life story with an elderly relative: it asks first, hints if she is close, and tells the story if she can't recall it. Every verified story becomes a square in a growing **Memory Quilt**.

**Deadline: Wed Nov 4, 2026, 9:00 PM ET.** Prefer working, simple code over clever code. Ship the core first.

## What Magpie is NOT

- Not therapy, not a medical tool, not a memory test. It makes **no claims** about memory, dementia or health.
- Copy and UI say "keep and share her story". Never "help her remember", "improve memory", "protect memory" or similar.
- The demo persona **Rose Ellis** is fictional. All her images are AI-generated or staged objects, and are labeled as such in the UI.

## Non-negotiable rules (product)

1. **Only confirmed facts are used.** The model *suggests* facts; family clicks Confirm / Edit / Reject. The session agent may only say things traceable to a confirmed Engram fact.
2. **Every fact has a source**, shown in the UI: e.g. `from photo, confirmed by Priya` or `from Priya's voice note, Nov 1` or `Rose told this on Nov 2`.
3. **Session agent never** quizzes, tests or corrects her; never mentions illness, memory, forgetting or death. If she says something different from Engram, accept it warmly and save it for family review.
4. **Ask → Hint → Tell.** Open warm question first, at most two gentle hints, then tell the story as a gift. End gently if she seems tired or upset.
5. **Fragments are saved** (e.g. "blue… Frank's car") to the family review queue, never discarded.
6. **Quilt squares show places and objects only. No faces, no people.**
7. **Family voices in Tell mode.** When Magpie tells a story and a family voice note is linked to it, play that clip after the telling ("Here's Priya talking about Bluebell"). Family voices are real recordings by real people, never synthesized or cloned.
8. **"Talk about something else" is always available.** One tap moves to another item, preferring her comfort topics. The transition is gentle and never implies she failed ("Let's look at something else lovely").
9. **"Talk with Grandma" cards** are generated only from confirmed stories, so relatives get grounded conversation starters, not invented ones.

## Models and providers

All model calls go through `config.py`. Do not create OpenAI clients anywhere else.

| Job | Model | Provider | ID |
|---|---|---|---|
| Photo/document reading, fact extraction, story structuring | Muse Spark | Meta Model API | `muse-spark-1.3` |
| Voice notes → text | Muse Voice Transcribe | Meta Model API | `muse-voice-transcribe-1.0` at `POST /v1/asr/transcribe` (WAV only; `transcribe.py` converts webm with bundled ffmpeg) |
| Item images + quilt squares | Muse Image | Meta Model API | `muse-image-1.0` ($0.01/image) |
| **Session agent + grounding check** | **Muse Glimmer** (open weights, 30B) | Modal, Ollama, 1x A10G | `muse-glimmer:30b-q4_K_M` |
| *Stretch:* match a held-up real object to a known item | Muse Glimmer (image input) | Modal | same as above |
| *Optional:* clean object cutouts for cards and quilt | SAM 3.1 | Meta Model API | confirm ID in Meta model list |

- Meta base URL: `https://api.meta.ai/v1` (OpenAI-compatible; Chat Completions and Responses both supported). Key env var: `MODEL_API_KEY`.
- Switch the session agent with `MAGPIE_PROVIDER=meta|glimmer`. **The final demo runs the session agent on Glimmer** (needed for the Open Weight bonus). Develop on `meta`, switch to `glimmer` by Tuesday morning.
- Glimmer has **no audio input** — always transcribe with Muse Voice Transcribe first.
- These are **reasoning models**: always use `config.chat()` (default `max_tokens=1024`, `reasoning_effort=low`). Small `max_tokens` returns empty text.
- Ask the model for **JSON** when extracting facts; validate and retry once on bad JSON.

### Keeping costs low

- Resize images to max 1024px before sending.
- Keep the system prompt identical across calls (prompt caching).
- Each session turn sends only the current item + its confirmed facts, not the whole graph.
- Glimmer on Modal scales to zero after 3 min; first call after idle takes 1–2 min (cold start). Don't loop calls in tests without need.

## Project structure

```
config.py               provider config + chat() helper
glimmer_modal.py        Muse Glimmer on Modal (pull once, then deploy)
app.py                  FastAPI server: API routes + serves web/
magpie/
  prompts.py            ALL system prompts live here
  storage.py            SQLite (data/magpie.db)
  engram.py             graph: people, places, items, stories, facts + sources + status
  extract.py            item/voice text -> suggested facts (Muse Spark, JSON)
  transcribe.py         voice -> text
  session.py            session agent: Ask -> Hint -> Tell (+ family voice clip), fragments,
                        "talk about something else", gentle ending
  cards.py              "Talk with Grandma" cards from confirmed stories
  objects.py            (stretch) webcam snapshot -> match to a known item
  grounding.py          trace every factual claim in agent output to a confirmed fact
  quilt.py              story -> square (Muse Image), stitch quilt.png
scripts/
  generate_images.py    generate items + quilt squares from data/image_prompts.json
  test_models.py        smoke test Meta + Glimmer, print timings
  simulate_sessions.py  simulated Rose (data/rose.json simulated_recall) x 20 -> grounding report
data/
  rose.json             demo persona fact sheet + items + voice scripts + demo session
  image_prompts.json    Muse Image prompts
  images/ quilt/ voice/ generated/recorded assets (committed)
web/                    index.html, app.js, style.css — tabs: Session / Family / Quilt
```

## Data model (Engram)

- **Fact**: `id, subject, predicate, object, item_id?, story_id?, source_type (photo|document|voice|session|family_text), source_label, contributor, created_at, status (suggested|confirmed|rejected)`.
- **Story**: `id, title, decade, year, text, people[], place, item_id, status`.
- **Fragment**: `id, session_id, item_id, text, created_at, reviewed`.
- **Session**: `id, theme, items[], turns[] (role, text, mode: ask|hint|tell|switch), summary, started_at, ended_at`.
- **VoiceNote**: `id, speaker, audio_path, transcript, story_ids[], date, play_in_tell (bool, family opts in)`.
- **TalkCard**: `id, story_id, opener, details[] (confirmed facts only), tip, generated_at`.
- Only `status == confirmed` facts/stories are ever passed to the session agent.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env                          # fill in keys

modal run glimmer_modal.py::pull              # once: weights -> Modal volume
modal deploy glimmer_modal.py                 # prints URL; set GLIMMER_URL=<url>/v1

python scripts/test_models.py                 # smoke test both providers
python scripts/generate_images.py             # make item + quilt images
python scripts/simulate_sessions.py --n 20    # grounding report
uvicorn app:app --reload                      # run the app at http://localhost:8000
```

## Code conventions

- Python 3.11, FastAPI, SQLite (stdlib `sqlite3`), plain HTML/CSS/JS frontend (no build step).
- Browser `speechSynthesis` for Magpie's voice; `MediaRecorder` to capture voice notes.
- Small functions, type hints, docstrings on public functions. No heavy frameworks (no LangChain).
- Never hardcode keys or URLs; read from env via `config.py`.
- `.env` and `data/magpie.db` are gitignored. **The repo is public — never commit secrets.**
- UI copy: warm, plain, short. Label AI images "AI-generated illustration" and Rose as "Demo persona".

## Priorities (cut from the bottom if behind)

1. Engram: item → suggested facts → Confirm/Edit/Reject → sources shown
2. Session agent: Ask → Tell (text), fragments saved, "Talk about something else" button
3. Hint ladder
4. Family voice clip played in Tell mode
5. Family view: archive, review queue, "Add a story about her", summaries, "Talk with Grandma" cards
6. Grounding check + simulated sessions ("0 invented facts across 20 sessions")
7. Voice in/out
8. Memory Quilt
9. *Stretch (Tue):* hold up the real object → webcam → Glimmer matches it to a known item
10. *Optional:* SAM 3.1 cutouts

## Hackathon requirements to keep in mind

- Repo must be **public**; README must have a clear **"Built during the hackathon (Oct 27 – Nov 4)"** section.
- 3-minute demo video shows only features built during the hackathon.
- Must visibly use Meta models; session agent on Muse Glimmer for the Open Weight bonus; quilt on Muse Image for the Creative bonus.
