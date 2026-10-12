"""Connects to amazon.com, searches for a product and parses the results.

Amazon sometimes sends a stripped-down page (products without prices) to automated
requests. To cope, the search is tried as several different browsers, and if prices are
still missing the individual product pages are opened to read the price.
"""
import random
import re
import time

import requests
from bs4 import BeautifulSoup

import config

HOME_URL = "https://www.amazon.com/"
SEARCH_URL = "https://www.amazon.com/s"
DETAIL_URL = "https://www.amazon.com/dp/{asin}"
RETAILER_NAME = "Amazon.com"
RETAILER_EMAIL = "Not available"   # Amazon search pages do not publish seller emails
DEBUG_FILE = config.BASE_DIR / "debug_amazon.html"

SEARCH_ATTEMPTS = getattr(config, "SEARCH_ATTEMPTS", 3)
DETAIL_LOOKUPS = getattr(config, "DETAIL_LOOKUPS", 6)
SEARCH_PAGES = getattr(config, "SEARCH_PAGES", 3)
MAX_RESULTS = getattr(config, "MAX_RESULTS", 60)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Referer": HOME_URL,
}


class ScraperError(Exception):
    """Raised when the remote search fails in a way the user should be told about."""


def log(*parts):
    print("[scraper]", *parts, flush=True)       # visible in the terminal running the app


# ----------------------------------------------------------------- parsing helpers
def _parse_price(text):
    """'$1,299.99' -> 1299.99 (returns None if no number is found)."""
    match = re.search(r"\d[\d,]*\.?\d*", text or "")
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def _extract_price(card):
    """Try several places where Amazon puts the price."""
    tag = card.select_one("span.a-price span.a-offscreen")
    if tag and tag.get_text(strip=True):
        price = _parse_price(tag.get_text())
        if price:
            return price

    whole = card.select_one("span.a-price-whole")
    if whole:
        digits = whole.get_text().replace(",", "").replace(".", "").strip()
        fraction = card.select_one("span.a-price-fraction")
        cents = fraction.get_text(strip=True) if fraction else "00"
        if digits.isdigit() and cents.isdigit():
            return float(f"{digits}.{cents}")

    match = re.search(r"\$\s?(\d[\d,]*(?:\.\d{2})?)", card.get_text(" "))
    return _parse_price(match.group(1)) if match else None


def _extract_rating(card):
    tag = card.select_one("span.a-icon-alt") or card.select_one('[aria-label*="out of 5 stars"]')
    if not tag:
        return None
    text = tag.get_text() or tag.get("aria-label", "")
    match = re.search(r"\d+(\.\d+)?", text)
    return float(match.group()) if match else None


def _extract_name(card):
    for selector in ("h2", "span.a-text-normal"):
        tag = card.select_one(selector)
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
    return None


def _find_cards(soup):
    cards = soup.select('div[data-component-type="s-search-result"]')
    if not cards:                                   # fallback for other page layouts
        cards = [c for c in soup.select("div.s-result-item[data-asin]") if c.get("data-asin")]
    return cards


def _is_captcha(html):
    return "validateCaptcha" in html or "api-services-support@amazon.com" in html


def parse_results(html):
    """Turn Amazon search-page HTML into (products_with_price, products_without_price)."""
    if _is_captcha(html):
        raise ScraperError(
            "Amazon showed a robot-check (CAPTCHA) page and blocked the search. "
            "Wait a few minutes and try again."
        )
    soup = BeautifulSoup(html, "lxml")
    priced, unpriced = [], []
    for card in _find_cards(soup):
        name = _extract_name(card)
        if not name:
            continue
        image_tag = card.select_one("img.s-image")
        item = {
            "asin": card.get("data-asin", ""),
            "product_name": name,
            "product_price": _extract_price(card),
            "global_rating": _extract_rating(card),
            "retailer_name": RETAILER_NAME,
            "retailer_email": RETAILER_EMAIL,
            "image_url": image_tag.get("src") if image_tag else None,
        }
        (priced if item["product_price"] else unpriced).append(item)
    return priced, unpriced


def _detail_price(html):
    """Read the price from a single product page."""
    if _is_captcha(html):
        return None
    soup = BeautifulSoup(html, "lxml")
    for selector in ("#corePrice_feature_div span.a-offscreen",
                     "#corePriceDisplay_desktop_feature_div span.a-offscreen",
                     "span.a-price span.a-offscreen", "#priceblock_ourprice",
                     "#priceblock_dealprice", "#price_inside_buy"):
        tag = soup.select_one(selector)
        if tag and tag.get_text(strip=True):
            price = _parse_price(tag.get_text())
            if price:
                return price
    return None


