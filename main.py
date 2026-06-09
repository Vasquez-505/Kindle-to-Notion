import logging
import os
import pathlib
import sys

from dotenv import load_dotenv

from kindle_sync import __version__, notion as notion_module, sync, watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HASHES_FILE = pathlib.Path(__file__).parent / "synced_hashes.json"


def _load_config() -> tuple[str, str]:
    load_dotenv()
    token = os.environ.get("NOTION_TOKEN", "").strip()
    database_id = os.environ.get("NOTION_BOOKS_DB_ID", "").strip()
    if not token:
        sys.exit("ERROR: NOTION_TOKEN is missing from .env")
    if not database_id:
        sys.exit("ERROR: NOTION_BOOKS_DB_ID is missing from .env")
    return token, database_id


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--watch"
    token, database_id = _load_config()

    log.info(f"Kindle → Notion Second Brain  v{__version__}")

    if mode == "--reset":
        log.info("Mode: Reset — deleting all Book pages from Notion")
        client = notion_module.get_notion_client(token)
        count = notion_module.delete_all_book_pages(client, database_id)
        log.info(f"Moved {count} page(s) to trash.")
        if HASHES_FILE.exists():
            HASHES_FILE.unlink()
            log.info("Cleared synced_hashes.json")
        log.info("Reset complete. Run '--cloud' or '--file' to re-sync.")

    elif mode == "--cloud":
        log.info("Mode: Cloud sync (read.amazon.com)")
        summary = sync.run_sync_from_cloud(token, database_id, HASHES_FILE)
        _print_summary(summary)

    elif mode == "--file":
        if len(sys.argv) < 3:
            sys.exit("Usage: python main.py --file path/to/My Clippings.txt")
        clippings = pathlib.Path(sys.argv[2])
        if not clippings.exists():
            sys.exit(f"ERROR: File not found: {clippings}")
        log.info(f"Mode: File sync ({clippings})")
        summary = sync.run_sync_from_file(clippings, token, database_id, HASHES_FILE)
        _print_summary(summary)

    elif mode == "--export-highlights":
        log.info("Mode: Export highlights from Notion to local markdown files")
        count = sync.export_all_highlights_from_notion(token, database_id)
        log.info(f"Exported {count} book(s) to book_highlights/")

    elif mode == "--update-covers":
        log.info("Mode: Update covers — fetching high-res images for all books")
        client = notion_module.get_notion_client(token)
        updated, skipped = notion_module.update_all_book_covers(client, database_id)
        log.info("─" * 50)
        log.info(f"Covers upgraded : {updated}")
        log.info(f"Skipped         : {skipped}")
        log.info("─" * 50)

    elif mode == "--discover-collections":
        log.info("Mode: Discover Amazon collections")
        from kindle_sync import cloud_scraper
        mapping = cloud_scraper.discover_collections()
        if mapping:
            log.info("─" * 50)
            log.info("Collection → Books mapping:")
            for collection, titles in mapping.items():
                log.info(f"\n  [{collection}]")
                for t in titles:
                    log.info(f"    - {t}")
        else:
            log.info("No collections found. Check the browser window for clues.")

    elif mode == "--watch":
        log.info("Mode: USB watch (plug in your Kindle to sync)")
        watcher.watch(token, database_id, HASHES_FILE)

    else:
        sys.exit(
            f"ERROR: Unknown mode {mode!r}. "
            "Valid: --cloud --file --watch --reset --update-covers --export-highlights --discover-collections"
        )


def _print_summary(summary: dict) -> None:
    log.info("─" * 50)
    log.info(f"Total parsed       : {summary['total_parsed']}")
    log.info(f"New highlights     : {summary['new']}")
    log.info(f"Already synced     : {summary['skipped_duplicates']}")
    log.info(f"Books updated      : {', '.join(summary['books_updated']) or 'none'}")
    if summary["errors"]:
        for e in summary["errors"]:
            log.error(f"Error: {e}")
    log.info("─" * 50)


if __name__ == "__main__":
    main()
