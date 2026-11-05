"""
Session agent: the agentic core of Magpie.

  plan    -> picks a theme and 3 keepsakes (prefers comfort topics, less-shared stories)
  ask     -> open, warm question about the keepsake on screen
  hint    -> at most two gentle clues from the family's hints
  tell    -> tells the confirmed story as a gift, then plays a family voice note if one is linked
  reflect -> when she tells it herself, reflects her words back and saves them for family review
  switch  -> "Talk about something else" (always available, prefers comfort topics)
  close   -> ends gently when the keepsakes are done, or if she seems tired or upset

Every Magpie line is checked by grounding.check() before it is spoken. A draft with an
unsupported claim is rewritten once; if it still fails, Magpie falls back to the family's own words.
"""
from __future__ import annotations

import os
import random
import re
import uuid

import config

from . import engram, grounding, prompts, storage

MAX_HINTS = 2
TELL_TOKENS = 1536

CLOSE_LINES = [
    "Thank you for sharing these with me today. I loved every one of them.",
    "That was a lovely visit. Thank you for spending this time with me.",
    "Thank you, that was wonderful. Let's rest here for today.",
]
GENTLE_CLOSE_LINES = [
    "Let's rest for now. Thank you for this lovely time together.",
    "That's plenty for today. Thank you for sitting with me.",
]
DEFAULT_SWITCH_LINES = ["Let's look at something else lovely."]
JUMP_LINE = "Oh, what have you got there? Let's look at that together."


