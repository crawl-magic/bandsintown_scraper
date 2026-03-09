"""
SQLite pipeline.

• EventURLItem  → writes to event_urls table (listing spider output)
• EventItem     → writes to events table   (details spider output)
"""
import logging

from itemadapter import ItemAdapter

from bandsintown_scraper.db import init_db, insert_event_url, upsert_event
from bandsintown_scraper.items import EventItem, EventURLItem

logger = logging.getLogger(__name__)


class SQLitePipeline:
    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def open_spider(self, spider=None):
        init_db()
        logger.info("SQLite database initialised.")

    def process_item(self, item, spider=None):
        adapter = ItemAdapter(item)

        if isinstance(item, EventURLItem):
            url = adapter.get("url")
            inserted = insert_event_url(url)
            if inserted:
                logger.debug(f"Stored new URL: {url}")
            else:
                logger.debug(f"URL already in DB (skipped): {url}")

        elif isinstance(item, EventItem):
            upsert_event(dict(adapter))
            logger.debug(f"Saved event: {adapter.get('event_id')} – {adapter.get('artist_name')}")

        return item
