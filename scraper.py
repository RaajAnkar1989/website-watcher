"""
Website Watcher
----------------
Reads config.json (list of "watches"), checks each URL, and writes
docs/results.json which the dashboard (docs/index.html) reads.

For each watch we:
  1. Load the page (either as plain HTML, or with a real headless
     browser if the site needs JavaScript to render content).
  2. Search the visible text for a spot where ALL of the watch's
     keywords appear close together (e.g. "Morley" near "Stage 1").
  3. Look at the words right around that spot to decide if it looks
     AVAILABLE, UNAVAILABLE, or UNCLEAR.

This is a best-effort, generic approach so it can work across many
different websites without needing site-specific code. It won't be
perfect for every site on day one -- the dashboard shows the raw
matched text snippet for each watch so keywords can be tuned.
"""

import json
import re
import itertools
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright

CONFIG_PATH = "config.json"
RESULTS_PATH = "docs/results.json"
HISTORY_DIR = Path("docs/history")

DEFAULT_AVAILABLE_WORDS = ["book now", "available", "spaces", "book"]
DEFAULT_UNAVAILABLE_WORDS = ["full", "waiting list", "sold out", "no spaces"]

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Listing pages often show many courses in one view — match per-course blocks first.
COURSE_BLOCK_RE = re.compile(
    r"((?:Learn2Swim|Swimming Assessment)\s*-\s*"
    r"(?:(?!Learn2Swim\s*-|Swimming Assessment\s*-).){5,260}?"
    r"(?:Book this course|book now).*?"
    r"Spaces left:\s*\d+\s*out of\s*\d+)",
    re.I | re.DOTALL,
)

ZOOPLA_LISTING_RE = re.compile(
    r"£([\d,]+)(?:\s+Guide price)?\s+See monthly cost\s+"
    r"(\d+)\s+beds?\s+(\d+)\s+baths?\s+(\d+)\s+receptions?\s+"
    r"(.{10,160}?LS\d{1,2}[A-Z]?\b[^£]{0,60})",
    re.I,
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_text_simple(url):
    """Plain HTTP fetch for ordinary (non-JS) websites."""
    resp = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 (WebsiteWatcher)"})
    resp.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def action_link(page_url, highlight_text):
    """Scroll/highlight target on page using browser text-fragment URLs."""
    base = page_url.split("#")[0]
    return f"{base}#:~:text={quote(highlight_text.strip(), safe='')}"


def launch_browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=BROWSER_USER_AGENT,
        viewport={"width": 1440, "height": 900},
        locale="en-GB",
    )
    return browser, context


