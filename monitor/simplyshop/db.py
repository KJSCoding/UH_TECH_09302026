"""
The database. SQLite, one file (simplyshop.db), nothing to install.

Tables:
  sweeps       one row per run: when, mode, headline metrics
  answers      one row per (sweep, question, assistant): the raw AI answer, rank, sources
  claims       one row per fact checked: what the AI said, the truth, ok or not, risk level and category, source
  actions      what happened about each hallucination: auto fix, approved, rejected, by whom, when
  source_pages one row per cited page: owner, last snapshot, how many hallucinations trace to it

Why SQLite: it is a real database with real SQL, judges can open it with any viewer, and it
swaps for Postgres by changing one connection line when there is more than one server.
"""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlparse
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sweeps (
  id INTEGER PRIMARY KEY,
  run_at TEXT NOT NULL,
  mode TEXT NOT NULL,                 -- 'demo' or 'live'
  brand TEXT NOT NULL,
  answers INTEGER, claims INTEGER, hallucinations INTEGER, high_severity INTEGER,   -- high_severity = risk level 4 or 5
  inclusion REAL, accuracy REAL
);
CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY,
  sweep_id INTEGER NOT NULL REFERENCES sweeps(id),
  question_id TEXT NOT NULL, question TEXT NOT NULL, question_kind TEXT NOT NULL,
  assistant TEXT NOT NULL,
  text TEXT NOT NULL,
  brand_rank INTEGER,                 -- NULL when the brand is not mentioned
  brands_in_order TEXT,               -- JSON list
  sources TEXT                        -- JSON list of cited URLs
);
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY,
  answer_id INTEGER NOT NULL REFERENCES answers(id),
  product TEXT NOT NULL, attribute TEXT NOT NULL,
  said TEXT, truth TEXT, raw TEXT,
  ok INTEGER NOT NULL,                -- 1 correct, 0 hallucination
  error_type TEXT, hallucination_kind TEXT,
  severity INTEGER, severity_level TEXT,   -- risk level 1 to 5 and its label (critical, high, moderate, low, minimal)
  risk_category TEXT, risk_reason TEXT,    -- losing_money / money_at_risk / bad_data / reputation, and why
  likely_source TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  claim_id INTEGER NOT NULL REFERENCES claims(id),
  action TEXT NOT NULL,               -- what was done or proposed
  mode TEXT NOT NULL,                 -- 'automatic' or 'needs_approval'
  owner TEXT NOT NULL,                -- accountable role
  status TEXT NOT NULL,               -- 'done', 'pending', 'approved', 'rejected'
  decided_by TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS case_files (
  id INTEGER PRIMARY KEY,
  claim_id INTEGER NOT NULL REFERENCES claims(id),
  driver TEXT NOT NULL,               -- 'rules' or 'llm:<provider>'
  likely_cause TEXT, likely_source TEXT,
  confidence TEXT,                    -- high / medium / low
  recommendation TEXT,
  draft_kind TEXT, draft_to TEXT, draft_subject TEXT, draft_body TEXT,
  evidence TEXT,                      -- JSON: every cited source and whether it holds the wrong value
  steps TEXT,                         -- JSON: the tool calls the agent made, in order
  requires_approval INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source_pages (
  url TEXT PRIMARY KEY,
  owner TEXT,                         -- 'brand' or 'third_party'
  snapshot TEXT,
  last_seen TEXT,
  hallucinations INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS claims_bad ON claims(ok, severity_level);
CREATE INDEX IF NOT EXISTS answers_sweep ON answers(sweep_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def route(claim, brand: str, source_owner: str | None) -> tuple[str, str, str]:
    """Same governance rules as the dashboard: (action, mode, owner)."""
    a = claim.attribute
    own = source_owner == "brand"
    src = claim.likely_source or "the source"
    if a in ("returns", "warranty"):
        return (f"Approve policy FAQ update and send correction request to {src}", "needs_approval", "Legal and Compliance")
    if a in ("exists", "comparison"):
        return ("Publish a current lineup page naming retired models and replacements; request correction", "needs_approval", "Content Lead")
    if own and a in ("price", "stock"):
        return (f"Resync {src} from the product record", "automatic", "Product Data Lead")
    if own:
        return (f"Retire {src} and redirect to the current product page", "needs_approval", "Content Lead")
    if a in ("price", "stock"):
        return (f"Send correction request to {src}", "needs_approval", "Partner Manager")
    return (f"Publish clarifying spec table and FAQ; request correction from {src}", "needs_approval", "Content Lead")


def save_sweep(con: sqlite3.Connection, brand: str, mode: str, run_at: str, checked: list, metrics: dict,
               snapshots: dict[str, str], case_files: dict | None = None, brand_owned: list[str] | None = None) -> int:
    """Write one sweep and everything in it. Returns the sweep id."""
    brand_owned = brand_owned or []
    cur = con.cursor()
    cur.execute(
        "INSERT INTO sweeps(run_at, mode, brand, answers, claims, hallucinations, high_severity, inclusion, accuracy) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_at, mode, brand, len(checked), metrics["claims"], metrics["errors"], metrics["high"], metrics["inclusion"], metrics["accuracy"]),
    )
    sweep_id = cur.lastrowid
    for ans in checked:
        cur.execute(
            "INSERT INTO answers(sweep_id, question_id, question, question_kind, assistant, text, brand_rank, brands_in_order, sources) VALUES (?,?,?,?,?,?,?,?,?)",
            (sweep_id, ans.question_id, ans.question, ans.question_kind, ans.assistant, ans.text, ans.brand_rank,
             json.dumps(ans.brands_in_order), json.dumps(ans.sources)),
        )
        answer_id = cur.lastrowid
        for cl in ans.claims:
            cur.execute(
                "INSERT INTO claims(answer_id, product, attribute, said, truth, raw, ok, error_type, hallucination_kind, severity, severity_level, risk_category, risk_reason, likely_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (answer_id, cl.product, cl.attribute, str(cl.said), str(cl.truth), cl.raw, int(cl.ok), cl.error_type,
                 cl.hallucination_kind, cl.severity, cl.severity_level, cl.risk_category, cl.risk_reason, cl.likely_source),
            )
            if cl.ok:
                continue
            claim_id = cur.lastrowid
            owner_kind = None
            if cl.likely_source:
                owner_kind = "brand" if any(cl.likely_source.startswith(px) for px in brand_owned) else "third_party"
                cur.execute(
                    "INSERT INTO source_pages(url, owner, snapshot, last_seen, hallucinations) VALUES (?,?,?,?,1) "
                    "ON CONFLICT(url) DO UPDATE SET hallucinations = hallucinations + 1, last_seen = excluded.last_seen, snapshot = COALESCE(excluded.snapshot, snapshot)",
                    (cl.likely_source, owner_kind, snapshots.get(cl.likely_source), run_at),
                )
            action, mode_, owner = route(cl, brand, owner_kind)
            status = "done" if mode_ == "automatic" else "pending"
            cur.execute(
                "INSERT INTO actions(claim_id, action, mode, owner, status, decided_by, decided_at) VALUES (?,?,?,?,?,?,?)",
                (claim_id, action, mode_, owner, status, "system" if status == "done" else None, run_at if status == "done" else None),
            )
            cf = (case_files or {}).get(f"{ans.question_id}_{ans.assistant}|{cl.attribute}")
            if cf:
                cur.execute(
                    "INSERT INTO case_files(claim_id, driver, likely_cause, likely_source, confidence, recommendation, draft_kind, draft_to, draft_subject, draft_body, evidence, steps, requires_approval) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (claim_id, cf.driver, cf.likely_cause, cf.likely_source, cf.confidence, cf.recommendation, cf.draft["kind"], cf.draft["to"],
                     cf.draft["subject"], cf.draft["body"], json.dumps(cf.evidence), json.dumps(cf.steps), int(cf.requires_approval)),
                )
    con.commit()
    return sweep_id


# ---------- the queries the dashboard needs ----------

QUERIES = {
    "latest_metrics": "SELECT * FROM sweeps ORDER BY id DESC LIMIT 1",
    "trend": "SELECT run_at, inclusion, accuracy, hallucinations FROM sweeps ORDER BY id",
    "open_hallucinations": """
        SELECT c.id, c.product, c.hallucination_kind, c.error_type, c.said, c.truth, c.severity, c.severity_level, c.risk_category, c.risk_reason, c.likely_source,
               a.assistant, a.question, act.action, act.mode, act.owner, act.status
        FROM claims c JOIN answers a ON a.id = c.answer_id
        LEFT JOIN actions act ON act.claim_id = c.id
        WHERE c.ok = 0 AND a.sweep_id = (SELECT MAX(id) FROM sweeps)
        ORDER BY c.severity DESC""",
    "inclusion_by_assistant": """
        SELECT assistant, AVG(CASE WHEN brand_rank IS NULL THEN 0 ELSE 1 END) AS inclusion
        FROM answers WHERE question_kind = 'shopping' AND sweep_id = (SELECT MAX(id) FROM sweeps)
        GROUP BY assistant ORDER BY inclusion DESC""",
    "worst_sources": "SELECT url, owner, hallucinations FROM source_pages ORDER BY hallucinations DESC LIMIT 10",
    "approval_queue": """
        SELECT act.id, act.action, act.owner, act.status, c.product, c.hallucination_kind, a.assistant
        FROM actions act JOIN claims c ON c.id = act.claim_id JOIN answers a ON a.id = c.answer_id
        WHERE act.mode = 'needs_approval' AND act.status = 'pending' AND a.sweep_id = (SELECT MAX(id) FROM sweeps)""",
    "case_files": """
        SELECT cf.*, c.product, c.hallucination_kind, a.assistant
        FROM case_files cf JOIN claims c ON c.id = cf.claim_id JOIN answers a ON a.id = c.answer_id
        WHERE a.sweep_id = (SELECT MAX(id) FROM sweeps) ORDER BY cf.requires_approval DESC, cf.confidence""",
    "kinds": """
        SELECT c.hallucination_kind, COUNT(*) AS n, SUM(c.severity >= 4) AS high
        FROM claims c JOIN answers a ON a.id = c.answer_id
        WHERE c.ok = 0 AND a.sweep_id = (SELECT MAX(id) FROM sweeps) GROUP BY c.hallucination_kind ORDER BY n DESC""",
    "by_risk_level": """
        SELECT c.severity AS level, c.severity_level AS label, COUNT(*) AS n
        FROM claims c JOIN answers a ON a.id = c.answer_id
        WHERE c.ok = 0 AND a.sweep_id = (SELECT MAX(id) FROM sweeps) GROUP BY c.severity ORDER BY c.severity DESC""",
    "by_risk_category": """
        SELECT c.risk_category, COUNT(*) AS n, MAX(c.severity) AS worst
        FROM claims c JOIN answers a ON a.id = c.answer_id
        WHERE c.ok = 0 AND a.sweep_id = (SELECT MAX(id) FROM sweeps) GROUP BY c.risk_category ORDER BY worst DESC, n DESC""",
}


def export_for_dashboard(con: sqlite3.Connection) -> dict:
    """Everything the dashboard shows, as one JSON object read straight from the database."""
    out = {}
    for name, sql in QUERIES.items():
        rows = [dict(r) for r in con.execute(sql).fetchall()]
        out[name] = rows[0] if name == "latest_metrics" and rows else rows
    return out
