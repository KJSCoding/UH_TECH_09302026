"""Small tests for the claim checker. Run: python3 test_checker.py"""

import json
from pathlib import Path

from simplyshop.checker import Checker

record = json.loads((Path(__file__).parent / "data" / "products.json").read_text())
checker = Checker(record)
Q = {"id": "t", "text": "test", "kind": "shopping"}


def claims(text):
    return [(c.attribute, c.said, c.ok) for c in checker.check(Q, "chatgpt", text).claims]


def test_correct_price_and_specs():
    assert claims("The Dell 14 is $449 with 8GB RAM and a 10 hour battery.") == [
        ("price", 449, True), ("ram_gb", 8, True), ("battery_hours", 10, True)]


def test_wrong_price_is_flagged():
    assert ("price", 399, False) in claims("The Dell 14 costs $399.")


def test_budget_is_not_a_price_claim():
    assert claims("For a laptop under $500 get the Dell 14.") == []


def test_competitor_numbers_are_not_credited_to_us():
    out = claims("The Dell 14 costs $449; the Kestrel Lite 14 costs $479 with 16GB RAM.")
    assert ("price", 449, True) in out and all(a != "ram_gb" for a, _, _ in out)


def test_missing_touchscreen_is_caught():
    assert ("touchscreen", False, False) in claims("The Dell 16 2-in-1 does not have a touchscreen.")


def test_policy_without_product_name():
    assert ("returns", 14, False) in claims("Dell offers a 14 day return policy.")


def test_rank_and_severity():
    a = checker.check(Q, "chatgpt", "1. Kestrel Lite 14. 2. Dell 14 for $399.")
    assert a.brand_rank == 2
    err = a.errors[0]
    assert err.severity_level == "high"  # price + shopping question + high reach assistant


def test_structured_price_parser():
    from simplyshop.prices import parse_structured_price
    html = '''<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
      "name":"Dell 14","offers":{"@type":"Offer","price":"449.00","priceCurrency":"USD",
      "availability":"https://schema.org/InStock"}}</script></html>'''
    got = parse_structured_price(html, "https://example.com/dell-14")
    assert got["price"] == 449.0 and got["availability"] == "in stock" and got["name"] == "Dell 14"


def test_fabricated_and_retired_products():
    out = claims("Go with the Dell Latitude 5400, or the Dell Inspiron 3000 at $349.")
    kinds = {(c[0], c[1]) for c in out}
    assert ("exists", "exists") in kinds            # model not in the record
    assert ("exists", "current product") in kinds   # retired model presented as current


def test_misleading_comparison_against_retired_product():
    out = claims("Dell 14 vs Dell Inspiron 3000: the Inspiron wins on price.")
    assert any(a == "comparison" for a, _, _ in out)


def test_real_products_are_not_flagged_as_fabricated():
    assert all(a != "exists" for a, _, _ in claims("The Dell 14 and Dell Pro 16 are both good."))


def test_database_round_trip(tmp_path=None):
    import tempfile
    from simplyshop.db import connect, save_sweep, export_for_dashboard
    a = checker.check(Q, "chatgpt", "The Dell 14 is $399, and Dell offers a 14 day return policy.", ["https://retailhub.example/dell-14"])
    with tempfile.TemporaryDirectory() as d:
        con = connect(f"{d}/t.db")
        save_sweep(con, "Dell", "demo", "now", [a], {"claims": 2, "errors": 2, "high": 2, "inclusion": 1.0, "accuracy": 0.0}, {})
        out = export_for_dashboard(con)
    assert out["latest_metrics"]["hallucinations"] == 2
    assert len(out["open_hallucinations"]) == 2
    assert any(r["owner"] == "Legal and Compliance" for r in out["approval_queue"])
    assert out["worst_sources"][0]["owner"] == "third_party"


def test_agent_drafts_correction_and_needs_approval():
    from simplyshop.agent import investigate
    a = checker.check(Q, "chatgpt", "The Dell 14 is $399.", ["https://retailhub.example/dell-14"])
    cf = investigate(a.errors[0], a, record, {"https://retailhub.example/dell-14": "Dell 14 $399 sold out"}, checker.needle(a.errors[0]))
    assert cf.likely_source == "https://retailhub.example/dell-14"
    assert cf.confidence == "high" and cf.requires_approval
    assert cf.draft["kind"] == "correction_request" and "$449" not in cf.draft["body"] or "449" in cf.draft["body"]
    assert "lookup_fact" in cf.steps[0] and "draft_correction" in cf.steps[-1]


def test_agent_marks_brand_feed_resync_automatic():
    from simplyshop.agent import investigate
    a = checker.check(Q, "chatgpt", "The Dell Pro 16 is in stock.", ["https://feeds.example/dell-retail-feed"])
    cf = investigate(a.errors[0], a, record, {"https://feeds.example/dell-retail-feed": "Dell Pro 16 in stock"}, checker.needle(a.errors[0]))
    assert cf.draft["kind"] == "resync_brand_source" and not cf.requires_approval


def test_agent_never_auto_publishes_policy_or_content():
    from simplyshop.agent import investigate
    a = checker.check(Q, "chatgpt", "Dell offers a 14 day return policy.", ["https://dealsforum.example/dell-returns-2023"])
    cf = investigate(a.errors[0], a, record, {"https://dealsforum.example/dell-returns-2023": "14 day return"}, checker.needle(a.errors[0]))
    assert cf.requires_approval and cf.owner == "Legal and Compliance"


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} tests passed")
