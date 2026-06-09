import json
import logging
import pathlib
import re
import time

from notion_client.errors import APIResponseError, RequestTimeoutError

from kindle_sync import notion

log = logging.getLogger(__name__)

HIGHLIGHTS_DIR = pathlib.Path(__file__).parent.parent / "book_highlights"

RATE_LIMIT_DELAY = 0.34  # stays under Notion's ~3 req/sec average limit


def run_sync(
    highlights: list[dict],
    notion_token: str,
    database_id: str,
    hashes_file: pathlib.Path,
) -> dict:
    """
    Sync a list of highlight dicts to Notion.
    Accepts output from either parser.parse_clippings() or cloud_scraper.scrape_highlights().
    """
    summary = {
        "total_parsed": len(highlights),
        "new": 0,
        "skipped_duplicates": 0,
        "books_updated": [],
        "errors": [],
    }

    known_hashes = _load_hashes(hashes_file)
    new_highlights = [h for h in highlights if h["hash"] not in known_hashes]
    summary["skipped_duplicates"] = len(highlights) - len(new_highlights)

    if not new_highlights:
        log.info("No new highlights to sync.")
        return summary

    client = notion.get_notion_client(notion_token)
    grouped = _group_by_book(new_highlights)
    new_hashes: set[str] = set()

    for title, book_highlights in grouped.items():
        first = book_highlights[0]
        author = first.get("author", "")
        cover_url = first.get("cover_url")
        category = first.get("category", "Book")

        theme = first.get("theme") or None
        try:
            page_id = notion.find_or_create_book_page(
                client, database_id, title, author,
                cover_url=cover_url, category=category, theme=theme,
            )
            time.sleep(RATE_LIMIT_DELAY)

            count = notion.append_highlights_to_page(client, page_id, book_highlights)
            time.sleep(RATE_LIMIT_DELAY)

            try:
                notion.update_book_properties(client, page_id, count, cover_url=cover_url)
            except (APIResponseError, RequestTimeoutError) as e:
                log.warning(f"  '{title}': property update failed (non-fatal): {e}")
            time.sleep(RATE_LIMIT_DELAY)

            new_hashes.update(h["hash"] for h in book_highlights)
            summary["new"] += len(book_highlights)
            summary["books_updated"].append(title)
            # Save after each book so a crash mid-sync doesn't lose progress
            _save_hashes(hashes_file, known_hashes | new_hashes)
            _append_highlights_to_md(title, author, book_highlights)
            log.info(f"  '{title}': {len(book_highlights)} highlight(s) synced.")
        except (APIResponseError, RequestTimeoutError) as e:
            msg = f"Failed to sync '{title}': {e}"
            log.error(msg)
            summary["errors"].append(msg)

    return summary


def run_sync_from_file(
    clippings_path: pathlib.Path,
    notion_token: str,
    database_id: str,
    hashes_file: pathlib.Path,
) -> dict:
    from kindle_sync import parser
    highlights = parser.parse_clippings(clippings_path)
    return run_sync(highlights, notion_token, database_id, hashes_file)


def run_sync_from_cloud(
    notion_token: str,
    database_id: str,
    hashes_file: pathlib.Path,
) -> dict:
    from kindle_sync import cloud_scraper
    log.info("Starting cloud sync via read.amazon.com...")
    highlights = cloud_scraper.scrape_highlights()
    log.info(f"Scraped {len(highlights)} highlight(s) from Amazon.")
    return run_sync(highlights, notion_token, database_id, hashes_file)


def _load_hashes(path: pathlib.Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        return set()


def _save_hashes(path: pathlib.Path, hashes: set[str]) -> None:
    path.write_text(json.dumps(sorted(hashes), indent=2), encoding="utf-8")


def export_all_highlights_from_notion(notion_token: str, database_id: str) -> int:
    """
    One-time export: read every book page from Notion and write local .md files.
    Returns the number of books exported.
    """
    client = notion.get_notion_client(notion_token)
    ds_id = notion._get_ds_id(client, database_id)

    HIGHLIGHTS_DIR.mkdir(exist_ok=True)
    exported = 0

    for page in notion.iter_data_source_pages(client, ds_id):
        if notion._page_type(page) != "Book":
            continue

        title = notion._page_title(page)
        author = notion._page_author(page)

        highlights = _fetch_highlights_from_notion(client, page["id"])
        _write_highlights_md(title, author, highlights)
        log.info(f"  Exported '{title}': {len(highlights)} highlight(s).")
        exported += 1
        time.sleep(RATE_LIMIT_DELAY)

    return exported


def _fetch_highlights_from_notion(client, page_id: str) -> list[str]:
    """Return all highlight texts from a book page's quote blocks."""
    texts = []
    cursor = None
    while True:
        kwargs = {"block_id": page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.blocks.children.list(**kwargs)
        for block in result.get("results", []):
            if block.get("type") == "quote":
                parts = block["quote"].get("rich_text", [])
                text = "".join(p.get("plain_text", "") for p in parts)
                if text.strip():
                    texts.append(text.strip())
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return texts


def _md_header(title: str, author: str) -> list[str]:
    return [f"# {title}", f"**Author:** {author}", "", "## Highlights", ""]


def _append_highlights_to_md(title: str, author: str, highlights: list[dict]) -> None:
    """Append new highlights to the book's local .md file (creates it if missing)."""
    HIGHLIGHTS_DIR.mkdir(exist_ok=True)
    path = HIGHLIGHTS_DIR / (_slugify(title) + ".md")

    lines: list[str] = []
    if not path.exists():
        lines += _md_header(title, author)

    for h in highlights:
        meta_parts = []
        if h.get("page"):
            meta_parts.append(f"p.{h['page']}")
        if h.get("location"):
            meta_parts.append(f"loc. {h['location']}")
        lines.append(f"> {h['text']}")
        if meta_parts:
            lines.append(f"> *{'  ·  '.join(meta_parts)}*")
        lines.append("")

    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_highlights_md(title: str, author: str, highlight_texts: list[str]) -> None:
    """Write (overwrite) a book's .md file from a list of raw text strings."""
    HIGHLIGHTS_DIR.mkdir(exist_ok=True)
    path = HIGHLIGHTS_DIR / (_slugify(title) + ".md")
    lines = _md_header(title, author)
    for text in highlight_texts:
        lines.append(f"> {text}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s]+", "_", slug.strip())
    return slug[:80]


def _group_by_book(highlights: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for h in highlights:
        grouped.setdefault(h["title"], []).append(h)
    return grouped