def load_browser_page(context, url, wait_ms=8000):
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page.goto(url, timeout=90000, wait_until="domcontentloaded")
    page.wait_for_timeout(wait_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    text = re.sub(r"\s+", " ", page.inner_text("body")).strip()
    return page, text


def fetch_text_browser(url, wait_ms=8000):
    """Render the page with a headless browser tuned for modern listing sites."""
    with sync_playwright() as p:
        browser, context = launch_browser_context(p)
        page, text = load_browser_page(context, url, wait_ms)
        browser.close()
    return text


def is_blocked_page(text):
    blocked_markers = [
        "performing security verification",
        "verify you are human",
        "attention required",
        "cf-browser-verification",
        "checking your browser",
    ]
    text_l = text.lower()
    if len(text) < 600 and any(m in text_l for m in blocked_markers):
        return True
    return False


def listing_fingerprint(listing):
    return f"{listing['price']}|{listing['beds']}|{listing['address'][:100].lower()}"


def load_listing_history(watch_id):
    path = HISTORY_DIR / f"{watch_id}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_listing_history(watch_id, fingerprints):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{watch_id}.json"
    path.write_text(json.dumps(fingerprints, indent=2) + "\n", encoding="utf-8")


def parse_zoopla_listings(text, watch, page=None):
    if page is not None:
        dom_listings = extract_zoopla_listings_from_page(page, watch)
        if dom_listings:
            return dom_listings

    filters = watch.get("filters") or {}
    beds_min = filters.get("beds_min")
    price_max = filters.get("price_max")
    keywords = [k.lower() for k in watch.get("keywords_all") or []]

    listings = []
    seen = set()
    for match in ZOOPLA_LISTING_RE.finditer(text):
        price = int(match.group(1).replace(",", ""))
        beds = int(match.group(2))
        baths = int(match.group(3))
        receptions = int(match.group(4))
        address = re.sub(r"\s+", " ", match.group(5)).strip()
        address = re.split(r"\b(Freehold|Leasehold|Chain free|Auction|Call Email)\b", address)[0].strip()

        if beds_min and beds < beds_min:
            continue
        if price_max and price > price_max:
            continue

        blob = f"{address} {price}".lower()
        if keywords and not all(k in blob for k in keywords):
            continue

        listing = {
            "price": price,
            "price_display": f"£{price:,}",
            "beds": beds,
            "baths": baths,
            "receptions": receptions,
            "address": address,
        }
        fp = listing_fingerprint(listing)
        if fp in seen:
            continue
        seen.add(fp)
        listings.append(listing)

    listings.sort(key=lambda x: x["price"])
    return listings


def extract_zoopla_listings_from_page(page, watch):
    filters = watch.get("filters") or {}
    beds_min = filters.get("beds_min")
    price_max = filters.get("price_max")
    keywords = [k.lower() for k in watch.get("keywords_all") or []]

    raw = page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();
          document.querySelectorAll('a[href*="/for-sale/details/"]').forEach((a) => {
            const href = a.href || '';
            if (!/\\/for-sale\\/details\\/\\d+\\/?$/.test(href) || seen.has(href)) return;
            const card = a.closest('[data-testid], article, li, div') || a.parentElement;
            const text = (card ? card.innerText : a.innerText).replace(/\\s+/g, ' ').trim();
            if (text.length < 20) return;
            seen.add(href);
            out.push({ url: href.split('?')[0], text });
          });
          return out;
        }"""
    )

    listings = []
    seen = set()
    for item in raw:
        text = item["text"]
        price_m = re.search(r"£([\d,]+)", text)
        beds_m = re.search(r"(\d+)\s+beds?", text, re.I)
        baths_m = re.search(r"(\d+)\s+baths?", text, re.I)
        receptions_m = re.search(r"(\d+)\s+receptions?", text, re.I)
        if not price_m or not beds_m:
            continue

        price = int(price_m.group(1).replace(",", ""))
        beds = int(beds_m.group(1))
        baths = int(baths_m.group(1)) if baths_m else 0
        receptions = int(receptions_m.group(1)) if receptions_m else 0

        address = text
        if price_m:
            address = text[price_m.end() :].strip()
        address = re.split(
            r"\b(Freehold|Leasehold|Chain free|Auction|Call|Email|See monthly cost)\b",
            address,
            maxsplit=1,
        )[0].strip()
        address = re.sub(r"^Guide price\s*", "", address, flags=re.I).strip()
        if len(address) > 160:
            address = address[:160].strip()

        if beds_min and beds < beds_min:
            continue
        if price_max and price > price_max:
            continue
        blob = text.lower()
        if keywords and not all(k in blob for k in keywords):
            continue

        listing = {
            "price": price,
            "price_display": f"£{price:,}",
            "beds": beds,
            "baths": baths,
            "receptions": receptions,
            "address": address,
            "url": item["url"],
            "action_url": item["url"],
            "action_label": "View property",
        }
        fp = listing_fingerprint(listing)
        if fp in seen:
            continue
        seen.add(fp)
        listings.append(listing)

    listings.sort(key=lambda x: x["price"])
    return listings


def check_listings(watch, text, page=None):
    if is_blocked_page(text):
        return {
            "matched": False,
            "status": "error",
            "error": "Site blocked the automated check (Cloudflare / bot protection). Try again later or run locally.",
            "reason": "Bot protection page returned instead of listings.",
        }

    site = watch.get("listings_site", "zoopla")
    if site == "zoopla":
        listings = parse_zoopla_listings(text, watch, page=page)
    else:
        listings = parse_zoopla_listings(text, watch, page=page)

    if not listings:
        return {
            "matched": False,
            "status": "unknown",
            "reason": "No property listings parsed. Page layout may have changed or filters returned zero results.",
            "listing_count": 0,
            "listings": [],
            "new_listings": [],
        }

    fps = [listing_fingerprint(item) for item in listings]
    previous = set(load_listing_history(watch["id"]))
    new_items = [item for item, fp in zip(listings, fps) if fp not in previous]
    save_listing_history(watch["id"], fps)

    top = listings[:8]
    summary = "; ".join(
        f"{item['price_display']} · {item['beds']} bed · {item['address'][:70]}"
        for item in top
    )
    snippet = summary + (" …" if len(listings) > 8 else "")

    result = {
        "matched": True,
        "status": "available",
        "listing_count": len(listings),
        "listings": top,
        "new_listings": new_items[:10],
        "new_count": len(new_items),
        "snippet": snippet,
        "signals": {"available": [f"{len(listings)} listings"] , "unavailable": []},
    }
    if new_items:
        result["course_hint"] = f"{len(new_items)} new since last check"
    else:
        result["course_hint"] = f"{len(listings)} listings (none new since last check)"
    if listings:
        top_url = listings[0].get("action_url")
        if top_url:
            result["action_url"] = top_url
            result["action_label"] = listings[0].get("action_label", "View property")
    return result


def split_course_blocks(text):
    """Split a course listing page into one chunk per course row."""
    blocks = [
        re.sub(r"\s+", " ", m.group(1)).strip()
        for m in COURSE_BLOCK_RE.finditer(text)
    ]
    return blocks if blocks else None


def keywords_match_block(block, keywords_all):
    block_l = block.lower()
    return all(kw.lower() in block_l for kw in keywords_all)


def pick_best_block(blocks, keywords_all):
    """Prefer the block whose title best matches the longest keyword phrase."""
    matching = [b for b in blocks if keywords_match_block(b, keywords_all)]
    if not matching:
        return None
    if len(matching) == 1:
        return matching[0]

    def score(block):
        block_l = block.lower()
        best = 0
        for kw in keywords_all:
            kw_l = kw.lower()
            if kw_l not in block_l:
                return -1
            # Exact stage number beats partial ("stage 1" must not win via "stage 10")
            stage = re.search(r"stage\s*(\d+)", kw_l)
            if stage:
                block_stage = re.search(r"stage\s*(\d+)", block_l)
                if block_stage and stage.group(1) == block_stage.group(1):
                    best += 100 + len(kw)
                else:
                    best += len(kw)
            else:
                best += len(kw)
        return best

    matching.sort(key=score, reverse=True)
    return matching[0]


def classify_snippet(snippet, available_words, unavailable_words, keyword_spread=None, watch_url=None):
    snippet_l = snippet.lower()

    found_avail = [w for w in available_words if w.lower() in snippet_l]
    found_unavail = [w for w in unavailable_words if w.lower() in snippet_l]
    has_avail = bool(found_avail)
    has_unavail = bool(found_unavail)

    if has_avail and not has_unavail:
        status = "available"
    elif has_unavail and not has_avail:
        status = "unavailable"
    elif has_avail and has_unavail:
        status = "unclear"
    else:
        status = "unknown"

    result = {
        "matched": True,
        "status": status,
        "snippet": snippet,
        "signals": {"available": found_avail, "unavailable": found_unavail},
    }
    if keyword_spread is not None:
        result["keyword_spread"] = keyword_spread

    spaces_match = re.search(r"spaces left:\s*(\d+)\s*out of\s*(\d+)", snippet_l)
    if spaces_match:
        result["spaces"] = {
            "left": int(spaces_match.group(1)),
            "total": int(spaces_match.group(2)),
        }

    course_match = re.search(
        r"((?:Learn2Swim|Swimming Assessment)\s*-\s*[^.]{5,120})",
        snippet,
        re.I,
    )
    if course_match:
        title = course_match.group(1).strip()
        result["course_hint"] = title
        if watch_url and result.get("status") == "available":
            result["action_url"] = action_link(watch_url, title)
            result["action_label"] = "Book this course"

    return result


def find_status(text, keywords_all, available_words, unavailable_words, window=700, watch_url=None):
    """
    Find a place in `text` where all keywords appear close together, then
    classify availability. For multi-course listing pages, match one course
    block at a time so Stage 1 doesn't pick up Stage 4's spaces count.
    """
    blocks = split_course_blocks(text)
    if blocks:
        block = pick_best_block(blocks, keywords_all)
        if block:
            return classify_snippet(
                block, available_words, unavailable_words, keyword_spread=0, watch_url=watch_url
            )
        missing = [
            kw for kw in keywords_all
            if not any(kw.lower() in b.lower() for b in blocks)
        ]
        if missing:
            return {
                "matched": False,
                "reason": f'Keyword "{missing[0]}" not found in any course listing on the page.',
            }
        return {
            "matched": False,
            "reason": "Keywords found on page, but not together in the same course listing.",
        }

    text_l = text.lower()

    positions_per_keyword = []
    for kw in keywords_all:
        positions = [m.start() for m in re.finditer(re.escape(kw.lower()), text_l)]
        if not positions:
            return {"matched": False, "reason": f'Keyword "{kw}" not found anywhere on the page.'}
        positions_per_keyword.append(positions)

    # To avoid combinatorial blowup, cap how many positions we consider per keyword
    positions_per_keyword = [p[:25] for p in positions_per_keyword]

    best_combo = None
    best_spread = None
    for combo in itertools.product(*positions_per_keyword):
        spread = max(combo) - min(combo)
        if spread <= window and (best_spread is None or spread < best_spread):
            best_spread = spread
            best_combo = combo

    if best_combo is None:
        return {"matched": False, "reason": "Keywords found, but not close together anywhere on the page."}

    lo = max(0, min(best_combo) - 250)
    hi = min(len(text), max(best_combo) + window + 250)
    snippet = text[lo:hi].strip()

    return classify_snippet(
        snippet,
        available_words,
        unavailable_words,
        keyword_spread=best_spread,
        watch_url=watch_url,
    )


def check_watch_browser(watch, url):
    with sync_playwright() as p:
        browser, context = launch_browser_context(p)
        page, text = load_browser_page(context, url)

        if is_blocked_page(text):
            browser.close()
            return {
                "matched": False,
                "status": "error",
                "error": "Site blocked the automated check (Cloudflare / bot protection).",
                "reason": "Bot protection page returned instead of content.",
            }

        if watch.get("type") == "listings":
            result = check_listings(watch, text, page=page)
        else:
            result = find_status(
                text,
                watch.get("keywords_all", []),
                watch.get("available_words") or DEFAULT_AVAILABLE_WORDS,
                watch.get("unavailable_words") or DEFAULT_UNAVAILABLE_WORDS,
                watch_url=url,
            )

        browser.close()
        return result


def check_watch(watch):
    mode = watch.get("mode", "simple")
    url = watch["url"]

    if mode == "browser":
        return check_watch_browser(watch, url)

    text = fetch_text_simple(url)

    if is_blocked_page(text):
        return {
            "matched": False,
            "status": "error",
            "error": "Site blocked the automated check (Cloudflare / bot protection).",
            "reason": "Bot protection page returned instead of content.",
        }

    if watch.get("type") == "listings":
        return check_listings(watch, text)

    return find_status(
        text,
        watch.get("keywords_all", []),
        watch.get("available_words") or DEFAULT_AVAILABLE_WORDS,
        watch.get("unavailable_words") or DEFAULT_UNAVAILABLE_WORDS,
        watch_url=url,
    )


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    output = {"last_run": now_iso(), "watches": []}

    for watch in config.get("watches", []):
        entry = {
            "id": watch["id"],
            "name": watch.get("name", watch["id"]),
            "url": watch["url"],
            "checked_at": now_iso(),
        }
        try:
            entry.update(check_watch(watch))
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
            entry["traceback"] = traceback.format_exc()[-1500:]
        output["watches"].append(entry)

    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Checked {len(output['watches'])} watch(es). Wrote {RESULTS_PATH}.")


if __name__ == "__main__":
    main()
