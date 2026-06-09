# kindle-to-notion

Your Kindle highlights are more useful in Notion than on the device. This tool syncs them automatically, turning each book into a searchable Notion page of quote blocks you can annotate, link, and stack ideas on top of. Over time it becomes a second brain: a personal library of everything you've read, organized well enough to actually think with.

It scrapes directly from `read.amazon.com` or parses `My Clippings.txt` from a USB-connected Kindle. A watch mode detects when you plug in the device and syncs without any extra steps.

---

<!-- Add a screenshot of your Notion database with synced highlights here once set up -->
<!-- Example: ![Notion highlights page](screenshot.png) -->

---

## Features

- **Cloud sync**: scrapes `read.amazon.com/notebook` via Playwright; handles Amazon login once and saves the session for future runs
- **File sync**: parses `My Clippings.txt` from a USB Kindle or a local copy of the file
- **USB watch mode**: detects when a Kindle is plugged in and triggers a sync automatically
- **Deduplication**: each highlight gets an MD5 hash; `synced_hashes.json` tracks what's already uploaded so re-running is always safe
- **Cover art**: fetches book cover images from Amazon's CDN or Google Books API
- **Highlight export**: pulls all existing Notion book pages to local Markdown files in `book_highlights/`
- **Cover upgrade**: strips Amazon thumbnail size codes and queries Google Books to find full-resolution images
- **Collection discovery**: scrapes Amazon MYCD collections so you can map books to Notion themes

---

## Requirements

- Python 3.11 or later
- A Notion account with an integration token
- A Notion database with the properties described below
- For cloud sync: Chromium (installed automatically by Playwright)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Create a Notion integration

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **New integration**, give it a name (e.g. `kindle-sync`), and select your workspace
3. Copy the **Internal Integration Secret** (this is your `NOTION_TOKEN`)

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

To get the **Database ID**: open the database in your browser. The URL looks like `https://www.notion.so/workspace/3625da853eba8076bc94000b248ff011?v=...`. The 32-character hex string before the `?` is your `NOTION_BOOKS_DB_ID`.

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

Run all commands from the project root with `python main.py <mode>`.

### Cloud sync (recommended)

Scrapes all your highlights directly from `read.amazon.com`:

```bash
python main.py --cloud
```

A Chromium browser window opens on first run. Log in to Amazon and the scraper saves your session automatically. Subsequent runs skip the login.

### File sync

Parse a `My Clippings.txt` file from a connected Kindle or a local copy:

```bash
python main.py --file "E:\documents\My Clippings.txt"
python main.py --file "/path/to/My Clippings.txt"
```

### USB watch mode (default)

Plug in your Kindle and sync automatically:

```bash
python main.py --watch
# or simply:
python main.py
```

The script checks for new USB drives every two seconds. When it finds one with `documents/My Clippings.txt`, it runs a file sync and keeps watching. Press `Ctrl+C` to stop.

### Export highlights from Notion

Pull all highlights from existing Notion book pages to `book_highlights/<slug>.md`:

```bash
python main.py --export-highlights
```

### Upgrade book covers

Fetch higher-resolution cover images for every book in Notion:

```bash
python main.py --update-covers
```

Tries Amazon's full-resolution URL first, then falls back to Google Books API.

### Discover Amazon collections

Scrape your Amazon MYCD collections and print a mapping of collection names to book titles:

```bash
python main.py --discover-collections
```

Useful for building a theme map if you organize books into Amazon collections.

### Reset (destructive)

Move all `Type = Book` pages to Notion trash and clear the local hash store:

```bash
python main.py --reset
```

Run this before a full re-sync to rebuild the database from scratch. Category Hub and other non-Book pages are left untouched.

---

## How deduplication works

Each highlight has an MD5 hash of `title | location | text`. After a successful sync, hashes go into `synced_hashes.json`. On the next run, only highlights missing from that file get uploaded. Re-running against the same clippings file is always safe.

The file updates after each book, not at the end. If the sync crashes halfway through, the completed books stay recorded and won't be re-uploaded.

---

## Project structure

```
kindle-to-notion/
├── main.py                  # entry point, CLI mode dispatch
├── requirements.txt
├── .env.example
└── kindle_sync/
    ├── __init__.py          # package version
    ├── parser.py            # My Clippings.txt parser
    ├── cloud_scraper.py     # Playwright scraper for read.amazon.com
    ├── sync.py              # parsing, dedup, and Notion upload
    ├── notion.py            # Notion API calls (pages, blocks, covers)
    ├── watcher.py           # USB drive watcher (psutil)
    ├── utils.py             # clean_title / clean_author / make_highlight_hash
    └── ai_titles.py         # optional: AI-generated highlight titles via Gemini
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
