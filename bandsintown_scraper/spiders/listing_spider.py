"""
listing_spider.py
=================
Scrapes event listings for 4 target cities by:
  1. Calling the citySuggestions API to resolve city_id / lat / lon.
  2. Paginating through the upcomingEvents fetch-next JSON API.
  3. Storing every discovered /e/... URL in the SQLite `event_urls` table.

Target cities (hardcoded, scraped in parallel)
----------------------------------------------
  Atlanta, GA  |  Austin, TX  |  Orlando, FL  |  Nashville, TN

Usage
-----
    scrapy crawl listing                         # all pages, all cities
    scrapy crawl listing -a max_page=5           # stop after page 5
    scrapy crawl listing -a start_page=3         # resume from page 3
    scrapy crawl listing -a max_page=10 -a start_page=2
"""
import json
import re
from urllib.parse import urlencode, quote_plus

import scrapy

from bandsintown_scraper.items import EventURLItem


BASE_URL            = "https://www.bandsintown.com"
CITY_SUGGEST_URL    = BASE_URL + "/citySuggestions?string={query}"
EVENTS_FETCH_URL    = BASE_URL + "/all-dates/fetch-next/upcomingEvents"

EVENT_PATH_RE = re.compile(r"^/e/\d+")
EVENT_ID_RE   = re.compile(r"/e/(\d+[^\"'\s?#&,>)]*)")

# Cities to scrape — add / remove as needed
TARGET_CITIES = [
    "Atlanta, GA",
    "Austin, TX",
    "Orlando, FL",
    "Nashville, TN",
]


class ListingSpider(scrapy.Spider):
    name = "listing"

    def __init__(self, start_page: int = 1, max_page: int = 0,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_page = int(start_page)   # page to begin from (inclusive)
        self.max_page   = int(max_page)     # page to stop at (0 = no limit)

    # ── Phase 1: resolve each city ────────────────────────────────────────────

    async def start(self):
        for city in TARGET_CITIES:
            yield scrapy.Request(
                url=CITY_SUGGEST_URL.format(query=quote_plus(city)),
                callback=self.parse_city,
                headers={"Accept": "application/json"},
                meta={"city_label": city},
                dont_filter=True,
            )

    def parse_city(self, response):
        label = response.meta["city_label"]
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error(f"[{label}] citySuggestions returned non-JSON: "
                              f"{response.text[:200]}")
            return

        cities = data.get("cities") or []
        if not cities:
            self.logger.warning(f"[{label}] No city match found — skipping.")
            return

        city   = cities[0]
        city_id = city["id"]
        lat     = city["latitude"]
        lon     = city["longitude"]
        self.logger.info(
            f"[{label}] Resolved → id={city_id}, lat={lat}, lon={lon}"
        )
        yield self._make_events_request(city_id, lat, lon, self.start_page, label)

    # ── Phase 2: paginate events API ──────────────────────────────────────────

    def _make_events_request(self, city_id, lat, lon, page, label):
        params = urlencode({
            "city_id":   city_id,
            "page":      page,
            "latitude":  lat,
            "longitude": lon,
        })
        return scrapy.Request(
            url=f"{EVENTS_FETCH_URL}?{params}",
            callback=self.parse_events,
            headers={"Accept": "application/json, text/html, */*"},
            meta={
                "city_id":    city_id,
                "latitude":   lat,
                "longitude":  lon,
                "page":       page,
                "city_label": label,
            },
            dont_filter=True,
        )

    def parse_events(self, response):
        city_id = response.meta["city_id"]
        lat     = response.meta["latitude"]
        lon     = response.meta["longitude"]
        page    = response.meta["page"]
        label   = response.meta["city_label"]

        # The fetch-next endpoint returns JSON — fall back to HTML extraction
        urls      = set()
        has_more  = False

        try:
            data     = json.loads(response.text)
            urls     = self._urls_from_json(data)
            has_more = self._json_has_more(data, urls)
        except (json.JSONDecodeError, ValueError):
            urls     = self._extract_event_urls(response.text)
            has_more = bool(urls)   # if we got any URLs there may be more pages

        self.logger.info(
            f"[{label}] Page {page}: {len(urls)} event URLs "
            f"(has_more={has_more})"
        )

        for url in urls:
            yield EventURLItem(url=url)

        # Advance to next page if allowed
        at_limit = self.max_page and page >= self.max_page
        if has_more and not at_limit:
            yield self._make_events_request(city_id, lat, lon, page + 1, label)
        elif at_limit:
            self.logger.info(
                f"[{label}] Reached max_page={self.max_page} — stopping."
            )

    # ── JSON event extraction ─────────────────────────────────────────────────

    def _urls_from_json(self, data) -> set[str]:
        """Extract /e/... URLs from the fetch-next JSON payload."""
        urls = set()
        # Dump entire JSON as text and apply regex — works regardless of schema shape
        text = json.dumps(data)
        for slug in re.findall(r'/e/(\d+[^"\\,\s]+)', text):
            clean = slug.split("?")[0].split("\\")[0].rstrip("/")
            if clean:
                urls.add(f"{BASE_URL}/e/{clean}")
        return urls

    def _json_has_more(self, data, found_urls: set) -> bool:
        """
        Determine whether more pages exist.
        Checks common 'has_more' / 'total' / 'next_page' keys, then falls
        back to: if we found URLs this page, assume there might be more.
        """
        if isinstance(data, dict):
            # Explicit signal from API
            if "has_more" in data:
                return bool(data["has_more"])
            if "next_page" in data and data["next_page"]:
                return True
            # Empty events array → no more pages
            events = data.get("events") or data.get("data") or []
            if isinstance(events, list):
                return len(events) > 0
        return bool(found_urls)

    # ── HTML fallback extraction (same as before) ─────────────────────────────

    def _extract_event_urls(self, html: str) -> set[str]:
        urls: set[str] = set()
        urls.update(self._urls_from_hrefs(html))
        urls.update(self._urls_from_next_data(html))
        urls.update(self._urls_from_raw_scan(html))
        return urls

    def _urls_from_hrefs(self, html: str) -> set[str]:
        urls: set[str] = set()
        for href in re.findall(r'''href=["']([^"']+)["']''', html):
            if "bandsintown.com/e/" in href:
                m = EVENT_ID_RE.search(href)
                if m:
                    urls.add(f"{BASE_URL}/e/{m.group(1)}")
            elif EVENT_PATH_RE.match(href):
                urls.add(BASE_URL + href.split("?")[0].split("#")[0])
        return urls

    def _urls_from_next_data(self, html: str) -> set[str]:
        urls: set[str] = set()
        nd_match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if not nd_match:
            return urls
        try:
            text = json.dumps(json.loads(nd_match.group(1)))
            for slug in re.findall(r'/e/(\d+[^"\\]+)', text):
                clean = slug.split("?")[0].split("\\")[0].rstrip("/")
                if clean:
                    urls.add(f"{BASE_URL}/e/{clean}")
        except json.JSONDecodeError:
            pass
        return urls

    def _urls_from_raw_scan(self, html: str) -> set[str]:
        urls: set[str] = set()
        for slug in EVENT_ID_RE.findall(html):
            clean = slug.split("?")[0].rstrip("/")
            if clean:
                urls.add(f"{BASE_URL}/e/{clean}")
        return urls
