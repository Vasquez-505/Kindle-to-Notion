import datetime
import logging
import time
from typing import Iterator

from notion_client import Client
from notion_client.errors import APIResponseError, RequestTimeoutError

log = logging.getLogger(__name__)

MAX_TEXT_LEN = 1997
CHUNK_SIZE = 30   # 3 blocks per highlight (quote + 2 spacers) → 90 blocks/call, under API limit of 100
COVER_UPDATE_DELAY = 0.34

_ds_cache: dict[str, str] = {}


def get_notion_client(token: str) -> Client:
    return Client(auth=token)


def _get_ds_id(client: Client, database_id: str) -> str:
    """Return the data_source_id for a database (cached after first call)."""
    if database_id not in _ds_cache:
        db = client.databases.retrieve(database_id=database_id)
        ds_list = db.get("data_sources", [])
        _ds_cache[database_id] = ds_list[0]["id"] if ds_list else database_id
    return _ds_cache[database_id]


def iter_data_source_pages(client: Client, ds_id: str) -> Iterator[dict]:
    """Yield every non-trashed, non-archived page from a Notion data source."""
    cursor = None
    while True:
        kwargs = {"data_source_id": ds_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.data_sources.query(**kwargs)
        for page in result.get("results", []):
            if page.get("in_trash") or page.get("archived"):
                continue
            yield page
        if not result.get("has_more"):
            return
        cursor = result.get("next_cursor")


def _page_type(page: dict) -> str:
    return (
        page.get("properties", {})
        .get("Type", {})
        .get("select", {})
        .get("name", "")
    )


def _page_author(page: dict) -> str:
    parts = page.get("properties", {}).get("Author", {}).get("rich_text", [])
    return parts[0].get("plain_text", "") if parts else ""


def find_or_create_book_page(
    client: Client,
    database_id: str,
    title: str,
    author: str,
    cover_url: str | None = None,
    category: str = "Book",
    theme: str | None = None,
) -> str:
    ds_id = _get_ds_id(client, database_id)

    for page in iter_data_source_pages(client, ds_id):
        if _page_title(page) != title:
            continue
        page_id = page["id"]
        # Always update author and cover with fresh data from Amazon
        update_kwargs: dict = {
            "properties": {
                "Author": {"rich_text": [{"type": "text", "text": {"content": author}}]},
            }
        }
        if cover_url:
            update_kwargs["cover"] = {"type": "external", "external": {"url": cover_url}}
            update_kwargs["icon"] = {"type": "external", "external": {"url": cover_url}}
        client.pages.update(page_id=page_id, **update_kwargs)
        return page_id

    # Create new page
    icon = (
        {"type": "external", "external": {"url": cover_url}}
        if cover_url
        else {"type": "emoji", "emoji": "📚"}
    )
    create_kwargs = {
        "parent": {"type": "data_source_id", "data_source_id": ds_id},
        "icon": icon,
        "properties": {
            "Title": {"title": [{"type": "text", "text": {"content": title}}]},
            "Author": {"rich_text": [{"type": "text", "text": {"content": author}}]},
            "Highlight Count": {"number": 0},
            "Last Synced": {"date": {"start": datetime.date.today().isoformat()}},
            "Type": {"select": {"name": category}},
        },
    }
    if cover_url:
        create_kwargs["cover"] = {"type": "external", "external": {"url": cover_url}}
        create_kwargs["properties"]["Cover"] = {"url": cover_url}
    if theme:
        create_kwargs["properties"]["Theme"] = {"multi_select": [{"name": theme}]}

    page = client.pages.create(**create_kwargs)
    page_id = page["id"]

    _add_page_header(client, page_id)
    return page_id


def append_highlights_to_page(
    client: Client,
    page_id: str,
    highlights: list[dict],
) -> int:
    total = 0
    for i in range(0, len(highlights), CHUNK_SIZE):
        chunk = highlights[i : i + CHUNK_SIZE]
        blocks = []
        for h in chunk:
            blocks.extend(_make_highlight_blocks(h))
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": []}})
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": []}})
        client.blocks.children.append(block_id=page_id, children=blocks)
        total += len(chunk)
    return total


def update_book_properties(
    client: Client,
    page_id: str,
    new_count: int,
    cover_url: str | None = None,
) -> None:
    page = client.pages.retrieve(page_id=page_id)
    existing = page.get("properties", {}).get("Highlight Count", {}).get("number") or 0
    props = {
        "Highlight Count": {"number": existing + new_count},
        "Last Synced": {"date": {"start": datetime.date.today().isoformat()}},
    }
    if cover_url:
        props["Cover"] = {"url": cover_url}
    client.pages.update(page_id=page_id, properties=props)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_page_header(client: Client, page_id: str) -> None:
    client.blocks.children.append(
        block_id=page_id,
        children=[
            {"type": "divider", "divider": {}},
            {
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "Highlights"}}
                    ],
                    "color": "default",
                    "is_toggleable": False,
                },
            },
        ],
    )


