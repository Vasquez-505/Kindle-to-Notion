import pathlib
import re

from kindle_sync.utils import clean_author, clean_title, make_highlight_hash

SEPARATOR = "=========="
BOOK_RE = re.compile(r"^(?P<title>.+) \((?P<author>.+)\)$")
META_RE = re.compile(
    r"^- Your (?P<kind>Highlight|Note) on page (?P<page>\d+)"
    r" \| Location (?P<loc>\d+-\d+)"
    r" \| Added on (?P<date>.+)$"
)


def parse_clippings(file_path: pathlib.Path) -> list[dict]:
    text = file_path.read_text(encoding="utf-8-sig")
    blocks = text.split(SEPARATOR)
    results = []
    for block in blocks:
        parsed = _parse_block(block)
        if parsed:
            results.append(parsed)
    return results


def _parse_block(block_text: str) -> dict | None:
    lines = [l.strip() for l in block_text.strip().splitlines() if l.strip()]
    if len(lines) < 3:
        return None

    book_match = BOOK_RE.match(lines[0])
    meta_match = META_RE.match(lines[1]) if book_match else None
    if not book_match or not meta_match:
        return None

    text = " ".join(lines[2:])
    if not text:
        return None

    title = clean_title(book_match.group("title").strip())
    author = clean_author(book_match.group("author").strip())
    location = meta_match.group("loc")

    return {
        "title": title,
        "author": author,
        "kind": meta_match.group("kind"),
        "page": int(meta_match.group("page")),
        "location": location,
        "date": meta_match.group("date").strip(),
        "text": text,
        "hash": make_highlight_hash(title, location, text),
    }
