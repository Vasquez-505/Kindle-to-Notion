"""
Scrapes Kindle highlights from read.amazon.com/notebook.
Login happens once in a visible browser; the session is saved for future runs.
"""
import json
import logging
import pathlib
import re
import time
import urllib.parse
import urllib.request

from kindle_sync.utils import clean_author, clean_title, make_highlight_hash

log = logging.getLogger(__name__)

NOTEBOOK_URL = "https://read.amazon.com/notebook"
LIBRARY_URL = "https://read.amazon.com"
COOKIES_FILE = pathlib.Path(__file__).parent.parent / "amazon_cookies.json"

# Strips localized "By: " / "De: " / "Par: " prefixes from Amazon author text
_AUTHOR_PREFIX_RE = re.compile(
    r"^\s*(By|De|Por|Par|Von|Di|Da|door|Автор)\s*:\s*", re.IGNORECASE
)


def _load_saved_cookies(context, log_success: bool = False) -> None:
    """Restore Amazon session cookies into a Playwright context, if available."""
    if not COOKIES_FILE.exists():
        return
    try:
        context.add_cookies(json.loads(COOKIES_FILE.read_text(encoding="utf-8")))
        if log_success:
            log.info("Amazon session restored from saved cookies.")
    except Exception as e:
        log.warning(f"Could not load cookies: {e}")


