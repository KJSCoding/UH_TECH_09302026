"""
The investigator agent. Runs on every hallucination the checker finds.

What it does, in the case prompt's words: it uses AI where it adds clear value, and it never
acts on its own. For each wrong claim it:

1. Looks up the approved fact in the product record.
2. Pulls the snapshot of every cited source and finds which one contains the wrong value.
3. Decides the likely cause (stale listing, archived page, old review, made up).
4. Drafts the correction: the text to send to the source owner, or the FAQ line to publish.
5. Writes a case file with a recommendation and hands it to the accountable owner.

The owner reads a finished case file and clicks approve or reject. The agent drafts, a person publishes.

How it is built: an agent loop over a small set of tools (below). Two drivers can run the loop:
  - RuleDriver: deterministic, no API key, runs in demo mode so judges get complete case files.
  - LLMDriver:  an LLM (Anthropic or OpenAI) picks which tool to call next and writes the draft.
                Used when ANTHROPIC_API_KEY or OPENAI_API_KEY is set and --agent-llm is passed.
Both drivers use the same tools, so the LLM can never touch anything the rules could not.
The LLM only ever writes drafts; it cannot publish, resync, or contact anyone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

# ---------------------------------------------------------------- tools

@dataclass
class CaseContext:
    claim: Any                    # Claim from checker
    answer: Any                   # CheckedAnswer from checker
    record: dict                  # product record
    snapshots: dict[str, str]     # url -> page text snapshot
    brand: str


def tool_lookup_fact(ctx: CaseContext, product: str, attribute: str) -> dict:
    """What does the approved product record say?"""
    if attribute in ("returns", "warranty"):
        key = "returns_days" if attribute == "returns" else "warranty_years"
        return {"product": f"{ctx.brand} policy", "attribute": attribute, "approved_value": ctx.record["policy"][key], "owner": "Legal and Compliance"}
    for p in ctx.record["products"]:
        if p["name"] == product:
            return {"product": product, "attribute": attribute, "approved_value": p.get(attribute), "owner": "Product Data Lead"}
    for r in ctx.record.get("retired_products", []):
        if r["name"] == product:
            return {"product": product, "attribute": attribute, "approved_value": f"retired {r['retired']}", "owner": "Content Lead"}
    return {"product": product, "attribute": attribute, "approved_value": None, "note": "not in the record: not a current model"}


def brand_owns(ctx: CaseContext, url: str) -> bool:
    """A source is the brand's only if the product record says so. Never guessed from the URL."""
    return any(url.startswith(prefix) for prefix in ctx.record.get("brand_owned_sources", []))


def tool_inspect_sources(ctx: CaseContext, needle: str) -> list[dict]:
    """Which cited pages contain the wrong value?"""
    out = []
    for url in ctx.answer.sources:
        snap = ctx.snapshots.get(url, "")
        out.append({"url": url, "contains_wrong_value": needle.lower() in snap.lower(), "snapshot": snap[:160],
                    "owner": "brand" if brand_owns(ctx, url) else "third_party",
                    "looks_stale": any(k in url.lower() for k in ("archive", "2022", "2023", "2024", "old"))})
    return out


def tool_classify_cause(ctx: CaseContext, sources: list[dict]) -> str:
    hits = [s for s in sources if s["contains_wrong_value"]]
    if not hits:
        return "no cited source contains the wrong value: likely model error or an uncited source"
    s = hits[0]
    if s["owner"] == "brand" and s["looks_stale"]:
        return "an archived or outdated page on the brand's own site"
    if s["owner"] == "brand":
        return "the brand's own feed or page is out of sync with the product record"
    if s["looks_stale"]:
        return "an old third party page (review, forum, spec listing) still being cited"
    return "a current third party listing with a wrong value"


def tool_draft_correction(ctx: CaseContext, fact: dict, cause: str, source: dict | None) -> dict:
    """Write the correction the owner will approve. Plain, specific, ready to send."""
    c = ctx.claim
    said = c.said if not isinstance(c.said, bool) else ("has a touchscreen" if c.said else "no touchscreen")
    truth = fact.get("approved_value")
    product = fact["product"]
    if c.attribute in ("exists", "comparison"):
        return {
            "kind": "publish_lineup_page",
            "to": "Content Lead",
            "subject": f"Publish current {ctx.brand} lineup page naming retired models",
            "body": (f"AI assistants are recommending {product}, which was {truth}. Publish a lineup page that lists retired models "
                     f"with their replacements and dates, and add structured data so assistants pick it up. "
                     f"Request that {source['url'] if source else 'the citing page'} mark the review as outdated."),
        }
    if source and source["owner"] == "brand" and source["looks_stale"]:
        return {
            "kind": "retire_brand_page",
            "to": "Content Lead",
            "subject": f"Retire {source['url']} and redirect to the current product page",
            "body": f"{product}: this archived page still says {said}; the product record says {truth}. Retire it and redirect so assistants stop citing it.",
        }
    if source and source["owner"] == "brand":
        return {
            "kind": "resync_brand_source",
            "to": "Product Data Lead",
            "subject": f"Resync {source['url']} from the product record",
            "body": f"{product}: feed shows {said}, product record says {truth}. Resync from the product record.",
        }
    to = "Legal and Compliance" if c.attribute in ("returns", "warranty") else "Partner Manager"
    return {
        "kind": "correction_request",
        "to": to,
        "subject": f"Correction request: {product} {c.attribute} on {source['url'] if source else 'cited page'}",
        "body": (f"Hello, your page currently lists {product} with {said}. The current, approved value is {truth} "
                 f"(product record, checked today). Could you update the listing? Source of truth: the {ctx.brand} product feed. Thank you."),
        "faq_line": f"{product}: {c.attribute} is {truth}." if c.attribute in ("returns", "warranty") else None,
    }