def live_grounding() -> bool:
    return os.getenv("MAGPIE_LIVE_GROUNDING", "1") == "1"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _turn(session_id: str, role: str, text: str, mode: str, item_id: str | None = None,
          audio_url: str | None = None, ground: dict | None = None) -> dict:
    tid = storage.execute(
        """INSERT INTO turns(session_id, role, text, mode, item_id, audio_url, grounding, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (session_id, role, text, mode, item_id, audio_url, storage.dumps(ground) if ground else None, engram.now()))
    return {"id": tid, "role": role, "text": text, "mode": mode, "item_id": item_id,
            "audio_url": audio_url, "grounding": ground}


def _load(session_id: str) -> tuple[dict, dict]:
    s = storage.one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not s:
        raise KeyError(session_id)
    return s, s["state"]


def _save(session_id: str, state: dict) -> None:
    storage.execute("UPDATE sessions SET state=?, items=? WHERE id=?",
                    (storage.dumps(state), storage.dumps(state["items"]), session_id))


def _current(state: dict) -> str | None:
    return state["items"][state["idx"]] if state["idx"] < len(state["items"]) else None


def _outcome(state: dict, item_id: str, title: str) -> dict:
    return state["outcomes"].setdefault(item_id, {"title": title, "path": [], "hints": 0,
                                                   "result": "incomplete", "voice": None})


# ---------------------------------------------------------------------------
# Speaking: generate -> ground -> rewrite or fall back
# ---------------------------------------------------------------------------

def _system() -> str:
    return prompts.session_system(engram.persona())


def _fallback(mode: str, ctx: dict, hint: str | None, transition: str | None) -> str:
    title = ctx["item"]["title"]
    title = title[0].lower() + title[1:]
    lead = f"{transition} " if transition else ""
    if mode == "ask":
        return f"{lead}Here is {title}. Would you tell me about it?" if not title.startswith(("a ", "an ", "the ")) \
            else f"{lead}Look, here's {title}. Would you tell me about it?"
    if mode == "hint":
        return f"{hint} There's no hurry at all." if hint else "There's no hurry at all. I'm happy to tell you about it."
    if mode == "tell" and ctx.get("story"):
        return ctx["story"]["text"]
    return "Thank you for telling me that. I love hearing it."


def _clean(text: str) -> str:
    text = text.strip().strip('"').strip()
    return re.sub(r"^(Magpie|MAGPIE)\s*:\s*", "", text)


def say(mode: str, ctx: dict, *, her_words: str | None = None, hint: str | None = None,
        greeting: str | None = None, transition: str | None = None) -> tuple[str, dict | None]:
    """Generate one grounded Magpie line. Returns (text, grounding record)."""
    sources = grounding.build_sources(ctx["facts"], item=ctx["item"], her_words=her_words,
                                      extra=[hint] if hint else None)
    feedback, caught = None, 0
    for _ in range(2):
        try:
            draft = _clean(config.chat(
                [{"role": "system", "content": _system()},
                 {"role": "user", "content": prompts.session_turn(
                     mode, ctx["item"]["title"], sources, her_words=her_words, hint=hint,
                     greeting=greeting, transition=transition, feedback=feedback)}],
                max_tokens=TELL_TOKENS if mode == "tell" else config.DEFAULT_MAX_TOKENS))
        except Exception as e:  # noqa: BLE001 - keep the visit going on a model error
            return _fallback(mode, ctx, hint, transition), {
                "claims": [], "checked": 0, "invented": 0, "caught": caught, "fallback": True,
                "error": str(e)[:200]}
        if not live_grounding():
            return draft, None
        g = grounding.check(draft, sources)
        if g["invented"] in (0, None):
            return draft, {**g, "caught": caught}
        caught += 1
        feedback = g["unsupported"]
    # Two drafts failed: use the family's own words, which are grounded by construction
    return _fallback(mode, ctx, hint, transition), {
        "claims": [], "checked": 0, "invented": 0, "caught": caught, "fallback": True}


# ---------------------------------------------------------------------------
# Understanding her reply
# ---------------------------------------------------------------------------

UNSURE = re.compile(r"\b(don'?t know|not sure|can'?t (think|place|recall)|no idea|what is (it|this)|hmm+)\b", re.I)
TIRED = re.compile(r"\b(tired|rest|stop|enough|sleepy|leave me|upset|sad)\b", re.I)


def _heuristic(her_words: str) -> dict:
    words = len(her_words.split())
    mood = "tired" if TIRED.search(her_words) else "ok"
    if UNSURE.search(her_words) and words < 12:
        recall = "unsure"
    elif "..." in her_words or "…" in her_words or words < 5:
        recall = "partial"
    else:
        recall = "full"
    return {"recall": recall, "mood": mood, "fragment": her_words if recall == "partial" else None, "details": []}


def classify(ctx: dict, her_words: str, hints_used: int) -> dict:
    """Describe her reply: recall level, mood, any fragment, and details she shared."""
    story_text = (ctx.get("story") or {}).get("text", "")
    name = engram.persona().get("preferred_name", "She")
    try:
        d = config.chat_json(
            [{"role": "system", "content": prompts.CLASSIFY_SYSTEM},
             {"role": "user", "content": prompts.classify_user(
                 ctx["item"]["title"], story_text, her_words, hints_used, name)}],
            max_tokens=1024)
    except Exception:  # noqa: BLE001
        return _heuristic(her_words)
    recall = d.get("recall") if d.get("recall") in ("full", "partial", "unsure", "none") else "unsure"
    mood = d.get("mood") if d.get("mood") in ("ok", "tired", "upset") else "ok"
    frag = d.get("fragment")
    frag = frag.strip() if isinstance(frag, str) and frag.strip() and frag.strip().lower() != "null" else None
    details = [x.strip() for x in (d.get("details") or []) if isinstance(x, str) and x.strip()]
    return {"recall": recall, "mood": mood, "fragment": frag, "details": details[:4]}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _plan(candidates: list[dict], n: int = 3) -> tuple[str, list[str], str]:
    """Agent picks a theme and keepsakes. Falls back to a simple rule if the model fails."""
    p = engram.persona()
    ids = {c["id"] for c in candidates}
    try:
        d = config.chat_json(
            [{"role": "system", "content": prompts.PLAN_SYSTEM},
             {"role": "user", "content": prompts.plan_user(candidates, p.get("comfort_topics", []), n)}],
            max_tokens=1024)
        chosen, stories = [], set()
        by_id = {c["id"]: c for c in candidates}
        for i in d.get("item_ids", []):
            if i in ids and by_id[i]["story_id"] not in stories:
                chosen.append(i); stories.add(by_id[i]["story_id"])
        if chosen:
            return d.get("theme") or "Stories from her life", chosen[:n], d.get("reason") or ""
    except Exception:  # noqa: BLE001
        pass
    pool = sorted(candidates, key=lambda c: (c["times_discussed"], random.random()))
    chosen, stories = [], set()
    for c in pool:
        if c["story_id"] not in stories:
            chosen.append(c["id"]); stories.add(c["story_id"])
        if len(chosen) == n:
            break
    return "Stories from her life", chosen, "Chose the stories shared least often."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_session(mode: str = "agent", theme: str | None = None, item_ids: list[str] | None = None,
                  kind: str = "live") -> dict:
    """
    Start a visit.
      mode='agent' -> the agent plans theme + keepsakes
      mode='demo'  -> the scripted demo order from rose.json (Summers and the lake)
      item_ids     -> explicit keepsakes (used by simulations)
    """
    candidates = engram.session_candidates()
    if not candidates:
        raise ValueError("No keepsakes with confirmed stories yet. Confirm a story in the Family view first.")
    cand_ids = [c["id"] for c in candidates]
    reason = ""
    if item_ids:
        chosen = [i for i in item_ids if i in cand_ids]
        theme = theme or "Stories from her life"
    elif mode == "demo":
        demo = engram.persona().get("demo_session", {})
        chosen = [i for i in demo.get("items_in_order", []) if i in cand_ids]
        theme = demo.get("theme") or "Stories from her life"
        reason = "Scripted demo visit."
    else:
        theme, chosen, reason = _plan(candidates)
    if not chosen:
        chosen = cand_ids[:3]

    sid = uuid.uuid4().hex[:10]
    state = {"items": chosen, "idx": 0, "hints_used": 0, "outcomes": {}, "ended": False,
             "theme": theme, "plan_reason": reason, "simulated": kind == "sim"}
    storage.execute("INSERT INTO sessions(id, kind, theme, items, state, started_at) VALUES (?,?,?,?,?,?)",
                    (sid, kind, theme, storage.dumps(chosen), storage.dumps(state), engram.now()))

    ctx = engram.item_context(chosen[0])
    _outcome(state, chosen[0], ctx["item"]["title"])["path"].append("ask")
    text, g = say("ask", ctx, greeting=f"Today's theme is '{theme}'.")
    _turn(sid, "magpie", text, "ask", chosen[0], ground=g)
    _save(sid, state)
    return get_session(sid)


def respond(session_id: str, her_words: str) -> list[dict]:
    """Handle one reply from her. Returns the new Magpie turns (in order)."""
    sess, state = _load(session_id)
    her_words = (her_words or "").strip()
    if state["ended"] or not her_words:
        return []
    item_id = _current(state)
    ctx = engram.item_context(item_id)
    story = ctx.get("story")
    out = _outcome(state, item_id, ctx["item"]["title"])
    _turn(session_id, "rose", her_words, "reply", item_id)

    cls = classify(ctx, her_words, state["hints_used"])
    if not state.get("simulated"):
        if cls["fragment"] and cls["recall"] != "full":
            engram.add_fragment(session_id, item_id, story["id"] if story else None, cls["fragment"])
        if cls["details"] and cls["recall"] in ("full", "partial"):
            for d in cls["details"]:
                engram.add_fact(d, source_type="session",
                                source_label=f"{engram.persona().get('preferred_name', 'She')} told this on "
                                             f"{engram.date_label()}",
                                item_id=item_id, story_id=story["id"] if story else None, contributor="Rose")
    out.setdefault("replies", []).append({"recall": cls["recall"], "mood": cls["mood"]})

    new: list[dict] = []
    if cls["mood"] in ("tired", "upset"):
        out["result"] = "rested"
        new += _close(session_id, state, gentle=True)

    elif cls["recall"] == "full":
        out["path"].append("reflect")
        out["result"] = "herself" if state["hints_used"] == 0 else "after_hints"
        text, g = say("reflect", ctx, her_words=her_words)
        new.append(_turn(session_id, "magpie", text, "reflect", item_id, ground=g))
        new += _advance(session_id, state)

    else:
        hints = ctx["hints"]
        limit = min(MAX_HINTS, max(1, len(hints)))
        can_hint = state["hints_used"] < limit and not (cls["recall"] == "none" and state["hints_used"] >= 1)
        if can_hint:
            hint = hints[state["hints_used"]] if state["hints_used"] < len(hints) else None
            state["hints_used"] += 1
            out["hints"] = state["hints_used"]
            out["path"].append("hint")
            text, g = say("hint", ctx, her_words=her_words, hint=hint)
            new.append(_turn(session_id, "magpie", text, "hint", item_id, ground=g))
        else:
            new += _tell(session_id, state, ctx, out, her_words)
            new += _advance(session_id, state)

    _save(session_id, state)
    return new


def _tell(session_id: str, state: dict, ctx: dict, out: dict, her_words: str | None) -> list[dict]:
    item_id = ctx["item"]["id"]
    out["path"].append("tell")
    out["result"] = "told"
    text, g = say("tell", ctx, her_words=her_words)
    turns = [_turn(session_id, "magpie", text, "tell", item_id, ground=g)]
    for clip in ctx["voice_clips"][:1]:
        intro = clip.get("tell_intro") or f"Here's {clip['speaker_name']}."
        turns.append(_turn(session_id, "magpie", intro, "voice", item_id, audio_url=clip["audio_url"]))
        out["voice"] = clip["speaker_name"]
    return turns


def _advance(session_id: str, state: dict, transition: str | None = None) -> list[dict]:
    state["idx"] += 1
    state["hints_used"] = 0
    nxt = _current(state)
    if nxt is None:
        return _close(session_id, state)
    ctx = engram.item_context(nxt)
    _outcome(state, nxt, ctx["item"]["title"])["path"].append("ask")
    text, g = say("ask", ctx, transition=transition)
    return [_turn(session_id, "magpie", text, "ask" if not transition else "switch", nxt, ground=g)]


def _close(session_id: str, state: dict, gentle: bool = False) -> list[dict]:
    text = random.choice(GENTLE_CLOSE_LINES if gentle else CLOSE_LINES)
    turn = _turn(session_id, "magpie", text, "close", _current(state))
    state["ended"] = True
    summary = build_summary(session_id, state)
    storage.execute("UPDATE sessions SET summary=?, ended_at=? WHERE id=?", (summary, engram.now(), session_id))
    return [turn]


def switch(session_id: str) -> list[dict]:
    """'Talk about something else': move on gently, preferring her comfort stories."""
    sess, state = _load(session_id)
    if state["ended"]:
        return []
    cur = _current(state)
    if cur:
        o = state["outcomes"].get(cur)
        if o and o["result"] == "incomplete":
            o["result"] = "switched"
            o["path"].append("switch")
    p = engram.persona().get("switch_topic", {})
    remaining = state["items"][state["idx"] + 1:]
    if not remaining:
        used = set(state["items"])
        cands = {c["story_id"]: c["id"] for c in engram.session_candidates() if c["id"] not in used}
        prefer = [cands[s] for s in p.get("prefer", []) if s in cands] or list(cands.values())
        if prefer:
            state["items"].insert(state["idx"] + 1, prefer[0])
    line = random.choice(p.get("lines") or DEFAULT_SWITCH_LINES)
    turns = _advance(session_id, state, transition=line)
    _save(session_id, state)
    return turns


def jump(session_id: str, item_id: str) -> list[dict]:
    """She held up a real keepsake: move to it next."""
    sess, state = _load(session_id)
    if state["ended"] or item_id == _current(state):
        return []
    if item_id not in {c["id"] for c in engram.session_candidates()}:
        raise ValueError("That keepsake doesn't have a confirmed story yet.")
    cur = _current(state)
    if cur and state["outcomes"].get(cur, {}).get("result") == "incomplete":
        state["outcomes"][cur]["result"] = "switched"
    state["items"].insert(state["idx"] + 1, item_id)
    turns = _advance(session_id, state, transition=JUMP_LINE)
    _save(session_id, state)
    return turns


def end_session(session_id: str) -> list[dict]:
    sess, state = _load(session_id)
    if state["ended"]:
        return []
    turns = _close(session_id, state)
    _save(session_id, state)
    return turns


# ---------------------------------------------------------------------------
# Summaries and views
# ---------------------------------------------------------------------------

RESULTS = {
    "herself": "told it herself",
    "after_hints": "got there with {h} hint{s}",
    "told": "heard the story from Magpie{after}",
    "switched": "moved on to something else",
    "rested": "stopped here to rest",
    "incomplete": "looked at it together",
}


def build_summary(session_id: str, state: dict) -> str:
    """Plain, factual summary for the family."""
    seen = [i for i in state["items"] if i in state["outcomes"]]
    parts = [f"Looked at {len(seen)} keepsake{'s' if len(seen) != 1 else ''} ({state.get('theme')})."]
    for i in seen:
        o = state["outcomes"][i]
        h = o.get("hints", 0)
        after = f" after {h} hint{'s' if h != 1 else ''}" if h else ""
        phrase = RESULTS.get(o["result"], o["result"]).format(h=h, s="s" if h != 1 else "", after=after)
        if o.get("voice"):
            phrase += f", and heard {o['voice']}'s voice note"
        parts.append(f"{o['title']}: {phrase}.")
    n_frag = storage.one("SELECT COUNT(*) AS n FROM fragments WHERE session_id=?", (session_id,))["n"]
    if n_frag:
        parts.append(f"{n_frag} fragment{'s' if n_frag != 1 else ''} saved for review.")
    st = grounding.stats(kind="sim" if state.get("simulated") else "live", session_ids=[session_id])
    if st["lines_checked"]:
        parts.append(f"Grounding: {st['claims_traced']} claims traced, {st['invented_spoken']} invented.")
    return " ".join(parts)


def get_session(session_id: str) -> dict:
    s, state = _load(session_id)
    turns = storage.query("SELECT * FROM turns WHERE session_id=? ORDER BY id", (session_id,))
    cur = _current(state)
    return {
        "id": s["id"], "kind": s["kind"], "theme": s["theme"], "started_at": s["started_at"],
        "ended_at": s["ended_at"], "ended": state["ended"], "summary": s["summary"],
        "plan_reason": state.get("plan_reason"), "items": state["items"], "idx": state["idx"],
        "current_item": engram.get_item(cur) if cur else None, "turns": turns,
        "outcomes": state["outcomes"],
    }


def list_sessions(kind: str = "live") -> list[dict]:
    return storage.query(
        "SELECT id, theme, summary, started_at, ended_at FROM sessions WHERE kind=? ORDER BY started_at DESC", (kind,))