def scrape_highlights() -> list[dict]:
    """
    Open Kindle Notebook, handle login if needed, scrape every book's highlights.
    Returns list of dicts compatible with parser.py output (plus cover_url key).
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(viewport={"width": 1400, "height": 900})

        _load_saved_cookies(context, log_success=True)

        page = context.new_page()

        log.info("Opening Kindle Notebook...")
        page.goto(NOTEBOOK_URL, wait_until="domcontentloaded", timeout=30000)
        _wait_idle(page)

        if "signin" in page.url or "ap/signin" in page.url:
            log.info("=" * 60)
            log.info("Please log in to Amazon in the browser window.")
            log.info("The sync resumes automatically after login.")
            log.info("=" * 60)
            page.wait_for_function(
                "() => window.location.hostname === 'read.amazon.com'",
                timeout=300000,
            )
            time.sleep(2)
            COOKIES_FILE.write_text(
                json.dumps(context.cookies()), encoding="utf-8"
            )
            log.info("Session saved — no login needed next time.")
            _wait_idle(page)

        # Scroll sidebar to trigger lazy-loading of all book covers
        _scroll_sidebar(page)

        # Collect book metadata (titles, authors, covers) from sidebar
        books = _collect_books(page)
        log.info(f"Found {len(books)} book(s) in your library.")

        for idx, book in enumerate(books):
            log.info(f"  [{idx + 1}/{len(books)}] {book.get('title', '?')}")
            try:
                # Re-query elements fresh each time to avoid stale handle errors
                elements = page.query_selector_all("div.kp-notebook-library-each-book")
                if idx >= len(elements):
                    log.warning(f"       Book element not found at index {idx}, skipping.")
                    continue
                elements[idx].click()
                _wait_idle(page, timeout=15000)
                time.sleep(2)
                highlights = _extract_highlights(page)

                # Use sidebar metadata (correctly scoped per book) as the source of truth
                title = book.get("title", "Unknown")
                author = book.get("author", "")
                cover_url = book.get("cover_url")
                for h in highlights:
                    h["title"] = title
                    h["author"] = author
                    h["cover_url"] = cover_url
                    h["hash"] = make_highlight_hash(title, h["location"], h["text"])

                results.extend(highlights)
                log.info(f"       → {len(highlights)} highlight(s)")
            except PWTimeout:
                log.warning("       Timed out loading book, skipping.")
            except Exception as e:
                log.error(f"       Error: {e}")

        browser.close()

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wait_idle(page, timeout: int = 20000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass


def _scroll_sidebar(page) -> None:
    """Scroll the sidebar book list to trigger lazy-loading of all cover images."""
    try:
        sidebar = page.query_selector(
            "#kp-notebook-library, .kp-notebook-library-list, [class*='library']"
        )
        if sidebar:
            page.evaluate("el => { el.scrollTop = el.scrollHeight; }", sidebar)
            time.sleep(1.5)
            page.evaluate("el => { el.scrollTop = 0; }", sidebar)
            time.sleep(0.5)
        else:
            # Fallback: scroll window
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


def _upgrade_amazon_cover_url(url: str) -> str:
    """Strip Amazon thumbnail size codes (e.g. ._SY160 or ._SY75_) to get the full-res image."""
    if not url or "media-amazon.com" not in url:
        return url
    # Matches ._SY160.jpg  OR  ._SY75_.jpg  OR  ._SX315_SY475_.jpg etc.
    return re.sub(r"\._[A-Za-z0-9_]+(?=\.(?:jpg|jpeg|png))", "", url)


def _fetch_google_books_cover(title: str, author: str) -> str | None:
    """Return a large cover URL from Google Books API (no key required)."""
    try:
        query = urllib.parse.urlencode({
            "q": f"intitle:{title} inauthor:{author}",
            "maxResults": "1",
            "fields": "items/volumeInfo/imageLinks",
        })
        req = urllib.request.Request(
            f"https://www.googleapis.com/books/v1/volumes?{query}",
            headers={"User-Agent": "KindleSync/1.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        links = (data.get("items") or [{}])[0].get("volumeInfo", {}).get("imageLinks", {})
        thumb = links.get("thumbnail") or links.get("smallThumbnail")
        if thumb:
            # Remove curl edge effect and upgrade zoom for larger image
            thumb = thumb.replace("&edge=curl", "").replace("zoom=1", "zoom=0")
            return thumb.replace("http://", "https://")
        return None
    except Exception:
        return None


def fetch_best_cover(title: str, author: str, current_url: str | None) -> str | None:
    """Return the best available cover URL: upgraded Amazon URL or Google Books fallback."""
    if current_url and "media-amazon.com" in current_url:
        upgraded = _upgrade_amazon_cover_url(current_url)
        if upgraded != current_url:
            return upgraded
    if not current_url:
        return _fetch_google_books_cover(title, author)
    return current_url


def _collect_books(page) -> list[dict]:
    books = []

    elements = page.query_selector_all("div.kp-notebook-library-each-book")

    for el in elements:
        title = _text(el, [
            "h2.kp-notebook-searchable",
            "h2",
            ".kp-notebook-metadata",
            "[class*='title']",
        ])

        raw_author = _text(el, [
            ".a-size-base.a-color-secondary",
            "[class*='author']",
        ])
        raw_author = _AUTHOR_PREFIX_RE.sub("", raw_author).strip()
        author = clean_author(raw_author)

        # Handle lazy-loaded images: try data-src before src
        cover_url = None
        img = el.query_selector("img")
        if img:
            src = img.get_attribute("data-src") or img.get_attribute("src") or ""
            if src and not src.startswith("data:"):
                cover_url = _upgrade_amazon_cover_url(src)

        books.append({
            "title": clean_title(title) if title else "Unknown Title",
            "author": author or "",
            "cover_url": cover_url,
        })

    return books


def _extract_highlights(page) -> list[dict]:
    highlights = []

    # Collect all highlight texts
    h_elements = []
    for sel in ["#highlight", ".kp-notebook-highlight", "[id^='highlight']"]:
        h_elements = page.query_selector_all(sel)
        if h_elements:
            break

    # Collect all location strings in DOM order — they appear 1:1 with highlights
    loc_elements = []
    for sel in ["#kp-annotation-location", ".kp-annotation-location"]:
        loc_elements = page.query_selector_all(sel)
        if loc_elements:
            break

    # Extract location text, also checking hidden input values
    loc_texts: list[str] = []
    for loc_el in loc_elements:
        try:
            text = loc_el.inner_text().strip()
            if not text:
                # Some Amazon versions use a hidden input with a value attribute
                text = loc_el.evaluate(
                    "el => el.value || el.getAttribute('value') || ''"
                ) or ""
            loc_texts.append(text)
        except Exception:
            loc_texts.append("")

    for idx, h_el in enumerate(h_elements):
        text = h_el.inner_text().strip()
        if not text or len(text) < 5:
            continue

        location = loc_texts[idx] if idx < len(loc_texts) else ""

        highlights.append({
            # title/author/cover_url filled by caller from sidebar metadata
            "kind": "Highlight",
            "page": 0,
            "location": location,
            "date": "",
            "text": text,
            "hash": "",
        })

    return highlights


def _text(parent, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            el = parent.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                if t:
                    return t
        except Exception:
            pass
    return ""


def discover_collections() -> dict[str, list[str]]:
    """
    Open the Kindle library page and scrape collection names with their book titles.
    Returns {collection_name: [book_title, ...]} so you can build the THEME_MAP.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(viewport={"width": 1400, "height": 900})

        _load_saved_cookies(context)

        page = context.new_page()
        # Try the direct Amazon Collections list page
        collections_url = "https://www.amazon.com/hz/mycd/digital-console/contentlist/collections"
        page.goto(collections_url, wait_until="domcontentloaded", timeout=30000)
        _wait_idle(page, timeout=10000)
        time.sleep(3)

        if "signin" in page.url or "ap/signin" in page.url:
            log.info("Please log in to Amazon in the browser window.")
            page.wait_for_function(
                "() => window.location.hostname.includes('amazon')",
                timeout=300000,
            )
            _wait_idle(page)
            time.sleep(3)

        log.info(f"Collections page URL: {page.url}")
        page_text = page.evaluate("() => document.body.innerText")
        log.info("=== COLLECTIONS PAGE TEXT (first 2000 chars) ===\n" + page_text[:2000])

        results: dict[str, list[str]] = _scrape_mycd_collections(page)

        browser.close()

    return results