TOOLS: dict[str, Callable] = {
    "lookup_fact": tool_lookup_fact,
    "inspect_sources": tool_inspect_sources,
    "classify_cause": tool_classify_cause,
    "draft_correction": tool_draft_correction,
}

# ---------------------------------------------------------------- case file

@dataclass
class CaseFile:
    claim_key: str
    product: str
    attribute: str
    ai_said: str
    approved_value: str
    likely_cause: str
    likely_source: str | None
    evidence: list[dict]
    draft: dict
    recommendation: str
    confidence: str            # high / medium / low
    requires_approval: bool
    owner: str
    driver: str                # "rules" or "llm:<model>"
    steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- drivers

class RuleDriver:
    """Deterministic driver. Calls the tools in a fixed order. No API key."""
    name = "rules"

    def run(self, ctx: CaseContext, needle: str) -> CaseFile:
        c = ctx.claim
        steps = []
        fact = tool_lookup_fact(ctx, c.product, c.attribute); steps.append(f"lookup_fact({c.product}, {c.attribute}) -> {fact.get('approved_value')}")
        sources = tool_inspect_sources(ctx, needle); steps.append(f"inspect_sources('{needle}') -> {sum(s['contains_wrong_value'] for s in sources)} of {len(sources)} contain it")
        cause = tool_classify_cause(ctx, sources); steps.append(f"classify_cause -> {cause}")
        hit = next((s for s in sources if s["contains_wrong_value"]), None)
        draft = tool_draft_correction(ctx, fact, cause, hit); steps.append(f"draft_correction -> {draft['kind']} to {draft['to']}")
        conf = "high" if hit else ("medium" if sources else "low")
        auto = draft["kind"] == "resync_brand_source" and c.attribute in ("price", "stock")
        rec = ("Resync automatically: the fix only copies an already approved fact to the brand's own page."
               if auto else f"Approve and send: {draft['subject']}.")
        return CaseFile(
            claim_key=f"{ctx.answer.question_id}_{ctx.answer.assistant}|{c.attribute}", product=c.product, attribute=c.attribute,
            ai_said=str(c.said), approved_value=str(fact.get("approved_value")), likely_cause=cause,
            likely_source=hit["url"] if hit else None, evidence=sources, draft=draft, recommendation=rec,
            confidence=conf, requires_approval=not auto, owner=draft["to"], driver=self.name, steps=steps,
        )


class LLMDriver:
    """An LLM chooses tool calls and writes the draft. Falls back to the rules if anything fails.
    The LLM sees the same tools and nothing else; it cannot publish or contact anyone."""

    SYSTEM = ("You are SimplyShop's hallucination investigator. You have four tools. Investigate the wrong claim, "
              "then return JSON: {likely_cause, recommendation, confidence: high|medium|low, draft: {subject, body}}. "
              "Draft text must be plain, specific, and ready for a human to approve. Never claim to know what trained the model; "
              "say 'likely cited source'. You cannot publish or send anything.")

    def __init__(self):
        self.provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai" if os.environ.get("OPENAI_API_KEY") else None
        self.name = f"llm:{self.provider}" if self.provider else "rules"

    def run(self, ctx: CaseContext, needle: str) -> CaseFile:
        base = RuleDriver().run(ctx, needle)   # tools run first; the LLM refines cause, recommendation and draft wording
        if not self.provider:
            return base
        try:
            prompt = json.dumps({"claim": {"product": base.product, "attribute": base.attribute, "ai_said": base.ai_said,
                                            "approved_value": base.approved_value},
                                 "tool_results": {"sources": base.evidence, "rule_based_cause": base.likely_cause,
                                                  "rule_based_draft": base.draft}})
            text = _call_llm(self.provider, self.SYSTEM, prompt)
            j = json.loads(text[text.find("{"): text.rfind("}") + 1])
            base.likely_cause = j.get("likely_cause", base.likely_cause)
            base.recommendation = j.get("recommendation", base.recommendation)
            base.confidence = j.get("confidence", base.confidence)
            if isinstance(j.get("draft"), dict):
                base.draft = {**base.draft, **{k: v for k, v in j["draft"].items() if k in ("subject", "body")}}
            base.driver = self.name
            base.steps.append(f"llm({self.provider}) refined cause, recommendation and draft")
        except Exception as e:  # never let the LLM break the pipeline
            base.steps.append(f"llm failed ({e.__class__.__name__}); kept rule based case file")
        return base


def _call_llm(provider: str, system: str, user: str) -> str:
    import urllib.request
    if provider == "anthropic":
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
            data=json.dumps({"model": "claude-3-5-haiku-latest", "max_tokens": 600, "system": system,
                             "messages": [{"role": "user", "content": user}]}).encode(),
            headers={"Content-Type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return "".join(b.get("text", "") for b in json.loads(r.read())["content"])
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- entry point

def investigate(claim, answer, record: dict, snapshots: dict[str, str], needle: str, use_llm: bool = False) -> CaseFile:
    ctx = CaseContext(claim=claim, answer=answer, record=record, snapshots=snapshots, brand=record["brand"])
    driver = LLMDriver() if use_llm else RuleDriver()
    return driver.run(ctx, needle)