def delete_all_book_pages(client: Client, database_id: str) -> int:
    """Move only Type=Book pages to trash. Leaves Category Hub pages untouched."""
    ds_id = _get_ds_id(client, database_id)
    count = 0
    for page in iter_data_source_pages(client, ds_id):
        if _page_type(page) != "Book":
            continue
        client.pages.update(page_id=page["id"], in_trash=True)
        count += 1
    return count


def update_all_book_covers(client: Client, database_id: str) -> tuple[int, int]:
    """
    Upgrade cover images for all Book pages in Notion.
    Returns (updated_count, skipped_count).
    Tries: (1) strip Amazon size codes from existing URL, (2) Google Books API.
    """
    from kindle_sync.cloud_scraper import fetch_best_cover

    ds_id = _get_ds_id(client, database_id)
    updated = skipped = 0

    for page in iter_data_source_pages(client, ds_id):
        if _page_type(page) != "Book":
            continue

        title = _page_title(page)
        author = _page_author(page)
        current_cover = (
            page.get("cover", {}).get("external", {}).get("url")
            or page.get("properties", {}).get("Cover", {}).get("url")
        )

        new_cover = fetch_best_cover(title, author, current_cover)
        if not new_cover or new_cover == current_cover:
            skipped += 1
            log.info(f"  '{title}': no improvement found, skipped.")
            continue

        try:
            client.pages.update(
                page_id=page["id"],
                cover={"type": "external", "external": {"url": new_cover}},
                icon={"type": "external", "external": {"url": new_cover}},
                properties={"Cover": {"url": new_cover}},
            )
            updated += 1
            log.info(f"  '{title}': cover upgraded.")
        except (APIResponseError, RequestTimeoutError) as e:
            log.warning(f"  '{title}': update failed — {e}")
            skipped += 1
        time.sleep(COVER_UPDATE_DELAY)

    return updated, skipped


def _page_title(page: dict) -> str:
    try:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                parts = prop.get("title", [])
                return "".join(p.get("plain_text", "") for p in parts)
    except Exception:
        pass
    return ""


def _make_highlight_blocks(highlight: dict) -> list[dict]:
    """Returns [quote block] for a single highlight."""
    prefix = "[Note] " if highlight.get("kind") == "Note" else ""
    text = highlight["text"]
    limit = MAX_TEXT_LEN - len(prefix)
    if len(text) > limit:
        text = text[:limit] + "..."

    meta_parts = []
    page = highlight.get("page")
    if page:
        meta_parts.append(f"p.{page}")
    if highlight.get("location"):
        meta_parts.append(f"loc. {highlight['location']}")
    if highlight.get("date"):
        meta_parts.append(highlight["date"])

    quote_rich_text = [{"type": "text", "text": {"content": prefix + text}}]
    if meta_parts:
        quote_rich_text.append({
            "type": "text",
            "text": {"content": "\n" + "  ·  ".join(meta_parts)},
            "annotations": {"italic": True, "color": "gray"},
        })

    return [{
        "object": "block",
        "type": "quote",
        "quote": {"rich_text": quote_rich_text, "color": "default"},
    }]
