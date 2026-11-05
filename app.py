"""
Magpie web server: API routes + the web UI.

    uvicorn app:app --reload        -> http://localhost:8000
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from magpie import cards, engram, extract, grounding, objects, quilt, session, storage, transcribe

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Magpie", description="Keeping their treasures safe.")

storage.init_db()
if storage.is_empty():
    engram.seed("full")


@app.exception_handler(KeyError)
async def _not_found(_, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": f"Not found: {exc}"})


@app.exception_handler(ValueError)
async def _bad(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _model_error(e: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Model call failed: {str(e)[:300]}")


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------

class By(BaseModel):
    by: str = "Priya"


class EditFact(BaseModel):
    text: str
    by: str = "Priya"


class Note(BaseModel):
    note: str | None = None


class NewStory(BaseModel):
    text: str
    contributor: str = "Priya"
    item_id: str | None = None


class FragmentAction(BaseModel):
    action: str = "keep"
    by: str = "Priya"


class StartSession(BaseModel):
    mode: str = "agent"
    theme: str | None = None


class Reply(BaseModel):
    text: str


class Jump(BaseModel):
    item_id: str


class Snapshot(BaseModel):
    image: str   # data URL


class Reset(BaseModel):
    mode: str = "full"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@app.get("/api/state")
def state():
    p = engram.persona()
    return {
        "persona": {k: p.get(k) for k in ("name", "preferred_name", "born", "birthplace", "label", "comfort_topics")},
        "people": engram.people(),
        "provider": config.active_provider(),
        "models": {"session": config.PROVIDERS[config.active_provider()]["model"],
                   "extract": config.PROVIDERS["meta"]["model"], "image": config.MUSE_IMAGE_MODEL,
                   "voice": config.MUSE_TRANSCRIBE_MODEL},
        "live_grounding": session.live_grounding(),
    }


@app.get("/api/graph")
def graph():
    return engram.graph()


# ---------------------------------------------------------------------------
# Engram: keepsakes and facts
# ---------------------------------------------------------------------------

@app.get("/api/items")
def items():
    return engram.list_items()


@app.get("/api/items/{item_id}")
def item(item_id: str):
    it = engram.get_item(item_id)
    if not it:
        raise KeyError(item_id)
    return {"item": it, "facts": [f for f in engram.list_facts(item_id=item_id) if f["status"] != "rejected"],
            "story": engram.get_story(it["story_id"]) if it.get("story_id") else None,
            "story_facts": engram.list_facts("confirmed", story_id=it["story_id"]) if it.get("story_id") else []}


@app.post("/api/items")
async def upload_item(file: UploadFile = File(...), title: str = Form("A keepsake"), type: str = Form("object"),
                      story_id: str = Form(""), contributor: str = Form("Priya")):
    ext = Path(file.filename or "x.jpg").suffix.lower()
    if ext not in storage.IMAGE_EXTS:
        raise HTTPException(400, "Please upload a JPG, PNG or WebP photo.")
    iid = engram.new_id("item")
    path = storage.IMAGES / f"{iid}{ext}"
    path.write_bytes(await file.read())
    return engram.add_item(item_id=iid, title=title.strip() or "A keepsake", type_=type, image_rel=storage.rel(path),
                           story_id=story_id or None, label=f"Photographed by {engram.contributor_name(contributor)}")


@app.post("/api/items/{item_id}/extract")
def extract_item(item_id: str, body: Note):
    try:
        return {"facts": extract.extract_from_item(item_id, body.note)}
    except (KeyError, FileNotFoundError):
        raise
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)


@app.post("/api/facts/{fact_id}/confirm")
def confirm_fact(fact_id: int, body: By):
    return engram.confirm_fact(fact_id, body.by)


@app.post("/api/facts/{fact_id}/edit")
def edit_fact(fact_id: int, body: EditFact):
    return engram.edit_fact(fact_id, body.text, body.by)


@app.post("/api/facts/{fact_id}/reject")
def reject_fact(fact_id: int, body: By):
    return engram.reject_fact(fact_id, body.by)


# ---------------------------------------------------------------------------
# Stories, voice notes, review queue, archive
# ---------------------------------------------------------------------------

@app.get("/api/stories")
def stories(status: str | None = None):
    return engram.list_stories(status)


@app.post("/api/stories")
def add_story(body: NewStory):
    if not body.text.strip():
        raise HTTPException(400, "Please write a few words about her.")
    who = engram.contributor_name(body.contributor)
    try:
        return extract.extract_from_text(
            body.text, contributor=body.contributor, source_type="family_text",
            source_label=f"from {who}'s story, {engram.date_label()}", item_id=body.item_id or None)
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)


@app.post("/api/stories/{story_id}/confirm")
def confirm_story(story_id: str, body: By):
    return engram.confirm_story(story_id, body.by)


@app.post("/api/stories/{story_id}/reject")
def reject_story(story_id: str, body: By):
    return engram.reject_story(story_id, body.by)


@app.get("/api/voice")
def voice_notes():
    return engram.list_voice_notes()


def _transcribe_note(vid: str, story_id: str | None) -> dict:
    v = engram.get_voice_note(vid)
    path = storage.abs_path(v["audio_path"])
    if not path or not path.exists():
        raise HTTPException(404, f"No recording yet at data/{storage.rel(path)}")
    try:
        text = transcribe.transcribe(path.read_bytes(), path.name)
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)
    engram.update_voice_note(vid, transcript=text)
    who = v["speaker_name"]
    label = f"from {who}'s voice note, {engram.date_label(v['date'] + 'T00:00:00' if len(v['date']) == 10 else v['date'])}"
    sid = story_id or ((v.get("story_ids") or [None])[0])
    try:
        res = extract.extract_from_text(text, contributor=who, source_type="voice", source_label=label, story_id=sid)
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)
    if res.get("story") and not v.get("story_ids"):
        engram.update_voice_note(vid, story_ids=[res["story"]["id"]])
    return {"voice": engram.get_voice_note(vid), "transcript": text, **res}


@app.post("/api/voice")
async def upload_voice(file: UploadFile = File(...), speaker: str = Form("priya"), story_id: str = Form(""),
                       play_in_tell: bool = Form(True)):
    ext = Path(file.filename or "note.webm").suffix.lower() or ".webm"
    if ext not in storage.AUDIO_EXTS:
        ext = ".webm"
    vid = engram.new_id("vn")
    path = storage.VOICE / f"{vid}{ext}"
    path.write_bytes(await file.read())
    who = engram.contributor_name(speaker)
    intro = None
    if story_id:
        s = engram.get_story(story_id)
        intro = f"Here's {who} talking about {s['title'].lower()}." if s else None
    engram.add_voice_note(vid=vid, speaker=speaker.lower(), audio_rel=storage.rel(path),
                          story_ids=[story_id] if story_id else [], play_in_tell=play_in_tell,
                          tell_intro=intro or f"Here's {who}.")
    return _transcribe_note(vid, story_id or None)


@app.post("/api/voice/{vid}/transcribe")
def transcribe_existing(vid: str):
    if not engram.get_voice_note(vid):
        raise KeyError(vid)
    return _transcribe_note(vid, None)


@app.post("/api/transcribe")
async def transcribe_reply(file: UploadFile = File(...)):
    """Her spoken reply during a visit -> text."""
    try:
        return {"text": transcribe.transcribe(await file.read(), file.filename or "reply.webm")}
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)


@app.get("/api/review")
def review():
    return engram.review_queue()


@app.post("/api/fragments/{fragment_id}")
def review_fragment(fragment_id: int, body: FragmentAction):
    return engram.review_fragment(fragment_id, body.action, body.by)


@app.get("/api/archive")
def archive():
    return engram.archive()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _with_session(sid: str, turns: list[dict]) -> dict:
    return {"turns": turns, "session": session.get_session(sid)}


@app.post("/api/session/start")
def start(body: StartSession):
    return session.start_session(mode=body.mode, theme=body.theme)


@app.get("/api/session/{sid}")
def get_session(sid: str):
    return session.get_session(sid)


@app.post("/api/session/{sid}/reply")
def reply(sid: str, body: Reply):
    return _with_session(sid, session.respond(sid, body.text))


@app.post("/api/session/{sid}/switch")
def switch(sid: str):
    return _with_session(sid, session.switch(sid))


@app.post("/api/session/{sid}/jump")
def jump(sid: str, body: Jump):
    return _with_session(sid, session.jump(sid, body.item_id))


@app.post("/api/session/{sid}/end")
def end(sid: str):
    return _with_session(sid, session.end_session(sid))


@app.get("/api/sessions")
def sessions():
    return session.list_sessions("live")


@app.get("/api/grounding")
def grounding_stats():
    return {"live": grounding.stats("live"), "report": grounding.load_report()}


# ---------------------------------------------------------------------------
# Talk with Grandma cards, quilt, keepsake matching
# ---------------------------------------------------------------------------

@app.get("/api/cards")
def get_cards():
    return cards.list_cards()


@app.post("/api/cards/generate")
def make_cards():
    return cards.generate_all()


@app.get("/api/quilt")
def get_quilt():
    return quilt.layout()


@app.post("/api/quilt/{story_id}/stitch")
def stitch(story_id: str):
    try:
        quilt.generate_square(story_id)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)
    return quilt.layout()


@app.post("/api/quilt/export")
def export_quilt():
    p = quilt.export()
    return {"url": storage.media_url(storage.rel(p)) + f"?v={uuid.uuid4().hex[:6]}"}


@app.post("/api/objects/match")
def match_object(body: Snapshot):
    try:
        return objects.match(body.image)
    except Exception as e:  # noqa: BLE001
        raise _model_error(e)


@app.post("/api/admin/reset")
def reset(body: Reset):
    if os.getenv("MAGPIE_ALLOW_RESET", "1") != "1":
        raise HTTPException(403, "Reset disabled")
    return engram.seed(body.mode, reset=True)


# ---------------------------------------------------------------------------
# Static files (only these data folders are public; magpie.db is never served)
# ---------------------------------------------------------------------------

app.mount("/media/images", StaticFiles(directory=storage.IMAGES), name="images")
app.mount("/media/quilt", StaticFiles(directory=storage.QUILT), name="quilt")
app.mount("/media/voice", StaticFiles(directory=storage.VOICE), name="voice")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