def scrape_book_collections() -> dict[str, str]:
    """
    Scrape Amazon MYCD to build {book_title: collection_name}.
    Called once during cloud sync to populate Theme on each book page.
    """
    from playwright.sync_api import sync_playwright

    mapping: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(viewport={"width": 1400, "height": 900})

        _load_saved_cookies(context)

        page = context.new_page()
        mycd_url = "https://www.amazon.com/hz/mycd/digital-console/contentlist/allcontent/dateDsc"
        page.goto(mycd_url, wait_until="domcontentloaded", timeout=30000)
        _wait_idle(page, timeout=12000)
        time.sleep(3)

        if "signin" in page.url or "ap/signin" in page.url:
            log.info("Please log in to Amazon in the browser window.")
            page.wait_for_function(
                "() => window.location.hostname.includes('amazon')",
                timeout=300000,
            )
            _wait_idle(page)
            time.sleep(3)

        for link_info in _get_collection_links(page):
            name_short = link_info["name"]
            url = link_info["href"]
            log.info(f"  Scraping collection '{name_short}'...")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                _wait_idle(page, timeout=12000)
                time.sleep(3)
                full_name = _get_collection_page_name(page) or name_short
                for title in _extract_collection_titles(page):
                    mapping[title] = full_name
                    log.info(f"    '{title}' → '{full_name}'")
                page.go_back()
                _wait_idle(page, timeout=10000)
                time.sleep(2)
            except Exception as e:
                log.warning(f"  Error scraping '{name_short}': {e}")

        browser.close()

    return mapping


def _scrape_mycd_collections(page) -> dict[str, list[str]]:
    """Used only by --discover-collections. Returns {full_collection_name: [titles]}."""
    results: dict[str, list[str]] = {}
    for link_info in _get_collection_links(page):
        name_short = link_info["name"]
        url = link_info["href"]
        log.info(f"  Navigating to collection '{name_short}'...")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            _wait_idle(page, timeout=12000)
            time.sleep(3)
            full_name = _get_collection_page_name(page) or name_short
            titles = _extract_collection_titles(page)
            results[full_name] = titles
            log.info(f"  '{full_name}': {titles}")
            page.go_back()
            _wait_idle(page, timeout=10000)
            time.sleep(2)
        except Exception as e:
            log.warning(f"  Error scraping '{name_short}': {e}")
    return results


def _get_collection_links(page) -> list[dict]:
    """Return [{name, href}] for each collection in the MYCD sidebar."""
    return page.evaluate("""() => {
        const results = [];
        const links = Array.from(document.querySelectorAll('a[href*="collectionContent"]'));
        for (const link of links) {
            const href = link.href;
            let container = link.parentElement;
            for (let i = 0; i < 6; i++) {
                if (!container) break;
                const leaves = Array.from(container.querySelectorAll('*'))
                    .filter(el => el.children.length === 0 && el !== link)
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length > 1 && t.length < 80 && !/^Ver \\d|^\\d+$/.test(t));
                if (leaves.length > 0) {
                    results.push({ name: leaves[leaves.length - 1], href });
                    break;
                }
                container = container.parentElement;
            }
        }
        return results;
    }""") or []


def _get_collection_page_name(page) -> str:
    """
    Read the full collection name from the browser page title on the collection content page.
    Amazon sets the document title to something like 'US History - Kindle Content'.
    """
    try:
        title = page.title()
        if title and " - " in title:
            name = title.split(" - ")[0].strip()
            if 2 < len(name) < 80:
                return name
        # Also try: look for the selected facet or breadcrumb text
        for sel in [
            "[class*='selectedFacet']",
            "[class*='breadcrumb']",
            "[class*='collection-name']",
            "[class*='contentListTitle']",
        ]:
            els = page.query_selector_all(sel)
            for el in els:
                t = el.inner_text().strip()
                if 2 < len(t) < 80:
                    return t
    except Exception:
        pass
    return ""


def _extract_collection_titles(page) -> list[str]:
    """
    Extract book titles from a MYCD collection content page.
    Uses span[class*='title'] (same selectors that worked in testing) then filters
    out UI chrome by matching the repeating pattern:
      [noise...] → title → 'Coleção com este item' → 'Dispositivo...' → 'Mais ações' → title → ...
    """
    try:
        items = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll(
                'span[class*="title"], td[class*="title"], div[class*="title"]'
            ));
            return els.map(el => el.innerText.trim()).filter(t => t.length > 1);
        }""") or []

        if not items:
            return []

        # Find the index of "Conteúdo digital" (or similar anchor) after which real titles start
        anchor_idx = -1
        for i, item in enumerate(items):
            if re.search(r"conte.?do digital", item, re.IGNORECASE):
                anchor_idx = i
                break

        if anchor_idx == -1:
            anchor_idx = 0

        # After the anchor the pattern repeats every 4 items:
        # title, "Coleção com este item", "Dispositivo com este item", "Mais ações"
        # Extract only items at positions 0, 4, 8, ... after anchor
        titles = []
        post = items[anchor_idx + 1:]
        for i in range(0, len(post), 4):
            candidate = post[i]
            if re.search(
                r"cole|dispositivo|mais a|suas listas|sua conta|conte.?do|your account|account",
                candidate, re.IGNORECASE,
            ):
                continue
            if len(candidate) > 150:
                continue
            titles.append(clean_title(candidate))

        return list(dict.fromkeys(titles))
    except Exception as e:
        log.warning(f"  _extract_collection_titles error: {e}")
        return []
