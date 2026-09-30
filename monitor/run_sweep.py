#!/usr/bin/env python3
"""
SimplyShop monitor: one "sweep".

    python3 run_sweep.py            demo mode: uses data/sample_answers.json, no API keys needed
    python3 run_sweep.py --live     live mode: asks every assistant you have a key for

A sweep does four things:
1. Ask each AI assistant every question in data/questions.json (or load the saved sample answers).
2. Check every answer against data/products.json with the claim checker.
3. Print a short report: inclusion rate, accuracy, the wrong facts, and the pages causing them.
4. Save everything to output/sweep_<timestamp>.json so the dashboard (or a database) can load it.

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

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "output"


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
    args = ap.parse_args()

    record = load("products.json")
    questions = load("questions.json")
    by_id = {q["id"]: q for q in questions}

    answers = collect_live(questions, args.delay) if args.live else load("sample_answers.json")
    if not args.live:
        print("Demo mode: using data/sample_answers.json (run with --live to query the real assistants)")

    checker = Checker(record, load("source_snapshots.json"))
    checked = [checker.check(by_id[a["q"]], a["assistant"], a["text"], a.get("sources")) for a in answers]

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
    print(f"  Wrong facts live:              {len(errors)}  ({sum(1 for _, cl in errors if cl.severity_level == 'high')} high severity)")
    print()
    print("  Inclusion by assistant:")
    for name, v in by_assistant.items():
        print(f"    {name:11s} {v:.0%}")
    print()
    print("  Wrong facts (most severe first):")
    for c, cl in errors:
        src = f"  <- {cl.likely_source}" if cl.likely_source else ""
        print(f"    [{cl.severity_level:6s}] {cl.error_type:26s} {cl.product} on {c.assistant}: said {cl.said!r}, truth {cl.truth!r}{src}")
    if by_source:
        print()
        print("  Pages causing the most wrong facts:")
        for src, n in by_source.most_common(5):
            print(f"    {n}x  {src}")

    # ---------- save ----------
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"sweep_{stamp}.json"
    path.write_text(json.dumps({
        "brand": brand, "mode": "live" if args.live else "demo", "run_at": stamp,
        "metrics": {"inclusion": inclusion, "accuracy": accuracy, "claims": len(claims), "errors": len(errors),
                    "inclusion_by_assistant": by_assistant},
        "answers": [c.to_dict() for c in checked],
    }, indent=2))
    print(f"\nSaved {path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
