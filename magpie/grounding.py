"""
Grounding check: trace every factual claim Magpie says back to a confirmed Engram fact.

Used live (every Magpie line is checked before it is spoken; unsupported drafts are rewritten
or replaced) and in scripts/simulate_sessions.py for the "0 invented facts" report.
Runs on the active provider, so in the final demo it runs on Muse Glimmer.
"""
from __future__ import annotations

import json

import config

from . import prompts, storage


def build_sources(facts: list[dict], item: dict | None = None, her_words: str | None = None,
                  extra: list[str] | None = None) -> list[dict]:
    """Numbered sources: F<id> confirmed facts, I1 the keepsake on screen, R1 her words, H<n> family hints."""
    src = []
    if item:
        desc = f" ({item['description']})" if item.get("description") else ""
        src.append({"id": "I1", "text": f"The keepsake on screen is {item['title'].lower()}{desc}."})
    src += [{"id": f"F{f['id']}", "text": f["text"]} for f in facts]
    if her_words:
        src.append({"id": "R1", "text": f'She just said: "{her_words}"'})
    for i, e in enumerate(extra or [], 1):
        if e:
            src.append({"id": f"H{i}", "text": f"Family hint: {e}"})
    return src


def check(utterance: str, sources: list[dict], provider: str | None = None) -> dict:
    """
    Returns {"claims": [{"claim", "sources", "supported"}], "checked": n, "invented": k, "unsupported": [...]}.
    On checker failure returns invented=None and an "error".
    """
    valid = {s["id"] for s in sources}
    try:
        data = config.chat_json(
            [{"role": "system", "content": prompts.GROUNDING_SYSTEM},
             {"role": "user", "content": prompts.grounding_user(utterance, sources)}],
            provider=provider, max_tokens=2000)
    except Exception as e:  # noqa: BLE001 - never block a gentle conversation on the checker
        return {"claims": [], "checked": 0, "invented": None, "unsupported": [], "error": str(e)[:200]}

    claims = []
    for c in data.get("claims", []) if isinstance(data, dict) else []:
        ids = [str(i).strip("[] ") for i in (c.get("sources") or [])]
        ids = [i for i in ids if i in valid]
        claims.append({"claim": c.get("claim", ""), "sources": ids, "supported": bool(ids)})
    unsupported = [c["claim"] for c in claims if not c["supported"]]
    return {"claims": claims, "checked": len(claims), "invented": len(unsupported), "unsupported": unsupported}


def stats(kind: str = "live", session_ids: list[str] | None = None) -> dict:
    """Aggregate grounding results over Magpie's spoken lines."""
    sql = """SELECT t.mode, t.grounding FROM turns t JOIN sessions s ON s.id = t.session_id
             WHERE t.role='magpie' AND s.kind=?"""
    args: list = [kind]
    if session_ids:
        sql += f" AND s.id IN ({','.join('?' * len(session_ids))})"
        args += session_ids
    out = {"lines": 0, "lines_checked": 0, "claims_traced": 0, "invented_spoken": 0,
           "drafts_caught": 0, "fallbacks": 0, "unchecked": 0, "by_mode": {}}
    for row in storage.query(sql, tuple(args)):
        out["lines"] += 1
        out["by_mode"][row["mode"]] = out["by_mode"].get(row["mode"], 0) + 1
        g = row["grounding"]
        if isinstance(g, str):
            g = json.loads(g)
        if not g:
            continue
        if g.get("invented") is None:
            out["unchecked"] += 1
            continue
        out["lines_checked"] += 1
        out["claims_traced"] += g.get("checked", 0)
        out["invented_spoken"] += g.get("invented", 0)
        out["drafts_caught"] += g.get("caught", 0)
        out["fallbacks"] += int(bool(g.get("fallback")))
    out["sessions"] = storage.one(
        "SELECT COUNT(*) AS n FROM sessions WHERE kind=?", (kind,))["n"] if not session_ids else len(session_ids)
    return out


def load_report() -> dict | None:
    p = storage.REPORTS / "grounding_report.json"
    if p.exists():
        r = json.loads(p.read_text(encoding="utf-8"))
        return {k: r[k] for k in ("generated_at", "provider", "stats", "sessions_run") if k in r}
    return None
