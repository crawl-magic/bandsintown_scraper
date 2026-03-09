BOT_NAME = "bandsintown_scraper"

SPIDER_MODULES = ["bandsintown_scraper.spiders"]
NEWSPIDER_MODULE = "bandsintown_scraper.spiders"

# ── Webshare rotating proxy ───────────────────────────────────────────────────
PROXY_HOST = "YOUR_PROXY_HOST"
PROXY_PORT = "YOUR_PROXY_HOST"
PROXY_USER = "YOUR_PROXY_USER"
PROXY_PASS = "YOUR_PROXY_PASS"

# ── Concurrency ───────────────────────────────────────────────────────────────
CONCURRENT_REQUESTS = 4
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 2          # seconds between requests — be polite
RANDOMIZE_DOWNLOAD_DELAY = True

# ── Pipelines ─────────────────────────────────────────────────────────────────
ITEM_PIPELINES = {
    "bandsintown_scraper.pipelines.SQLitePipeline": 300,
}

# ── Middleware ────────────────────────────────────────────────────────────────
DOWNLOADER_MIDDLEWARES = {
    "bandsintown_scraper.middlewares.BandsintownMiddleware": 543,
}

# ── HTTP / Robot settings ─────────────────────────────────────────────────────
ROBOTSTXT_OBEY = False
COOKIES_ENABLED = True
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ── Retry ─────────────────────────────────────────────────────────────────────
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

# ── Timeouts ──────────────────────────────────────────────────────────────────
DOWNLOAD_TIMEOUT = 60

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"

# ── Feed exports (optional CSV / JSON side-output) ────────────────────────────
# Uncomment to also save a JSON file:
# FEEDS = {
#     "events_%(time)s.json": {"format": "json", "encoding": "utf-8"},
# }

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