# ----------------------------------------------------------------- network helpers
def _new_session(user_agent):
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["User-Agent"] = user_agent
    try:                                              # visit the home page first (gets cookies)
        session.get(HOME_URL, timeout=10)
    except requests.exceptions.RequestException:
        pass
    return session


def _fetch_page(session, term, page):
    params = {"k": term}
    if page > 1:
        params.update(page=page, ref=f"sr_pg_{page}")
    try:
        response = session.get(SEARCH_URL, params=params, timeout=15)
    except requests.exceptions.Timeout:
        raise ScraperError("The connection to Amazon timed out. Please try again.")
    except requests.exceptions.RequestException:
        raise ScraperError("Could not connect to Amazon. Check your internet connection.")
    if response.status_code != 200:
        raise ScraperError(
            f"Amazon refused the request (HTTP {response.status_code}). "
            "It may be blocking automated searches."
        )
    return response.text


def _prices_from_detail_pages(session, unpriced):
    """Open individual product pages to read prices the search page left out."""
    found = []
    for item in unpriced[:DETAIL_LOOKUPS]:
        if not item["asin"]:
            continue
        try:
            response = session.get(DETAIL_URL.format(asin=item["asin"]), timeout=15)
        except requests.exceptions.RequestException:
            continue
        if response.status_code == 200:
            price = _detail_price(response.text)
            if price:
                item["product_price"] = price
                found.append(item)
        time.sleep(1.0)
    log(f"product pages gave {len(found)} prices out of {min(len(unpriced), DETAIL_LOOKUPS)} tried")
    return found


def _more_pages(session, term, products):
    seen = {p["asin"] or p["product_name"] for p in products}
    for page in range(2, SEARCH_PAGES + 1):
        if len(products) >= MAX_RESULTS:
            break
        time.sleep(1.2)                               # be polite, avoid being blocked
        try:
            priced, _ = parse_results(_fetch_page(session, term, page))
        except ScraperError:
            break                                     # keep what we already have
        if not priced:
            break
        for item in priced:
            key = item["asin"] or item["product_name"]
            if key not in seen:
                seen.add(key)
                products.append(item)
    return products


# ----------------------------------------------------------------- public API
def search_amazon(term):
    """Remote search on amazon.com. Raises ScraperError if no priced products can be found."""
    last_error, last_html = None, None
    for attempt, agent in enumerate(USER_AGENTS[:SEARCH_ATTEMPTS], start=1):
        is_last = attempt == min(SEARCH_ATTEMPTS, len(USER_AGENTS))
        session = _new_session(agent)
        try:
            html = _fetch_page(session, term, 1)
            last_html = html
            priced, unpriced = parse_results(html)
            log(f'attempt {attempt}: "{term}" -> {len(priced)} with price, {len(unpriced)} without')
            if not priced and unpriced and is_last:
                priced = _prices_from_detail_pages(session, unpriced)
            if priced:
                return _more_pages(session, term, priced)[:MAX_RESULTS]
            last_error = ScraperError(
                f'Amazon sent {len(unpriced)} products for "{term}" but no prices.')
        except ScraperError as exc:
            last_error = exc
            log(f"attempt {attempt} failed: {exc}")
        if not is_last:
            time.sleep(1.5)

    if last_html:
        DEBUG_FILE.write_text(last_html, encoding="utf-8")
    raise ScraperError(
        f"{last_error} Amazon gave no usable prices after {SEARCH_ATTEMPTS} tries. "
        "Wait a few minutes or try a more specific search such as a brand name.")


def sample_products(term):
    """Clearly labelled example products, used only when Amazon returns no prices."""
    rng = random.Random(term.lower())
    kinds = ["Basic", "Standard", "Plus", "Pro", "Premium", "Deluxe", "Mini", "Max"]
    products = []
    for index, kind in enumerate(kinds, start=1):
        products.append({
            "asin": "",
            "product_name": f"{term.title()} {kind} (sample item {index})",
            "product_price": round(rng.uniform(8, 250), 2),
            "global_rating": round(rng.uniform(3.6, 4.8), 1),
            "retailer_name": RETAILER_NAME,
            "retailer_email": RETAILER_EMAIL,
            "image_url": None,
            "sample": True,
        })
    return products