# Bandsintown Scraper

A two-phase Scrapy project that collects concert/event data from Bandsintown for
four target US cities. Every HTTP request is made via **cloudscraper** (Cloudflare
bypass) routed through a **Webshare rotating proxy** to avoid IP blocks.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Target Cities](#target-cities)
- [Running the Spiders](#running-the-spiders)
- [Database Schema](#database-schema)
- [Module Reference](#module-reference)
  - [db.py](#dbpy)
  - [items.py](#itemspy)
  - [pipelines.py](#pipelinespy)
  - [middlewares.py](#middlewarespy)
  - [settings.py](#settingspy)
  - [spiders/listing_spider.py](#spiderslistingspiderpy)
  - [spiders/details_spider.py](#spidersdetailsspiderpy)
- [Data Flow](#data-flow)
- [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1 – listing spider                                           │
│                                                                     │
│  citySuggestions API  ──►  resolve city_id / lat / lon  (×4 cities) │
│         │                                                           │
│         ▼                                                           │
│  fetch-next/upcomingEvents API  ──►  ListingSpider                  │
│  (paginated JSON, page=1,2,3…)       │  extract /e/ event URLs      │
│                                      ▼                              │
│                               SQLitePipeline                        │
│                                      │                              │
│                                      ▼                              │
│                            event_urls table  (status = pending)     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2 – details spider                                           │
│                                                                     │
│  event_urls (pending) ──► DetailsSpider ──(cloudscraper+proxy)──►  │
│  bandsintown.com/e/<id>    │  extract full event data               │
│                            ▼                                        │
│                     SQLitePipeline                                  │
│                            │                                        │
│                            ▼                                        │
│                     events table  +  event_urls.status updated      │
└─────────────────────────────────────────────────────────────────────┘
```

**Request layer (every request):**
```
cloudscraper + Webshare proxy  ──► 200 OK  →  done
        │ fails (blocked / timeout)
        ▼
cloudscraper direct (no proxy)  ──► 200 OK  →  done
        │ fails
        ▼
Scrapy default downloader (retry middleware)
```

---

## Project Structure

```
bandsintown_scraper/
├── bandsintown_scraper/
│   ├── __init__.py
│   ├── db.py              # SQLite helpers (schema, CRUD, auto-migration)
│   ├── items.py           # Scrapy Item definitions
│   ├── middlewares.py     # cloudscraper + Webshare proxy middleware
│   ├── pipelines.py       # SQLite persistence pipeline
│   ├── settings.py        # Scrapy + proxy configuration
│   └── spiders/
│       ├── __init__.py
│       ├── listing_spider.py   # Phase 1: resolve cities → paginate events
│       └── details_spider.py   # Phase 2: scrape full event details
├── requirements.txt
├── scrapy.cfg
└── bandsintown.db          # Created automatically on first run
```

---

## Requirements

| Package | Version |
|---|---|
| Python | >= 3.10 |
| scrapy | >= 2.11.0 |
| cloudscraper | >= 1.2.71 |
| itemadapter | >= 0.7.0 |

---

## Installation

```bash
# 1. Clone / download the project
cd bandsintown_scraper

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

The SQLite database (`bandsintown.db`) is created automatically on the first run.
Existing databases are **auto-migrated** — new columns are added without data loss.

---

## Configuration

All settings live in [settings.py](bandsintown_scraper/settings.py).

### Webshare Proxy

```python
PROXY_HOST = "HOST"
PROXY_PORT = "PORT"
PROXY_USER = "USER"
PROXY_PASS = "PASS"
```

### Key Scrapy Settings

| Setting | Default | Description |
|---|---|---|
| `CONCURRENT_REQUESTS` | `4` | Global concurrency cap |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | `4` | Per-domain concurrency cap |
| `DOWNLOAD_DELAY` | `2` s | Base delay between requests |
| `RANDOMIZE_DOWNLOAD_DELAY` | `True` | Jitter delay (0.5–1.5×) |
| `DOWNLOAD_TIMEOUT` | `60` s | Per-request timeout |
| `RETRY_TIMES` | `3` | Retries on 429 / 5xx |
| `LOG_LEVEL` | `INFO` | Log verbosity |

To also export events as JSON, uncomment the `FEEDS` block in `settings.py`.

---

## Target Cities

Configured in `TARGET_CITIES` at the top of [listing_spider.py](bandsintown_scraper/spiders/listing_spider.py):

| City | State |
|---|---|
| Atlanta | GA |
| Austin | TX |
| Orlando | FL |
| Nashville | TN |

Add or remove entries from the list to change which cities are scraped.

---

## Running the Spiders

### Phase 1 — Collect event URLs

The listing spider resolves each city via the Bandsintown `citySuggestions` API,
then paginates through the `upcomingEvents` JSON API for each city in parallel.

```bash
# Scrape all pages for all 4 cities
scrapy crawl listing

# Stop after page 5 (per city)
scrapy crawl listing -a max_page=5

# Resume from page 3 (per city)
scrapy crawl listing -a start_page=3

# Resume from page 3 and stop at page 10
scrapy crawl listing -a start_page=3 -a max_page=10
```

After this runs, `event_urls` will contain rows with `status = 'pending'`.

### Phase 2 — Scrape event details

```bash
# Default: process up to 100 pending URLs
scrapy crawl details

# Larger or smaller batch
scrapy crawl details -a batch=50
```

Repeat Phase 2 until no pending URLs remain, or schedule it with cron.

---

## Database Schema

Database file: `bandsintown.db` in the project root.

### `event_urls` — URL work queue

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment row ID |
| `url` | TEXT UNIQUE | Full Bandsintown event URL |
| `status` | TEXT | `pending` / `scraped` / `failed` |
| `created_at` | TIMESTAMP | When the URL was first discovered |
| `scraped_at` | TIMESTAMP | When the URL was last processed |

### `events` — Full event records

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment row ID |
| `event_id` | TEXT UNIQUE | Numeric ID from the URL path |
| `event_name` | TEXT | Full event title |
| `artist_name` | TEXT | Headlining artist name |
| `artist_id` | TEXT | Artist slug or numeric ID |
| `datetime` | TEXT | ISO-8601 event start date/time |
| `venue_name` | TEXT | Venue name |
| `venue_location` | TEXT | Full address (street, city, region, country) |
| `venue_latitude` | REAL | Latitude (nullable) |
| `venue_longitude` | REAL | Longitude (nullable) |
| `description` | TEXT | Event description |
| `lineup` | TEXT | JSON array of performer name strings |
| `offers` | TEXT | JSON array of `{name, url, cost, availability}` |
| `cost` | TEXT | e.g. `"USD 25.00"`, `"USD 25.00 – 75.00"`, `"Free"` |
| `availability` | TEXT | `InStock` / `SoldOut` / `PreOrder` / `Unknown` |
| `promoter` | TEXT | Event sponsor / promoter name |
| `category` | TEXT | Derived: `Concert` / `Festival` / `Tour` / `Club Night` |
| `target_demographic` | TEXT | Derived: `All Ages` / `18+` / `21+` / `Family` / `General` |
| `url` | TEXT | Source event page URL |
| `scraped_at` | TIMESTAMP | When this row was last written |

> `lineup` and `offers` are stored as JSON strings — use `json.loads()` to parse them.

---

## Module Reference

### `db.py`

Centralised SQLite layer. All SQL lives here — no raw queries in spiders or pipelines.

| Function | Description |
|---|---|
| `get_connection()` | Opens a connection with `row_factory = sqlite3.Row` |
| `init_db()` | Creates tables + auto-migrates new columns on existing DBs |
| `insert_event_url(url)` | Inserts a URL; returns `True` if newly inserted |
| `get_pending_urls(batch)` | Returns up to `batch` pending URLs |
| `mark_url_scraped(url)` | Sets `status = 'scraped'` |
| `mark_url_failed(url)` | Sets `status = 'failed'` |
| `upsert_event(item)` | INSERT-or-UPDATE on `event_id` conflict |

---

### `items.py`

#### `EventURLItem`

| Field | Type | Description |
|---|---|---|
| `url` | `str` | Full event page URL |

#### `EventItem`

| Field | Type | Description |
|---|---|---|
| `event_id` | `str` | Numeric ID from URL |
| `url` | `str` | Source URL |
| `event_name` | `str` | Full event title |
| `artist_name` | `str` | Headlining artist |
| `artist_id` | `str` | Artist identifier |
| `datetime` | `str` | ISO-8601 start time |
| `venue_name` | `str` | Venue |
| `venue_location` | `str` | Address |
| `venue_latitude` | `float \| None` | Latitude |
| `venue_longitude` | `float \| None` | Longitude |
| `description` | `str` | Event description |
| `lineup` | `list[str]` | All performer names |
| `offers` | `list[dict]` | `{name, url, cost, availability}` |
| `cost` | `str` | Formatted ticket price or `"Free"` |
| `availability` | `str` | Ticket availability status |
| `promoter` | `str` | Sponsor / promoter name |
| `category` | `str` | Derived event category |
| `target_demographic` | `str` | Derived target audience |

---

### `pipelines.py`

`SQLitePipeline` — priority `300`.

- `open_spider` → calls `init_db()` (creates / migrates schema).
- `process_item` → routes by type:
  - `EventURLItem` → `insert_event_url()`
  - `EventItem` → `upsert_event()`

---

### `middlewares.py`

`BandsintownMiddleware` — priority `543`.

Every request runs in a thread via `deferToThread` (keeps the Twisted reactor free).

| Step | Method | Description |
|---|---|---|
| 1 | `cloudscraper + Webshare proxy` | Primary fetch via rotating proxy |
| 2 | `cloudscraper direct` | Fallback if proxy fails |
| 3 | Scrapy default downloader | Last resort — retry middleware kicks in |

Thread-local cloudscraper sessions are used so each worker thread has its own
`requests.Session` (Session is not thread-safe when shared).

---

### `settings.py`

| Group | Settings |
|---|---|
| Proxy | `PROXY_HOST/PORT/USER/PASS` |
| Concurrency | `CONCURRENT_REQUESTS`, `DOWNLOAD_DELAY`, `RANDOMIZE_DOWNLOAD_DELAY` |
| Retry | `RETRY_ENABLED`, `RETRY_TIMES`, `RETRY_HTTP_CODES` |
| Timeout | `DOWNLOAD_TIMEOUT` |
| Pipelines | `ITEM_PIPELINES` |
| Middleware | `DOWNLOADER_MIDDLEWARES` |
| HTTP | `ROBOTSTXT_OBEY`, `COOKIES_ENABLED`, `DEFAULT_REQUEST_HEADERS` |

---

### `spiders/listing_spider.py`

**Spider name:** `listing`

#### Flow

1. Calls `citySuggestions?string=<city>` for each of the 4 cities to resolve `city_id`, `latitude`, `longitude`.
2. Paginates `GET /all-dates/fetch-next/upcomingEvents?city_id=…&page=…&latitude=…&longitude=…` until empty or `max_page` reached.
3. Extracts event URLs from the JSON payload (falls back to HTML scan).

#### Spider arguments

| Argument | Default | Description |
|---|---|---|
| `start_page` | `1` | Page to begin from (useful for resuming) |
| `max_page` | `0` | Page to stop at — `0` means no limit |

#### URL extraction strategies

1. JSON payload scan — regex over the raw JSON text.
2. `<a href="/e/…">` anchors (single/double quoted, relative or absolute).
3. `__NEXT_DATA__` embedded Next.js blob.
4. Full raw HTML scan (catches `data-*`, `onclick`, JSON islands).

---

### `spiders/details_spider.py`

**Spider name:** `details`

Reads pending URLs from the DB, fetches each page, extracts full event data.

#### Spider arguments

| Argument | Default | Description |
|---|---|---|
| `batch` | `100` | Number of pending URLs to process per run |

#### Extraction strategies (tried in order)

| Priority | Strategy | Source |
|---|---|---|
| 1 (best) | **JSON-LD** | `<script type="application/ld+json">` — `@type: MusicEvent/Event` |
| 2 | **`__NEXT_DATA__`** | Next.js SSR blob — recursive search for event dict |
| 3 (fallback) | **HTML selectors** | `data-testid` attributes + semantic element fallbacks |

#### Derived fields

| Field | Logic |
|---|---|
| `category` | Keywords in event name/description: `festival`, `tour`, `concert`, `club night` → defaults to `Concert` |
| `target_demographic` | Age-restriction patterns: `21+`, `18+`, `all ages`, `family` → defaults to `General` |

#### URL status lifecycle

```
pending  ──(200 + data extracted)──►  scraped
         ──(network error)──────────►  failed
         ──(empty extraction)────────►  failed
```

Re-queue failed URLs:
```sql
UPDATE event_urls SET status = 'pending', scraped_at = NULL WHERE status = 'failed';
```

---

## Data Flow

```
ListingSpider
    │
    ├─ citySuggestions API × 4 cities  (parallel)
    │       └─ resolve city_id, lat, lon
    │
    ├─ upcomingEvents API  page 1 → 2 → … (per city)
    │       └─ extract /e/<id-slug> URLs
    │
    │  yields EventURLItem(url)
    ▼
SQLitePipeline  →  event_urls (status=pending)

DetailsSpider
    │
    ├─ get_pending_urls(batch)  ←  event_urls
    │
    ├─ cloudscraper + Webshare proxy  →  fetch page
    │
    ├─ _extract()  JSON-LD → __NEXT_DATA__ → HTML
    │
    │  yields EventItem(...)  +  mark_url_scraped(url)
    ▼
SQLitePipeline  →  events table
```

---

## Troubleshooting

**No pending URLs found**
Run `scrapy crawl listing` first to populate `event_urls`.

**IP still getting blocked**
Check Webshare proxy credentials in `settings.py`. Verify the proxy works:
```bash
curl -x http://bexjaely-rotate:avg8c609wr9t@p.webshare.io:80 https://ipinfo.io
```

**Page load timeouts**
Increase `DOWNLOAD_TIMEOUT` in `settings.py` or reduce `CONCURRENT_REQUESTS`.

**All events have empty fields**
The site markup may have changed. Inspect the JSON-LD and `__NEXT_DATA__` in browser
DevTools and update the extraction logic in `details_spider.py`.

**Re-queue failed URLs**
```sql
UPDATE event_urls SET status = 'pending', scraped_at = NULL WHERE status = 'failed';
```

**Inspect the database**
```bash
sqlite3 bandsintown.db
sqlite> SELECT status, count(*) FROM event_urls GROUP BY status;
sqlite> SELECT count(*) FROM events;
sqlite> SELECT event_id, event_name, artist_name, cost, availability, category FROM events LIMIT 10;
```

**Export to CSV**
```bash
sqlite3 -csv -header bandsintown.db "SELECT * FROM events;" > events.csv
```
