"""
details_spider.py
=================
Reads **pending** event URLs from the SQLite `event_urls` table and scrapes
the full event details from each Bandsintown event page.

Extracted fields
----------------
  event_id, url, event_name, artist_name,
  datetime, venue_name, venue_location, venue_latitude, venue_longitude,
  description, cost, availability,
  promoter, category (derived), target_demographic (derived)

Extraction strategy (in priority order)
----------------------------------------
1. JSON-LD  <script type="application/ld+json">  (most reliable)
2. __NEXT_DATA__  embedded Next.js JSON           (fallback)
3. HTML element selectors                         (last resort)

Usage
-----
    scrapy crawl details
    scrapy crawl details -a batch=50
"""
import json
import re

import scrapy

from bandsintown_scraper.db import get_pending_urls, mark_url_scraped, mark_url_failed
from bandsintown_scraper.items import EventItem

# ── Derived field helpers ──────────────────────────────────────────────────────

_CATEGORY_KEYWORDS = {
    "Festival":  ["festival", "fest", "carnival"],
    "Sports":    ["sport", "game", "match", "race", "championship", "tournament"],
    "Comedy":    ["comedy", "stand-up", "standup", "comedian", "open mic"],
    "Arts":      ["art", "exhibition", "gallery", "theatre", "theater", "dance", "ballet", "opera"],
    "Music":     ["concert", "live", "show", "gig", "performance", "tour", "dj set", "rave", "nightclub"],
}

_FAMILY_FRIENDLY = "Family Friendly"
_DEMOGRAPHIC_PATTERNS = [
    (re.compile(r"\b21\s*\+", re.I),            "21+"),
    (re.compile(r"\b18\s*\+", re.I),            "18+"),
    (re.compile(r"\ball\s*ages?\b", re.I),      "All Ages"),
    (re.compile(r"\bfamily\s*friendly\b", re.I), _FAMILY_FRIENDLY),
    (re.compile(r"\bfamily\b", re.I),           _FAMILY_FRIENDLY),
    (re.compile(r"\bkids?\b", re.I),            _FAMILY_FRIENDLY),
]

_SCHEMA_AVAILABILITY = {
    "instock":             "Available",
    "soldout":             "Sold Out",
    "preorder":            "Pre-Sale",
    "preorderd":           "Pre-Sale",
    "limitedavailability": "Limited",
    "discontinued":        "Sold Out",
}


def _derive_category(text: str) -> str:
    """Infer event category from event name / description keywords."""
    lower = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "Music"   # default for music events


def _derive_demographic(text: str) -> str:
    """Infer target demographic from age-restriction / audience keywords."""
    for pattern, label in _DEMOGRAPHIC_PATTERNS:
        if pattern.search(text):
            return label
    return "General"


def _parse_availability(raw: str) -> str:
    """Normalise a schema.org availability URL or plain string."""
    if not raw:
        return "Unknown"
    key = raw.rstrip("/").split("/")[-1].lower().replace(" ", "")
    return _SCHEMA_AVAILABILITY.get(key, raw.split("/")[-1])


def _format_cost(offers: list) -> str:
    """Build a human-readable cost string from a list of offer dicts."""
    prices = []
    currency = ""
    for o in offers:
        price = o.get("price") or o.get("minPrice")
        if price:
            try:
                prices.append(float(str(price).replace(",", "")))
            except ValueError:
                pass
        if not currency:
            currency = o.get("priceCurrency", "")

    if not prices:
        return "Free" if any(
            str(o.get("price", "")).strip() in ("0", "0.0", "free", "Free")
            for o in offers
        ) else "Unknown"

    lo, hi = min(prices), max(prices)
    prefix = f"{currency} " if currency else ""
    if lo == 0 and hi == 0:
        return "Free"
    if lo == hi:
        return f"{prefix}{lo:.2f}"
    return f"{prefix}{lo:.2f} – {hi:.2f}"


# ─────────────────────────────────────────────────────────────────────────────

