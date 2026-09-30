#!/usr/bin/env python3
"""
SimplyShop monitor: one "sweep".

    python3 run_sweep.py            demo mode: uses data/sample_answers.json, no API keys needed
    python3 run_sweep.py --live     live mode: asks every assistant you have a key for

A sweep does four things:
1. Ask each AI assistant every question in data/questions.json (or load the saved sample answers).
2. Check every answer against data/products.json with the claim checker.
3. Print a short report: inclusion rate, accuracy, the hallucinations by business risk level, and the pages causing them.
4. Save everything to the SQLite database (simplyshop.db) and a JSON copy in output/.

In production this runs on a schedule (cron or GitHub Actions) a few times a day.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from simplyshop.checker import Checker  # noqa: E402
from simplyshop.db import connect, save_sweep  # noqa: E402
from simplyshop.agent import investigate  # noqa: E402

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "output"
DB = HERE / "simplyshop.db"


def load(name: str):
    return json.loads((DATA / name).read_text())


def collect_live(questions: list[dict], delay: float) -> list[dict]:
    from simplyshop.assistants import ASSISTANTS, available

    names = available()
    if not names:
        sys.exit("No API keys found. Set OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY or PERPLEXITY_API_KEY, or run without --live.")
    print(f"Live mode: asking {', '.join(names)}")
    answers = []
    for q in questions:
        for name in names:
            _, ask = ASSISTANTS[name]
            try:
                r = ask(q["text"])
                answers.append({"q": q["id"], "assistant": name, "text": r["text"], "sources": r["sources"]})
                print(f"  {name:11s} {q['id']}  ok")
            except Exception as e:  # keep going if one provider fails
                print(f"  {name:11s} {q['id']}  failed: {e}")
            time.sleep(delay)  # be polite to the APIs
    return answers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="ask the real assistants instead of using sample answers")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between live API calls")
    ap.add_argument("--agent-llm", action="store_true", help="let an LLM (ANTHROPIC_API_KEY or OPENAI_API_KEY) drive the investigator agent")
    args = ap.parse_args()

    record = load("products.json")
    questions = load("questions.json")
    by_id = {q["id"]: q for q in questions}

    answers = collect_live(questions, args.delay) if args.live else load("sample_answers.json")
    if not args.live:
        print("Demo mode: using data/sample_answers.json (run with --live to query the real assistants)")

    snapshots = load("source_snapshots.json")
    # Returns and support tickets already linked to a wrong fact (from the company's own systems).
    # Keyed "<question>_<assistant>|<attribute>". Evidence of money leaving raises the risk level.
    harm = load("linked_harm.json") if (DATA / "linked_harm.json").exists() else {}
    checker = Checker(record, snapshots)

    def harm_for(a):
        px = f"{a['q']}_{a['assistant']}|"
        return {k[len(px):]: v for k, v in harm.items() if k.startswith(px)}

    checked = [checker.check(by_id[a["q"]], a["assistant"], a["text"], a.get("sources"), harm_for(a)) for a in answers]

    # ---------- investigator agent: one case file per hallucination ----------
    case_files = {}
    for c in checked:
        for cl in c.errors:
            cf = investigate(cl, c, record, snapshots, checker.needle(cl), use_llm=args.agent_llm)
            case_files[cf.claim_key] = cf

    # ---------- metrics ----------
    shopping = [c for c in checked if c.question_kind == "shopping"]
    inclusion = sum(1 for c in shopping if c.brand_rank) / max(1, len(shopping))
    claims = [cl for c in checked for cl in c.claims]
    accuracy = sum(1 for cl in claims if cl.ok) / max(1, len(claims))
    errors = [(c, cl) for c in checked for cl in c.errors]
    errors.sort(key=lambda x: -(x[1].severity or 0))
    by_source = Counter(cl.likely_source for _, cl in errors if cl.likely_source)
    by_assistant = {
        name: sum(1 for c in shopping if c.assistant == name and c.brand_rank) / max(1, sum(1 for c in shopping if c.assistant == name))
        for name in sorted({c.assistant for c in shopping})
    }

    # ---------- report ----------
    brand = record["brand"]
    print()
    print(f"SimplyShop sweep for {brand}  ({len(checked)} answers, {len(claims)} claims checked)")
    print(f"  Inclusion in shopping answers: {inclusion:.0%}")
    print(f"  Fact accuracy:                 {accuracy:.0%}")
    print(f"  Hallucinations live:           {len(errors)}  ({sum(1 for _, cl in errors if cl.severity >= 4)} critical or high risk)")
    print()
    print("  Business risk (company profile, 5 = losing money now):")
    for lvl in (5, 4, 3, 2, 1):
        n = sum(1 for _, cl in errors if cl.severity == lvl)
        if n:
            print(f"    level {lvl} {checker.risk['levels'][lvl]:9s} {n}")
    by_cat = Counter(cl.risk_category for _, cl in errors)
    print("  By category: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in by_cat.most_common()))
    print()
    print("  Inclusion by assistant:")
    for name, v in by_assistant.items():
        print(f"    {name:11s} {v:.0%}")
    print()
    print("  Hallucinations (highest risk first):")
    for c, cl in errors:
        src = f"  <- {cl.likely_source}" if cl.likely_source else ""
        adj = f"  [{'; '.join(cl.risk_adjustments)}]" if cl.risk_adjustments else ""
        print(f"    [L{cl.severity} {cl.severity_level:8s}] {cl.risk_category.replace('_', ' '):13s} {cl.error_type:26s} {cl.product} on {c.assistant}: said {cl.said!r}, truth {cl.truth!r}{src}{adj}")
    if case_files:
        print()
        drv = next(iter(case_files.values())).driver
        print(f"  Investigator agent ({drv}): {len(case_files)} case files, {sum(1 for x in case_files.values() if x.requires_approval)} waiting on a person, {sum(1 for x in case_files.values() if not x.requires_approval)} automatic")
        top = max(case_files.values(), key=lambda x: (x.requires_approval, x.confidence == 'high'))
        print(f"    Example: {top.product} {top.attribute}: AI said {top.ai_said!r}, approved {top.approved_value!r}")
        print(f"      cause: {top.likely_cause}")
        print(f"      draft to {top.owner}: {top.draft['subject']}")
        print(f"      recommendation: {top.recommendation}")
    # ---------- this week's plan: fixes grouped by root cause, ordered by weekly payoff ----------
    COST = {5: 5200, 4: 3200, 3: 1800, 2: 800, 1: 250}   # estimated weekly cost of one live wrong fact, by risk level
    plan: dict[str, dict] = {}
    for c, cl in errors:
        cf = case_files.get(f"{c.question_id}_{c.assistant}|{cl.attribute}")
        if cf and not cf.requires_approval:
            continue  # already fixed automatically
        key = cl.likely_source or f"{cl.product}|{cl.attribute}"
        g = plan.setdefault(key, {"n": 0, "payoff": 0, "worst": 0, "owner": cf.owner if cf else "", "draft": cf.draft["subject"] if cf else cl.error_type})
        g["n"] += 1; g["payoff"] += COST[cl.severity]; g["worst"] = max(g["worst"], cl.severity)
    if plan:
        print()
        print(f"  This week's plan ({len(plan)} jobs, ${sum(g['payoff'] for g in plan.values()):,} / wk if all approved):")
        for k, (key, g) in enumerate(sorted(plan.items(), key=lambda kv: -kv[1]["payoff"]), 1):
            print(f"    {k}. ${g['payoff']:>5,}/wk  {g['draft'][:70]}  ({g['n']} hallucination{'s' if g['n'] > 1 else ''}, worst L{g['worst']}, owner {g['owner']})")
    if by_source:
        print()
        print("  Pages causing the most hallucinations:")
        for src, n in by_source.most_common(5):
            print(f"    {n}x  {src}")

    # ---------- save ----------
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    metrics_for_db = {"claims": len(claims), "errors": len(errors), "high": sum(1 for _, cl in errors if cl.severity >= 4),
                      "inclusion": inclusion, "accuracy": accuracy}
    con = connect(DB)
    sweep_id = save_sweep(con, brand, "live" if args.live else "demo", stamp, checked, metrics_for_db, snapshots, case_files, record.get("brand_owned_sources", []))
    print(f"\nSaved sweep #{sweep_id} to {DB.name} (tables: sweeps, answers, claims, actions, source_pages). Inspect with: python3 query_db.py")
    path = OUT / f"sweep_{stamp}.json"
    path.write_text(json.dumps({
        "brand": brand, "mode": "live" if args.live else "demo", "run_at": stamp,
        "metrics": {"inclusion": inclusion, "accuracy": accuracy, "claims": len(claims), "errors": len(errors),
                    "inclusion_by_assistant": by_assistant},
        "answers": [c.to_dict() for c in checked],
    }, indent=2))
    print(f"JSON copy: {path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
