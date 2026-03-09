"""
Custom middleware for the Bandsintown scraper.

Every request is made with cloudscraper routed through the Webshare
rotating proxy to avoid IP bans.

Fetch strategy
--------------
1. cloudscraper + Webshare proxy  (primary)
2. cloudscraper without proxy      (fallback if proxy call fails)
"""
import logging
import random
import threading

import cloudscraper
from scrapy.http import HtmlResponse
from twisted.internet.threads import deferToThread


logger = logging.getLogger(__name__)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",

    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",

    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# Thread-local cloudscraper sessions (requests.Session is not thread-safe).
_local = threading.local()


def _get_scraper():
    if not hasattr(_local, "scraper"):
        _local.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    return _local.scraper


class BandsintownMiddleware:

    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url          # "" means no proxy configured
        self.proxies   = {"http": proxy_url, "https": proxy_url} if proxy_url else {}

    @classmethod
    def from_crawler(cls, crawler):
        s    = crawler.settings
        host = s.get("WEBSHARE_PROXY_HOST", "")
        port = s.getint("WEBSHARE_PROXY_PORT", 80)
        user = s.get("WEBSHARE_PROXY_USER", "")
        pwd  = s.get("WEBSHARE_PROXY_PASS", "")
        proxy_url = f"http://{user}:{pwd}@{host}:{port}" if host and user else ""
        return cls(proxy_url=proxy_url)

    # ── Scrapy hook ───────────────────────────────────────────────────────────

    def process_request(self, request, _spider=None):
        return deferToThread(self._fetch, request)

    # ── fetch (runs in worker thread) ─────────────────────────────────────────

    def _fetch(self, request):
        ua = random.choice(UA_POOL)

        # ── Primary: cloudscraper + Webshare proxy ─────────────────────────
        if self.proxies:
            result = self._try_fetch(request, ua, proxies=self.proxies,
                                     label="cloudscraper+proxy")
            if result is not None:
                return result
            logger.warning(
                f"[proxy] failed — retrying without proxy: {request.url}"
            )

        # ── Fallback: cloudscraper direct (no proxy) ───────────────────────
        result = self._try_fetch(request, ua, proxies=None, label="cloudscraper")
        if result is not None:
            return result

        # Both failed — let Scrapy's default downloader have a go
        return request

    def _try_fetch(self, request, ua: str, proxies, label: str):
        """
        Attempt a single GET via cloudscraper.
        Returns HtmlResponse on HTTP 200, None otherwise.
        """
        logger.info(f"[{label}] → {request.url}")
        try:
            kwargs = dict(timeout=30, headers={"User-Agent": ua})
            if proxies:
                kwargs["proxies"] = proxies

            resp = _get_scraper().get(request.url, **kwargs)

            if resp.status_code == 200:
                logger.info(f"[{label}] ✓ 200 OK — {request.url}")
                return HtmlResponse(
                    url=request.url,
                    body=resp.content,
                    encoding="utf-8",
                    request=request,
                )

            logger.warning(
                f"[{label}] ✗ HTTP {resp.status_code} — {request.url}"
            )
        except Exception as exc:
            logger.warning(
                f"[{label}] ✗ {exc.__class__.__name__}: {exc} — {request.url}"
            )
        return None
