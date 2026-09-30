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


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} tests passed")
