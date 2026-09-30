"""
Live price fetcher: the "verified data layer" in its simplest form.

Order of preference, most reliable first:
1. The brand's own product feed (a JSON or CSV file the brand already publishes for
   Google Shopping and retailers). No scraping needed. That is what data/products.json stands in for.
2. Retailer APIs (Best Buy has a free developer API; Amazon and Walmart expose price and
   stock through their affiliate APIs, which also pay a commission when a shopper buys).
3. Reading a public product page, only where no API exists.

For option 3 we do not parse messy HTML. Most retailer product pages include a block of
structured data (schema.org "Product" JSON, the same thing Google reads) with the price,
currency and availability. This function pulls that block out.

Rules we follow when reading pages: respect robots.txt, one request every few seconds,
never behind a login, never personal data, and use the API when one exists.

    python3 -c "from simplyshop.prices import fetch_price; print(fetch_price('https://www.example.com/product'))"
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = "SimplyShopBot/0.1 (+https://simplyshop-uh.lovable.app; price verification; contact privacy@simplyshop.example)"


def allowed_by_robots(url: str) -> bool:
    """Check the site's robots.txt before fetching anything."""
    parts = urlparse(url)
    rp = RobotFileParser()
    try:
        rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True  # no robots.txt reachable: proceed politely


def fetch_price(url: str, timeout: int = 20) -> dict | None:
    """Return {"price", "currency", "availability", "name", "checked_at", "source"} or None."""
    if not allowed_by_robots(url):
        return {"error": "blocked by robots.txt", "source": url}
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", errors="replace")
    return parse_structured_price(html, url)


def parse_structured_price(html: str, url: str = "") -> dict | None:
    """Find schema.org Product data in the page and pull out the offer."""
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for item in _walk(data):
            if str(item.get("@type", "")).lower() == "product":
                offer = item.get("offers") or {}
                if isinstance(offer, list):
                    offer = offer[0] if offer else {}
                price = offer.get("price") or offer.get("lowPrice")
                if price is None:
                    continue
                avail = str(offer.get("availability", "")).split("/")[-1]  # e.g. "InStock"
                return {
                    "name": item.get("name"),
                    "price": float(str(price).replace(",", "")),
                    "currency": offer.get("priceCurrency", "USD"),
                    "availability": {"InStock": "in stock", "OutOfStock": "sold out", "BackOrder": "backorder",
                                     "Discontinued": "discontinued"}.get(avail, avail.lower() or "unknown"),
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source": url,
                }
    return None


def _walk(node):
    """Yield every dict inside a JSON structure (schema blocks can be nested or in @graph lists)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
