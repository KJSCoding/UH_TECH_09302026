#!/usr/bin/env python3
"""
Look inside the database, or export what the dashboard needs.

    python3 query_db.py                 summary of the latest sweep, from SQL
    python3 query_db.py --export        write output/dashboard.json (what the dashboard reads)
    python3 query_db.py --sql "SELECT ..."   run any query

The database is monitor/simplyshop.db. Any SQLite viewer (DB Browser for SQLite, VS Code
SQLite extension, or the sqlite3 command) can open it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from simplyshop.db import connect, export_for_dashboard  # noqa: E402

HERE = Path(__file__).parent
DB = HERE / "simplyshop.db"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--sql")
    args = ap.parse_args()

    if not DB.exists():
        sys.exit("No database yet. Run: python3 run_sweep.py")
    con = connect(DB)

    if args.sql:
        for row in con.execute(args.sql):
            print(dict(row))
        return

    data = export_for_dashboard(con)
    if args.export:
        (HERE / "output").mkdir(exist_ok=True)
        path = HERE / "output" / "dashboard.json"
        path.write_text(json.dumps(data, indent=2))
        print(f"Wrote {path.relative_to(HERE)}")
        return

    m = data["latest_metrics"]
    print(f"Latest sweep #{m['id']} ({m['mode']}, {m['run_at']}) for {m['brand']}")
    print(f"  inclusion {m['inclusion']:.0%}   accuracy {m['accuracy']:.0%}   hallucinations {m['hallucinations']} ({m['high_severity']} critical or high risk)")
    print("\n  Hallucinations by kind (SQL GROUP BY):")
    for r in data["kinds"]:
        print(f"    {r['hallucination_kind']:22s} {r['n']:2d}   critical or high: {r['high']}")
    print("\n  Business risk level (5 = losing money now):")
    for r in data["by_risk_level"]:
        print(f"    level {r['level']} {r['label']:9s} {r['n']:2d}")
    print("\n  Risk category:")
    for r in data["by_risk_category"]:
        print(f"    {r['risk_category'].replace('_', ' '):14s} {r['n']:2d}   worst level {r['worst']}")
    print("\n  Approval queue (needs a person):")
    for r in data["approval_queue"]:
        print(f"    [{r['owner']}] {r['action'][:80]}")
    print("\n  Pages causing the most hallucinations:")
    for r in data["worst_sources"][:5]:
        print(f"    {r['hallucinations']}x  {r['url']}  ({r['owner']})")
    print("\n  Sweeps so far:", con.execute("SELECT COUNT(*) FROM sweeps").fetchone()[0])
    print("  Tables:", ", ".join(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")))


if __name__ == "__main__":
    main()
