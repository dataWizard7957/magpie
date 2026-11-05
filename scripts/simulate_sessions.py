"""
Simulated visits for the grounding check: "0 invented facts across 20 sessions".

A second model plays Rose using the recall profile in data/rose.json (simulated_recall):
she remembers some stories, needs hints for others, gives fragments, or can't recall Bluebell at all.
That exercises Ask, Hint, Tell, Reflect and the gentle ending. Every Magpie line is checked by
grounding.check() on the active provider (Muse Glimmer in the final version).

    python scripts/simulate_sessions.py --n 20
    MAGPIE_PROVIDER=glimmer python scripts/simulate_sessions.py --n 20 --rose-provider meta

Writes data/reports/grounding_report.json and .md. Simulated sessions never touch the family review queue.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from magpie import engram, grounding, prompts, session, storage  # noqa: E402

SHEET = json.loads((storage.DATA / "rose.json").read_text(encoding="utf-8"))
RECALL = SHEET.get("simulated_recall", {})


def rose_reply(ctx: dict, magpie_line: str, hints: int, provider: str, tired: bool) -> str:
    story = ctx.get("story") or {}
    profile = RECALL.get(story.get("id"), {"level": "full", "behaviour": "Remembers it."})
    return config.chat(
        [{"role": "system", "content": prompts.ROSE_SIM_SYSTEM},
         {"role": "user", "content": prompts.rose_sim_user(
             profile["behaviour"], profile["level"], hints, story.get("text", ""), magpie_line, tired)}],
        provider=provider, max_tokens=3072).strip().strip('"')


def plan_items(candidates: list[dict], i: int, per_session: int) -> list[str]:
    """Rotate so every story is covered across the run; one keepsake per story."""
    by_story: dict[str, list[str]] = {}
    for c in candidates:
        by_story.setdefault(c["story_id"], []).append(c["id"])
    stories = sorted(by_story)
    start = (i * per_session) % len(stories)
    picked = [stories[(start + k) % len(stories)] for k in range(min(per_session, len(stories)))]
    return [random.choice(by_story[s]) for s in picked]


def run_one(i: int, args, candidates: list[dict]) -> dict:
    items = plan_items(candidates, i, args.per_session)
    s = session.start_session(item_ids=items, kind="sim", theme=f"Simulated visit {i + 1}")
    sid = s["id"]
    last_magpie = s["turns"][-1]["text"]
    tired_at = random.randint(2, 6) if random.random() < args.tired_rate else None
    replies = 0
    while replies < args.max_replies:
        cur = session.get_session(sid)
        if cur["ended"]:
            break
        ctx = engram.item_context(cur["items"][cur["idx"]])
        hints = storage.one("SELECT state FROM sessions WHERE id=?", (sid,))["state"]["hints_used"]
        reply = rose_reply(ctx, last_magpie, hints, args.rose_provider, tired=(replies + 1 == tired_at))
        new = session.respond(sid, reply)
        replies += 1
        spoken = [t for t in new if t["role"] == "magpie"]
        if spoken:
            last_magpie = spoken[-1]["text"]
    if not session.get_session(sid)["ended"]:
        session.end_session(sid)
    final = session.get_session(sid)
    print(f"  [{i + 1:>2}] {final['summary']}")
    return final


def write_report(results: list[dict], args, elapsed: float) -> dict:
    ids = [r["id"] for r in results]
    st = grounding.stats(kind="sim", session_ids=ids)
    caught_examples, transcripts = [], []
    for r in results:
        for t in r["turns"]:
            g = t.get("grounding") or {}
            if g.get("caught"):
                caught_examples.append({"session": r["id"], "mode": t["mode"], "final": t["text"],
                                        "fallback": bool(g.get("fallback"))})
        transcripts.append({"session": r["id"], "summary": r["summary"],
                            "turns": [{"role": t["role"], "mode": t["mode"], "text": t["text"],
                                       "invented": (t.get("grounding") or {}).get("invented")}
                                      for t in r["turns"]]})
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": config.active_provider(),
        "model": config.PROVIDERS[config.active_provider()]["model"],
        "rose_provider": args.rose_provider,
        "sessions_run": len(results),
        "seconds": round(elapsed, 1),
        "stats": st,
        "caught_examples": caught_examples[:20],
        "transcripts": transcripts,
    }
    out = storage.REPORTS / "grounding_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = [
        "# Magpie grounding report", "",
        f"- Generated: {report['generated_at']}",
        f"- Session agent + checker: **{report['model']}** (`{report['provider']}`)",
        f"- Simulated Rose: `{args.rose_provider}`",
        f"- Sessions: **{len(results)}**, Magpie lines: **{st['lines']}**, lines checked: **{st['lines_checked']}**",
        f"- Factual claims traced to Engram: **{st['claims_traced']}**",
        f"- **Invented facts spoken: {st['invented_spoken']}**",
        f"- Drafts caught and rewritten by the guard: {st['drafts_caught']} "
        f"(fell back to the family's own words {st['fallbacks']} times)",
        f"- Lines the checker could not verify: {st['unchecked']}",
        f"- Lines by mode: {', '.join(f'{k} {v}' for k, v in sorted(st['by_mode'].items()))}",
        "", "## Sessions", "",
    ] + [f"{n + 1}. {r['summary']}" for n, r in enumerate(results)]
    (storage.REPORTS / "grounding_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--per-session", type=int, default=3)
    ap.add_argument("--max-replies", type=int, default=12)
    ap.add_argument("--rose-provider", default="meta")
    ap.add_argument("--tired-rate", type=float, default=0.15, help="share of sessions where Rose gets tired")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    storage.init_db()
    if storage.is_empty():
        engram.seed("full")
    candidates = engram.session_candidates()
    if not candidates:
        sys.exit("No keepsakes with confirmed stories. Run: python -m magpie.engram --reset")

    print(f"Running {args.n} simulated visits on '{config.active_provider()}' ...")
    t = time.time()
    results = []
    for i in range(args.n):
        try:
            results.append(run_one(i, args, candidates))
        except Exception as e:  # noqa: BLE001
            print(f"  [{i + 1:>2}] FAILED: {e}")
    report = write_report(results, args, time.time() - t)
    st = report["stats"]
    print(f"\nInvented facts spoken: {st['invented_spoken']} across {len(results)} sessions "
          f"({st['claims_traced']} claims traced, {st['drafts_caught']} drafts caught).")
    print("Report: data/reports/grounding_report.md")


if __name__ == "__main__":
    main()