class DetailsSpider(scrapy.Spider):
    name = "details"

    def __init__(self, batch: int = 100, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch = int(batch)

    # ── entry point ───────────────────────────────────────────────────────────

    async def start(self):
        urls = get_pending_urls(self.batch)
        if not urls:
            self.logger.warning(
                "No pending URLs found in the database. "
                "Run the listing spider first:  scrapy crawl listing"
            )
            return

        self.logger.info(f"Details spider starting with {len(urls)} pending URLs.")

        for url in urls:
            yield scrapy.Request(
                url=url,
                callback=self.parse,
                errback=self.errback,
                meta={"event_url": url},
            )

    # ── main parse ────────────────────────────────────────────────────────────

    def parse(self, response):
        url = response.meta["event_url"]
        try:
            item = self._extract(response.text, url)
            if item:
                mark_url_scraped(url)
                yield item
            else:
                self.logger.warning(f"Extraction returned nothing for: {url}")
                mark_url_failed(url)
        except Exception as exc:
            self.logger.error(f"Extraction error for {url}: {exc}")
            mark_url_failed(url)

    # ── extraction orchestrator ───────────────────────────────────────────────

    def _extract(self, html: str, url: str) -> EventItem | None:
        return (
            self._extract_from_jsonld(html, url)
            or self._extract_from_next_data(html, url)
            or self._extract_from_html(html, url)
        )

    # ── Strategy 1: JSON-LD ───────────────────────────────────────────────────

    def _extract_from_jsonld(self, html: str, url: str) -> EventItem | None:
        blocks = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        )
        for raw in blocks:
            try:
                data = json.loads(raw.strip())
            except json.JSONDecodeError:
                continue
            records = data if isinstance(data, list) else [data]
            for rec in records:
                if rec.get("@type") not in ("MusicEvent", "Event"):
                    continue
                return self._map_jsonld(rec, url)
        return None

    def _map_jsonld(self, rec: dict, url: str) -> EventItem:
        event_id   = self._event_id_from_url(url)
        event_name = rec.get("name", "")

        # artist
        performer = rec.get("performer") or {}
        if isinstance(performer, list):
            performer = performer[0] if performer else {}
        artist_name = performer.get("name") or event_name

        # datetime
        dt = rec.get("startDate") or rec.get("endDate") or ""

        # venue / address
        location   = rec.get("location") or {}
        venue_name = location.get("name", "")
        address    = location.get("address") or {}
        if isinstance(address, str):
            venue_location = address
        else:
            venue_location = ", ".join(filter(None, [
                address.get("streetAddress", ""),
                address.get("addressLocality", ""),
                address.get("addressRegion", ""),
                address.get("addressCountry", ""),
            ]))
        geo       = location.get("geo") or {}
        venue_lat = self._to_float(geo.get("latitude"))
        venue_lon = self._to_float(geo.get("longitude"))

        # description
        description = rec.get("description", "")

        # promoter / organiser
        organizer = rec.get("organizer") or {}
        if isinstance(organizer, list):
            organizer = organizer[0] if organizer else {}
        promoter = organizer.get("name", "")

        # offers → cost / availability
        raw_offers = rec.get("offers") or []
        if isinstance(raw_offers, dict):
            raw_offers = [raw_offers]
        cost         = _format_cost(raw_offers)
        availability = _parse_availability(
            next((o.get("availability", "") for o in raw_offers), "")
        )

        # derived
        combined = f"{event_name} {description}"
        category           = _derive_category(combined)
        target_demographic = _derive_demographic(description)

        return EventItem(
            event_id=event_id, url=url,
            event_name=event_name, artist_name=artist_name,
            datetime=dt, description=description,
            venue_name=venue_name, venue_location=venue_location,
            venue_latitude=venue_lat, venue_longitude=venue_lon,
            cost=cost, availability=availability,
            promoter=promoter,
            category=category, target_demographic=target_demographic,
        )

    # ── Strategy 2: __NEXT_DATA__ ─────────────────────────────────────────────

    def _extract_from_next_data(self, html: str, url: str) -> EventItem | None:
        match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

        event = self._find_event_in_dict(data)
        if not event:
            return None
        return self._map_next_event(event, url)

    def _find_event_in_dict(self, d, depth: int = 0) -> dict | None:
        if depth > 10 or not isinstance(d, (dict, list)):
            return None
        if isinstance(d, dict):
            return self._search_event_dict(d, depth)
        return self._search_event_list(d, depth)

    def _search_event_dict(self, d: dict, depth: int) -> dict | None:
        if ("lineup" in d or "venue" in d) and "datetime" in d:
            return d
        for v in d.values():
            found = self._find_event_in_dict(v, depth + 1)
            if found:
                return found
        return None

    def _search_event_list(self, lst: list, depth: int) -> dict | None:
        for item in lst:
            found = self._find_event_in_dict(item, depth + 1)
            if found:
                return found
        return None

    def _map_next_event(self, ev: dict, url: str) -> EventItem:
        event_id   = str(ev.get("id") or self._event_id_from_url(url))
        event_name = ev.get("title") or ev.get("name") or ""

        artist      = ev.get("artist") or {}
        artist_name = artist.get("name") or event_name

        dt = ev.get("datetime") or ev.get("start_datetime") or ""

        venue  = ev.get("venue") or {}
        v_name = venue.get("name", "")
        v_loc  = ", ".join(filter(None, [
            venue.get("city", ""), venue.get("region", ""), venue.get("country", ""),
        ]))
        v_lat = self._to_float(venue.get("latitude"))
        v_lon = self._to_float(venue.get("longitude"))

        description = ev.get("description") or ""
        promoter    = ev.get("promoter") or ev.get("sponsor") or \
                      (ev.get("organizer") or {}).get("name", "")

        raw_offers = ev.get("offers") or []
        if isinstance(raw_offers, dict):
            raw_offers = [raw_offers]
        cost         = _format_cost(raw_offers)
        availability = _parse_availability(
            next((o.get("availability", "") for o in raw_offers), "")
        )

        combined = f"{event_name} {description}"
        return EventItem(
            event_id=event_id, url=url,
            event_name=event_name, artist_name=artist_name,
            datetime=dt, description=description,
            venue_name=v_name, venue_location=v_loc,
            venue_latitude=v_lat, venue_longitude=v_lon,
            cost=cost, availability=availability,
            promoter=str(promoter) if promoter else "",
            category=_derive_category(combined),
            target_demographic=_derive_demographic(description),
        )

    # ── Strategy 3: HTML selectors ────────────────────────────────────────────

    def _extract_from_html(self, html: str, url: str) -> EventItem | None:
        sel      = scrapy.Selector(text=html)
        event_id = self._event_id_from_url(url)

        event_name = (
            sel.css("[data-testid='event-title']::text").get()
            or sel.css("h1::text").get()
            or ""
        ).strip()

        artist_name = (
            sel.css("[data-testid='artist-name']::text").get()
            or sel.css(".artist-name::text").get()
            or event_name
        ).strip()

        dt = (
            sel.css("time::attr(datetime)").get()
            or sel.css("[data-testid='event-datetime']::text").get()
            or ""
        ).strip()

        venue_name = (
            sel.css("[data-testid='venue-name']::text").get()
            or sel.css(".venue-name::text").get()
            or sel.css("address span:first-child::text").get()
            or ""
        ).strip()

        venue_location = (
            sel.css("[data-testid='venue-location']::text").get()
            or sel.css(".venue-location::text").get()
            or " ".join(sel.css("address::text").getall()).strip()
            or ""
        )

        description = (
            sel.css("[data-testid='event-description']::text").get()
            or sel.css(".event-description::text").get()
            or ""
        ).strip()

        promoter = (
            sel.css("[data-testid='promoter']::text").get()
            or sel.css("[data-testid='sponsor']::text").get()
            or sel.css(".promoter::text").get()
            or ""
        ).strip()

        # cost from meta tags (og:price or similar)
        cost = (
            sel.css("meta[property='og:price:amount']::attr(content)").get()
            or sel.css("meta[name='price']::attr(content)").get()
            or ("Free" if sel.css("[data-testid='free-event']").get() else "Unknown")
        )

        availability = _parse_availability(
            sel.css("[data-testid='availability']::text").get() or ""
        )

        if not (artist_name or venue_name or dt):
            return None

        combined = f"{event_name} {description}"
        return EventItem(
            event_id=event_id, url=url,
            event_name=event_name, artist_name=artist_name,
            datetime=dt, description=description,
            venue_name=venue_name, venue_location=venue_location,
            venue_latitude=None, venue_longitude=None,
            cost=cost, availability=availability,
            promoter=promoter,
            category=_derive_category(combined),
            target_demographic=_derive_demographic(description),
        )

    # ── utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _event_id_from_url(url: str) -> str:
        m = re.search(r"/e/(\d+)", url)
        return m.group(1) if m else ""

    @staticmethod
    def _to_float(value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ── error handler ─────────────────────────────────────────────────────────

    def errback(self, failure):
        url = failure.request.meta.get("event_url", failure.request.url)
        mark_url_failed(url)
        self.logger.error(f"Failed to fetch {url}: {failure.value}")
