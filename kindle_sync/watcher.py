import logging
import pathlib
import time

import psutil

from kindle_sync import sync

log = logging.getLogger(__name__)

CLIPPINGS_RELATIVE = pathlib.Path("documents") / "My Clippings.txt"


def watch(
    notion_token: str,
    database_id: str,
    hashes_file: pathlib.Path,
    poll_interval: float = 2.0,
) -> None:
    known_drives = _get_current_drives()
    log.info("Watching for Kindle... Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(poll_interval)
            current = _get_current_drives()
            new_drives = current - known_drives
            for drive in new_drives:
                clippings = _find_clippings(drive)
                if clippings:
                    log.info(f"Kindle detected at {drive} — starting sync...")
                    summary = sync.run_sync_from_file(
                        clippings, notion_token, database_id, hashes_file
                    )
                    log.info(
                        f"Sync complete: {summary['new']} new highlight(s), "
                        f"{summary['skipped_duplicates']} already synced, "
                        f"books updated: {summary['books_updated'] or 'none'}"
                    )
                    if summary["errors"]:
                        for err in summary["errors"]:
                            log.error(f"  Error: {err}")
                else:
                    log.debug(f"New drive {drive!r} is not a Kindle — ignoring.")
            known_drives = current
    except KeyboardInterrupt:
        log.info("Stopped.")


def _get_current_drives() -> set[str]:
    return {p.device for p in psutil.disk_partitions(all=False)}


def _find_clippings(drive_device: str) -> pathlib.Path | None:
    # psutil returns e.g. "E:\\" on Windows
    drive_path = pathlib.Path(drive_device)
    clippings = drive_path / CLIPPINGS_RELATIVE
    return clippings if clippings.exists() else None
