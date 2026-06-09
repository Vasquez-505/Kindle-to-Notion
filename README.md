# kindle-to-notion

Automatically sync your Kindle highlights to a Notion database. Supports two sync modes — scrape directly from Amazon's cloud reader, or parse the `My Clippings.txt` file from a USB-connected Kindle — and watches for Kindle plug-in events so syncing can be fully hands-free.

Highlights are pushed as formatted quote blocks inside individual book pages. Re-running the sync never creates duplicates; every highlight is identified by a stable hash so only genuinely new clips are uploaded.

---

## Features

- **Cloud sync** — scrapes `read.amazon.com/notebook` via Playwright; handles Amazon login once and saves the session for subsequent runs
- **File sync** — parses `My Clippings.txt` from a Kindle filesystem (USB or copied file)
- **USB watch mode** — polls for new drives; triggers file sync automatically when a Kindle is plugged in
- **Deduplication** — every highlight gets an MD5 hash; `synced_hashes.json` prevents re-uploading on repeated runs
- **Cover art** — fetches book cover images from Amazon's CDN or Google Books API and sets them on the Notion page
- **Highlight export** — reads all existing Notion book pages and writes them to local Markdown files (`book_highlights/`)
- **Cover upgrade** — strips Amazon thumbnail size codes and/or queries Google Books to upgrade all cover images to full resolution
- **Collection discovery** — scrapes your Amazon MYCD collections so you can map books to themes

---

## Requirements

- Python 3.11 or later
- A Notion account with an integration token
- A Notion database set up with the properties described below
- For cloud sync: a Chromium-compatible browser (installed automatically by Playwright)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Create a Notion integration

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **New integration**, give it a name (e.g. `kindle-sync`), select your workspace
3. Copy the **Internal Integration Secret** — this is your `NOTION_TOKEN`

### 3. Create and configure the Notion database

Create a full-page database in Notion with the following properties:

| Property | Type |
|---|---|
| Title | Title |
| Author | Text |
| Highlight Count | Number |
| Last Synced | Date |
| Type | Select |
| Cover | URL |
| Theme | Multi-select |

Open the database, click **Share** in the top-right, and invite your integration by name.

To get the **Database ID**: open the database in your browser; the URL looks like `https://www.notion.so/workspace/3625da853eba8076bc94000b248ff011?v=...`. The 32-character hex string before the `?` is your `NOTION_BOOKS_DB_ID`.

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```ini
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_BOOKS_DB_ID=3625da853eba8076bc94000b248ff011
GOOGLE_API_KEY=AIza...   # optional
```

---

## Usage

All commands are run from the project root with `python main.py <mode>`.

### Cloud sync (recommended)

Scrapes all your highlights directly from `read.amazon.com`:

```bash
python main.py --cloud
```

A Chromium browser window opens on first run. Log in to Amazon; the session cookie is saved automatically so subsequent runs skip the login.

### File sync

Parse a `My Clippings.txt` file from a connected Kindle or a copied file:

```bash
python main.py --file "E:\documents\My Clippings.txt"
python main.py --file "/path/to/My Clippings.txt"
```

### USB watch mode (default)

Wait for a Kindle to be plugged in and sync automatically:

```bash
python main.py --watch
# or simply:
python main.py
```

The script polls for new USB drives every two seconds. When it detects a drive containing `documents/My Clippings.txt`, it runs a file sync and then keeps watching.

Press `Ctrl+C` to stop.

### Export highlights from Notion

Pull all highlights from existing Notion book pages and write them to `book_highlights/<slug>.md`:

```bash
python main.py --export-highlights
```

### Upgrade book covers

Fetch higher-resolution cover images for every book already in Notion:

```bash
python main.py --update-covers
```

Tries to strip Amazon thumbnail size codes first; falls back to Google Books API.

### Discover Amazon collections

Scrape your Amazon MYCD collections and print a mapping of collection names to book titles:

```bash
python main.py --discover-collections
```

Useful for building a theme map when you organise books into Amazon collections.

### Reset (destructive)

Move all `Type = Book` pages to Notion trash and clear the local hash store:

```bash
python main.py --reset
```

Run this before a full re-sync if you want to rebuild the database from scratch. Category Hub and other non-Book pages are left untouched.

---

## How deduplication works

Every highlight is identified by an MD5 hash of `title | location | text`. Hashes for all successfully synced highlights are stored in `synced_hashes.json`. On each run, only highlights whose hash is not already in that file are sent to Notion — so re-running against the same clippings file is always safe.

Hashes are saved after each book, not at the end, so a crash mid-sync does not lose progress.

---

## Project structure

```
kindle-to-notion/
├── main.py                  # Entry point — CLI flags and mode dispatch
├── requirements.txt
├── .env.example
└── kindle_sync/
    ├── __init__.py          # Package version
    ├── parser.py            # My Clippings.txt parser
    ├── cloud_scraper.py     # Playwright scraper for read.amazon.com
    ├── sync.py              # Orchestrates parsing → dedup → Notion upload
    ├── notion.py            # All Notion API calls (pages, blocks, covers)
    ├── watcher.py           # USB drive watcher (psutil)
    ├── utils.py             # clean_title / clean_author / make_highlight_hash
    └── ai_titles.py         # Optional: generate highlight titles via Gemini
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `NOTION_TOKEN` | Yes | Notion integration secret |
| `NOTION_BOOKS_DB_ID` | Yes | 32-character Notion database ID |
| `GOOGLE_API_KEY` | No | Gemini API key for AI-generated highlight titles |

---

## License

MIT
