"""
Centralised SQLite helpers.

Two tables:
  event_urls  – filled by the listing spider
  events      – filled by the details spider
"""
import sqlite3
import os

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bandsintown.db",
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS event_urls (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            url        TEXT    UNIQUE NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scraped_at TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id           TEXT UNIQUE,
            event_name         TEXT,
            artist_name        TEXT,
            datetime           TEXT,
            venue_name         TEXT,
            venue_location     TEXT,
            venue_latitude     REAL,
            venue_longitude    REAL,
            description        TEXT,
            cost               TEXT,
            availability       TEXT,
            promoter           TEXT,
            category           TEXT,
            target_demographic TEXT,
            url                TEXT,
            scraped_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ── listing spider helpers ────────────────────────────────────────────────────

def insert_event_url(url: str) -> bool:
    """Insert a URL if not already present. Returns True when inserted."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO event_urls (url) VALUES (?)", (url,)
        )
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


# ── details spider helpers ────────────────────────────────────────────────────

def get_pending_urls(batch: int = 100) -> list[str]:
    """Return up to *batch* URLs whose status is 'pending'."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT url FROM event_urls WHERE status = 'pending' LIMIT ?",
            (batch,),
        ).fetchall()
        return [r["url"] for r in rows]
    finally:
        conn.close()


def mark_url_scraped(url: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE event_urls SET status='scraped', scraped_at=CURRENT_TIMESTAMP WHERE url=?",
        (url,),
    )
    conn.commit()
    conn.close()


def mark_url_failed(url: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE event_urls SET status='failed', scraped_at=CURRENT_TIMESTAMP WHERE url=?",
        (url,),
    )
    conn.commit()
    conn.close()


def upsert_event(item: dict) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO events
            (event_id, event_name, artist_name, datetime,
             venue_name, venue_location, venue_latitude, venue_longitude,
             description, cost, availability,
             promoter, category, target_demographic, url)
        VALUES
            (:event_id, :event_name, :artist_name, :datetime,
             :venue_name, :venue_location, :venue_latitude, :venue_longitude,
             :description, :cost, :availability,
             :promoter, :category, :target_demographic, :url)
        ON CONFLICT(event_id) DO UPDATE SET
            event_name         = excluded.event_name,
            artist_name        = excluded.artist_name,
            datetime           = excluded.datetime,
            venue_name         = excluded.venue_name,
            venue_location     = excluded.venue_location,
            venue_latitude     = excluded.venue_latitude,
            venue_longitude    = excluded.venue_longitude,
            description        = excluded.description,
            cost               = excluded.cost,
            availability       = excluded.availability,
            promoter           = excluded.promoter,
            category           = excluded.category,
            target_demographic = excluded.target_demographic,
            url                = excluded.url,
            scraped_at         = CURRENT_TIMESTAMP
        """,
        {
            "event_id":           item.get("event_id"),
            "event_name":         item.get("event_name", ""),
            "artist_name":        item.get("artist_name"),
            "datetime":           item.get("datetime"),
            "venue_name":         item.get("venue_name"),
            "venue_location":     item.get("venue_location"),
            "venue_latitude":     item.get("venue_latitude"),
            "venue_longitude":    item.get("venue_longitude"),
            "description":        item.get("description"),
            "cost":               item.get("cost", "Unknown"),
            "availability":       item.get("availability", "Unknown"),
            "promoter":           item.get("promoter", ""),
            "category":           item.get("category", "Music"),
            "target_demographic": item.get("target_demographic", "General"),
            "url":                item.get("url"),
        },
    )
    conn.commit()
    conn.close()
