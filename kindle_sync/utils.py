import hashlib
import re

_PAREN_RE = re.compile(r"\s*\([^)]*\)")

# Titles: strip subtitle after first ':' then remove any (...) content
def clean_title(title: str) -> str:
    if ":" in title:
        title = title[: title.index(":")]
    title = _PAREN_RE.sub("", title)
    return title.strip(" ,-")


# Authors: strip common role labels; if 3+ contributors, keep only the first.
# Two-name "X and Y" is kept as-is (covers genuine co-authors like Friedman & Friedman).
_ROLE_RE = re.compile(
    r",?\s*(Foreword|Introduction|Afterword|Preface|Translated|Translation|Editor|Edited)"
    r"\s+by\s+[^,]+",
    re.IGNORECASE,
)

def clean_author(raw: str) -> str:
    raw = _ROLE_RE.sub("", raw).strip(" ,")
    # Split on ", " separators (but not on "and" inside two-name strings)
    parts = [p.strip() for p in re.split(r",\s*", raw) if p.strip()]
    if len(parts) >= 4:
        # 4+ listed contributors almost always includes translators — keep only first
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return raw.strip()


def make_highlight_hash(title: str, location: str, text: str) -> str:
    """Stable identifier for a single highlight; used for cross-source deduplication."""
    return hashlib.md5(f"{title}|{location}|{text}".encode("utf-8")).hexdigest()
