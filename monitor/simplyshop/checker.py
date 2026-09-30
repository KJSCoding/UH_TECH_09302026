"""
Claim checker: turns an AI answer into individual facts and grades each one
against the brand's product record.

How it works, step by step:
1. Split the answer into sentences.
2. Figure out which product each sentence is about (the last product name seen).
3. Pull out claims with small patterns: "$449", "8GB RAM", "512GB SSD",
   "10 hour battery", "touchscreen", "in stock", "30 day return", "1 year warranty".
4. Compare each claim to the product record. Anything that does not match is an error.
5. Score each error for customer harm and note which cited source most likely caused it.

This is deliberately rule based so it is easy to read and test. In production an LLM
would extract claims from messier answers and this checker would validate them.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from dataclasses import dataclass, field, asdict
from typing import Any

# Business risk model. Severity is not "how wrong is the fact", it is "what does this wrong fact
# cost the company". Every hallucination lands in one of four categories, and the company's risk
# profile says which level (1 to 5) each category is worth. Dell's defaults are below; a retailer
# like Home Depot would keep losing_money at 5 and might put reputation at 4. The profile lives in
# data/products.json under "risk_profile" so each company can tune it without touching code.
DEFAULT_RISK_PROFILE = {
    "levels": {5: "critical", 4: "high", 3: "moderate", 2: "low", 1: "minimal"},
    "category_level": {
        "losing_money": 5,    # money is leaving right now: refunds, cancelled orders, honored goodwill, chargebacks
        "money_at_risk": 4,   # money involved but not yet lost: shoppers rule us out or buy elsewhere
        "bad_data": 3,        # a wrong spec: no money moves on its own, but shoppers decide on bad information
        "reputation": 3,      # nothing to buy at the end: shoppers hunt for a product we do not sell and lose trust
    },
    # Adjustments. "Doable and no reputation harm" is what makes something low risk.
    "informational_drop": 1,     # bad_data in a non shopping answer drops one level
    "low_reach_drop": 1,         # bad_data from a low reach assistant drops one more level
    "returns_escalate_at": 5,    # this many linked returns in 30 days means money is leaving: straight to level 5
    "tickets_escalate_at": 30,   # this many linked support tickets, or any returns at all, bumps one level
}
CATEGORY_LABEL = {
    "losing_money": "Losing money", "money_at_risk": "Money at risk",
    "bad_data": "Bad data", "reputation": "Reputation",
}
ERROR_TYPE = {
    "price": "Wrong price", "ram_gb": "Wrong spec", "storage_gb": "Wrong spec",
    "battery_hours": "Wrong spec", "touchscreen": "Made up or missing feature",
    "stock": "Stale availability", "returns": "Wrong policy", "warranty": "Wrong policy",
    "exists": "Made up product", "comparison": "Made up comparison",
}
# Hallucination kinds, in the case prompt's words. Every error maps to one.
HALLUCINATION_KIND = {
    "price": "incorrect pricing", "stock": "incorrect availability", "returns": "incorrect policy",
    "warranty": "incorrect policy", "touchscreen": "incorrect feature", "ram_gb": "incorrect feature",
    "storage_gb": "incorrect feature", "battery_hours": "incorrect feature",
    "exists": "fabricated product", "comparison": "misleading comparison",
}
# Rough share of AI shopping traffic per assistant. Used only for severity weighting.
ASSISTANT_REACH = {"chatgpt": 0.4, "gemini": 0.2, "claude": 0.15, "copilot": 0.15, "perplexity": 0.1}


@dataclass
class Claim:
    product: str
    attribute: str
    said: Any
    truth: Any
    raw: str
    ok: bool
    error_type: str | None = None
    severity: int | None = None
    severity_level: str | None = None
    likely_source: str | None = None
    hallucination_kind: str | None = None
    risk_category: str | None = None      # losing_money / money_at_risk / bad_data / reputation
    risk_reason: str | None = None        # one sentence a finance person would accept
    risk_adjustments: list[str] = field(default_factory=list)


@dataclass
class CheckedAnswer:
    question_id: str
    question: str
    question_kind: str
    assistant: str
    text: str
    sources: list[str]
    brand_rank: int | None
    brands_in_order: list[str]
    claims: list[Claim] = field(default_factory=list)

    @property
    def errors(self) -> list[Claim]:
        return [c for c in self.claims if not c.ok]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["error_count"] = len(self.errors)
        return d


class Checker:
    def __init__(self, record: dict, snapshots: dict[str, str] | None = None):
        self.snapshots = {k: v.lower() for k, v in (snapshots or {}).items() if not k.startswith("_")}
        self.brand = record["brand"]
        self.policy = record["policy"]
        self.products = record["products"]
        self.competitors = record.get("competitors", [])
        self.brand_owned = record.get("brand_owned_sources", [])
        # Company specific risk profile, merged over the defaults so a partial profile works.
        self.risk = {**DEFAULT_RISK_PROFILE, **record.get("risk_profile", {})}
        self.risk["levels"] = {int(k): v for k, v in {**DEFAULT_RISK_PROFILE["levels"], **self.risk.get("levels", {})}.items()}
        self.risk["category_level"] = {**DEFAULT_RISK_PROFILE["category_level"], **self.risk.get("category_level", {})}
        # Products that were retired. If an AI recommends one, that is a hallucination.
        self.retired = {r["name"]: r for r in record.get("retired_products", [])}
        # Pattern for "<Brand> <Something> <number>" so we can spot model names we do not sell.
        self._model_re = re.compile(rf"\b{re.escape(self.brand)}\s+(?:[A-Z][\w-]*\s+){{0,2}}\d{{2,4}}\b")
        # Longest alias first so "Dell Pro 16" is not read as "Dell 16".
        self.aliases = sorted(
            [(alias, p) for p in self.products for alias in p["aliases"]],
            key=lambda x: -len(x[0]),
        )

    # ---------- public API ----------

    def check(self, question: dict, assistant: str, text: str, sources: list[str] | None = None,
              harm: dict[str, dict] | None = None) -> CheckedAnswer:
        """harm: optional {attribute: {"returns": n, "tickets": n}} already linked to this answer
        from the company's returns and support systems. It can only raise the risk level."""
        sources = sources or []
        harm = harm or {}
        rank, order = self._rank(text)
        answer = CheckedAnswer(
            question_id=question["id"], question=question["text"], question_kind=question["kind"],
            assistant=assistant, text=text, sources=sources, brand_rank=rank, brands_in_order=order,
        )
        for claim in self._extract_claims(text):
            if not claim.ok:
                claim.error_type = ERROR_TYPE[claim.attribute]
                claim.hallucination_kind = HALLUCINATION_KIND[claim.attribute]
                claim.risk_category, claim.risk_reason = self._risk_category(claim)
                claim.severity, claim.severity_level, claim.risk_adjustments = self._severity(
                    claim, question["kind"], assistant, harm.get(claim.attribute))
                claim.likely_source = self._trace(claim, sources)
            answer.claims.append(claim)
        return answer

    # ---------- steps ----------

    def _find_product(self, sentence: str) -> dict | None:
        best = None
        for alias, product in self.aliases:
            i = sentence.find(alias)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, product)
        return best[1] if best else None

    def _extract_claims(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        current: dict | None = None
        self._phantoms(text, claims)
        for sentence in re.split(r"(?<=[.!?;])\s+", text):
            product = self._find_product(sentence)
            if product:
                current = product
            elif any(c in sentence for c in self.competitors):
                current = None  # sentence is about a competitor, do not credit its numbers to us

            # Cut off any trailing competitor clause so its numbers are not read as ours.
            own = sentence
            if product:
                start = min(own.find(a) for a in product["aliases"] if a in own)
                for comp in self.competitors:
                    j = own.find(comp)
                    if j > start:
                        own = own[:j]

            if current:
                self._product_claims(own, current, claims)
            if current or self.brand.lower() in sentence.lower():
                self._policy_claims(sentence, claims)
        return claims

    def _phantoms(self, text: str, out: list[Claim]) -> None:
        """Catch product names that look like ours but are not in the record (fabricated or retired),
        and comparisons against a retired product presented as current."""
        known = {a for a, _ in self.aliases}
        seen = set()
        for m in self._model_re.finditer(text):
            name = m.group(0)
            if name in seen or any(name.startswith(a) or a.startswith(name) for a in known):
                continue
            seen.add(name)
            if name in self.retired:
                r = self.retired[name]
                out.append(Claim(name, "exists", "current product", f"retired {r['retired']}", name, False))
            else:
                out.append(Claim(name, "exists", "exists", "not a real model", name, False))
        # A comparison that pits us against a product that no longer exists is misleading.
        for retired_name in self.retired:
            if retired_name in text and re.search(r"\bvs\.?\b|\bversus\b|\bcompared?\b|\bcomparison\b|better than|beats|wins on", text, re.I):
                if not any(c.attribute == "comparison" and c.product == retired_name for c in out):
                    out.append(Claim(retired_name, "comparison", "compared as current",
                                     f"retired {self.retired[retired_name]['retired']}", retired_name, False))

    def _product_claims(self, s: str, p: dict, out: list[Claim]) -> None:
        def add(attr: str, said: Any, raw: str) -> None:
            out.append(Claim(p["name"], attr, said, p[attr], raw, said == p[attr]))

        for m in re.finditer(r"\$(\d[\d,]*)", s):
            # Skip "under $500" style budgets. Those are not price claims.
            if re.search(r"(under|below|less than|up to|within)\s*$", s[: m.start()], re.I):
                continue
            add("price", int(m.group(1).replace(",", "")), m.group(0))
            break
        if m := re.search(r"(\d+)\s?GB(?: of)? RAM", s, re.I):
            add("ram_gb", int(m.group(1)), m.group(0))
        if m := re.search(r"(\d+)\s?(GB|TB)\s?(?:SSD|of storage|storage)", s, re.I):
            n = int(m.group(1)) * (1024 if m.group(2).upper() == "TB" else 1)
            add("storage_gb", n, m.group(0))
        if m := re.search(r"(\d+)[\s-]?hours?", s, re.I):
            add("battery_hours", int(m.group(1)), m.group(0))
        if m := re.search(r"(does not|doesn't|no|without a|non)[\s-]?(have a )?touch\s?screen|non-touch", s, re.I):
            add("touchscreen", False, m.group(0))
        elif m := re.search(r"touch\s?screen", s, re.I):
            add("touchscreen", True, m.group(0))
        for pattern, value in [
            (r"sold out|out of stock", "sold out"), (r"discontinued", "discontinued"),
            (r"backorder", "backorder"), (r"in stock|available now", "in stock"),
        ]:
            if m := re.search(pattern, s, re.I):
                add("stock", value, m.group(0))
                break

    def _policy_claims(self, s: str, out: list[Claim]) -> None:
        if m := re.search(r"(\d+)[\s-]day return", s, re.I):
            said = int(m.group(1))
            out.append(Claim(f"{self.brand} policy", "returns", said, self.policy["returns_days"], m.group(0), said == self.policy["returns_days"]))
        if m := re.search(r"(\d+)[\s-]year warranty", s, re.I):
            said = int(m.group(1))
            out.append(Claim(f"{self.brand} policy", "warranty", said, self.policy["warranty_years"], m.group(0), said == self.policy["warranty_years"]))

    def _rank(self, text: str) -> tuple[int | None, list[str]]:
        """Position of the brand among the products named. Only real, current products count:
        an AI recommending a retired or made up model is not real inclusion."""
        real = [(a, self.brand) for a, p in self.aliases if p.get("stock") != "discontinued"]
        names = real + [(c, c) for c in self.competitors]
        hits = sorted((text.find(n), b) for n, b in names if text.find(n) >= 0)
        # Policy answers mention the brand without a product; count that as a mention too.
        if not any(b == self.brand for _, b in hits) and self.brand in text and not self._model_re.search(text):
            hits.append((text.find(self.brand), self.brand))
            hits.sort()
        order: list[str] = []
        for _, b in hits:
            if b not in order:
                order.append(b)
        return (order.index(self.brand) + 1 if self.brand in order else None), order

    def _risk_category(self, claim: Claim) -> tuple[str, str]:
        """Which kind of business harm does this wrong fact cause? The direction of the error matters:
        a price quoted too low loses money at checkout, a price quoted too high loses the sale."""
        a, said, truth, brand = claim.attribute, claim.said, claim.truth, self.brand
        if a == "price":
            if said < truth:
                return "losing_money", f"AI quoted ${said}, {brand} charges ${truth}: shoppers arrive expecting the lower price, then abandon at checkout or demand a price match"
            return "money_at_risk", f"AI quoted ${said}, {brand} charges ${truth}: shoppers rule {brand} out on price before they reach the site"
        if a == "stock":
            if truth == "in stock" and said != "in stock":
                return "money_at_risk", f"AI says {said} but it is in stock: shoppers who would have bought go to a competitor"
            if said == "in stock" and truth == "backorder":
                return "losing_money", "AI says in stock but it is on backorder: orders are placed, then delayed or cancelled, with refunds and support tickets"
            if said == "in stock":
                return "reputation", f"AI says in stock but the product is {truth}: shoppers hunt for something {brand} does not sell and lose trust"
            return "bad_data", f"AI says {said}, the record says {truth}: wrong availability with no direct sale attached"
        if a in ("returns", "warranty"):
            unit = "day" if a == "returns" else "year"
            if said > truth:
                return "losing_money", f"AI promised a {said} {unit} {a} policy, the real one is {truth} {unit}{'' if truth == 1 else 's'}: honored goodwill refunds, denied claims turning into disputes and chargebacks"
            return "money_at_risk", f"AI understated the {a} policy ({said} vs {truth} {unit}s): shoppers who want the safety net buy elsewhere"
        if a in ("exists", "comparison"):
            what = "recommends" if a == "exists" else "compares against"
            return "reputation", f"AI {what} {claim.product}, which {brand} does not sell ({truth}): shoppers cannot find it and blame {brand}"
        return "bad_data", f"Wrong {ERROR_TYPE[a].lower().replace('wrong ', '')}: no money moves on its own, but shoppers decide on bad information"

    def _severity(self, claim: Claim, kind: str, assistant: str, harm: dict | None) -> tuple[int, str, list[str]]:
        """Risk level 1 to 5 from the company profile, then adjusted by evidence."""
        p = self.risk
        level = p["category_level"][claim.risk_category]
        why: list[str] = []
        if claim.risk_category == "bad_data":
            if kind != "shopping" and p["informational_drop"]:
                level -= p["informational_drop"]; why.append("informational question, nobody was about to buy")
            if ASSISTANT_REACH.get(assistant, 0) < 0.3 and p["low_reach_drop"]:
                level -= p["low_reach_drop"]; why.append("low reach assistant")
        if harm:
            r, t = harm.get("returns", 0), harm.get("tickets", 0)
            if r >= p["returns_escalate_at"]:
                if level < 5:
                    why.append(f"{r} returns already linked to this wrong fact: money is leaving")
                level = 5
            elif r > 0 or t >= p["tickets_escalate_at"]:
                level += 1; why.append(f"{r} returns and {t} support tickets linked")
        level = max(1, min(5, level))
        return level, p["levels"][level], why

    def _trace(self, claim: Claim, sources: list[str]) -> str | None:
        """Which cited page most likely taught the AI the wrong value?
        1. A cited page whose snapshot contains the wrong value.
        2. Otherwise a cited third party or archived page.
        3. Otherwise None (the AI made it up or used an uncited source)."""
        if not sources:
            return None
        needle = self._needle(claim)
        for url in sources:
            snap = self.snapshots.get(url)
            if snap and needle in snap:
                return url
        outside = [u for u in sources if not any(u.startswith(px) for px in self.brand_owned) or "archive" in u.lower()]
        return outside[0] if outside else None

    def needle(self, claim: Claim) -> str:
        return self._needle(claim)

    def _needle(self, claim: Claim) -> str:
        a, v = claim.attribute, claim.said
        return {
            "price": f"${v}", "ram_gb": f"{v}gb ram", "battery_hours": f"{v} hour",
            "touchscreen": "touchscreen" if v else "non-touch", "stock": str(v),
            "returns": f"{v} day", "warranty": f"{v} year",
            "exists": claim.product, "comparison": claim.product,
        }.get(a, str(v)).lower()
