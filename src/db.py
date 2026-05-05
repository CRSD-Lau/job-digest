import sqlite3
import json
from pathlib import Path
from datetime import date, datetime


def get_week_label(d: date | None = None) -> str:
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS postings (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                posting_id                  TEXT NOT NULL,
                source                      TEXT NOT NULL,
                source_url                  TEXT,
                title                       TEXT NOT NULL,
                company                     TEXT,
                location                    TEXT,
                date_posted                 DATE,
                date_found                  DATE NOT NULL,
                week_found                  TEXT NOT NULL,
                week_last_seen              TEXT NOT NULL,
                employment_type             TEXT,
                wage_raw                    TEXT,
                wage_min                    REAL,
                wage_max                    REAL,
                wage_period                 TEXT,
                description_snippet         TEXT,
                raw_category                TEXT,
                normalized_category         TEXT,
                is_relevant                 INTEGER NOT NULL DEFAULT 1,
                exclusion_reason            TEXT,
                classification_confidence   REAL,
                flagged_for_review          INTEGER DEFAULT 0,
                content_hash                TEXT NOT NULL,
                status                      TEXT NOT NULL DEFAULT 'new',
                repost_count                INTEGER DEFAULT 0,
                UNIQUE(source, posting_id)
            );

            CREATE TABLE IF NOT EXISTS weekly_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date        DATE NOT NULL,
                week_label      TEXT NOT NULL,
                sources_checked TEXT,
                total_fetched   INTEGER DEFAULT 0,
                total_new       INTEGER DEFAULT 0,
                total_relevant  INTEGER DEFAULT 0,
                total_excluded  INTEGER DEFAULT 0,
                errors          TEXT,
                digest_path     TEXT
            );

            CREATE TABLE IF NOT EXISTS source_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER REFERENCES weekly_runs(id),
                source      TEXT NOT NULL,
                status      TEXT,
                items_found INTEGER DEFAULT 0,
                items_new   INTEGER DEFAULT 0,
                error_msg   TEXT,
                duration_ms INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_postings_week   ON postings(week_found);
            CREATE INDEX IF NOT EXISTS idx_postings_cat    ON postings(normalized_category);
            CREATE INDEX IF NOT EXISTS idx_postings_status ON postings(status);
            CREATE INDEX IF NOT EXISTS idx_postings_hash   ON postings(content_hash);
        """)


def start_run(db_path: str, sources: list[str]) -> int:
    today = date.today()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO weekly_runs (run_date, week_label, sources_checked) VALUES (?, ?, ?)",
            (today.isoformat(), get_week_label(today), json.dumps(sources))
        )
        return cur.lastrowid


def end_run(db_path: str, run_id: int, stats: dict, digest_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE weekly_runs
            SET total_fetched=?, total_new=?, total_relevant=?, total_excluded=?, errors=?, digest_path=?
            WHERE id=?
        """, (
            stats.get("total_fetched", 0),
            stats.get("total_new", 0),
            stats.get("total_relevant", 0),
            stats.get("total_excluded", 0),
            json.dumps(stats.get("errors", [])),
            digest_path,
            run_id,
        ))


def log_source_run(db_path: str, run_id: int, source: str, status: str,
                   items_found: int, items_new: int,
                   error_msg: str | None = None, duration_ms: int = 0) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO source_runs (run_id, source, status, items_found, items_new, error_msg, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (run_id, source, status, items_found, items_new, error_msg, duration_ms))


def upsert_posting(db_path: str, posting: dict) -> str:
    """Insert or update a posting. Returns 'new', 'updated', or 'duplicate'."""
    today = date.today()
    week = get_week_label(today)

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id, status, week_found FROM postings WHERE source=? AND posting_id=?",
            (posting["source"], posting["posting_id"])
        ).fetchone()

        if existing:
            # Only graduate from 'new' → 'active' when seen in a DIFFERENT week.
            # Within the same week (e.g. multiple partial runs) keep it 'new'
            # so it still appears in the digest's "New This Week" section.
            if existing["status"] == "removed":
                new_status = "reposted"
                conn.execute(
                    "UPDATE postings SET repost_count = repost_count + 1 WHERE id=?",
                    (existing["id"],)
                )
            elif existing["status"] == "new" and existing["week_found"] == week:
                new_status = "new"  # stay 'new' within the same week
            else:
                new_status = "active"
            conn.execute(
                "UPDATE postings SET week_last_seen=?, status=? WHERE id=?",
                (week, new_status, existing["id"])
            )
            return "updated"

        # Check for duplicate by content hash
        hash_existing = conn.execute(
            "SELECT id FROM postings WHERE content_hash=? AND source!=?",
            (posting["content_hash"], posting["source"])
        ).fetchone()

        status = "new"
        conn.execute("""
            INSERT INTO postings (
                posting_id, source, source_url, title, company, location,
                date_posted, date_found, week_found, week_last_seen,
                employment_type, wage_raw, wage_min, wage_max, wage_period,
                description_snippet, raw_category, normalized_category,
                is_relevant, exclusion_reason, classification_confidence,
                flagged_for_review, content_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            posting["posting_id"], posting["source"], posting.get("source_url"),
            posting["title"], posting.get("company"), posting.get("location"),
            posting.get("date_posted"), today.isoformat(), week, week,
            posting.get("employment_type"), posting.get("wage_raw"),
            posting.get("wage_min"), posting.get("wage_max"), posting.get("wage_period"),
            posting.get("description_snippet"), posting.get("raw_category"),
            posting.get("normalized_category"),
            1 if posting.get("is_relevant", True) else 0,
            posting.get("exclusion_reason"),
            posting.get("classification_confidence"),
            1 if posting.get("flagged_for_review") else 0,
            posting["content_hash"], status,
        ))
        return "new"


def mark_removed_postings(db_path: str) -> int:
    """Mark postings not seen this week as removed. Returns count."""
    current_week = get_week_label()
    with get_connection(db_path) as conn:
        cur = conn.execute("""
            UPDATE postings SET status='removed'
            WHERE status IN ('new', 'active', 'reposted')
            AND week_last_seen != ?
        """, (current_week,))
        return cur.rowcount


def get_digest_data(db_path: str) -> dict:
    """Pull all data needed to render the weekly digest."""
    week = get_week_label()
    prev_week = get_previous_week_label()

    with get_connection(db_path) as conn:
        new_relevant = conn.execute("""
            SELECT * FROM postings
            WHERE week_found=? AND is_relevant=1 AND status='new'
            ORDER BY normalized_category, date_posted DESC
        """, (week,)).fetchall()

        removed = conn.execute("""
            SELECT * FROM postings
            WHERE status='removed' AND week_last_seen=? AND is_relevant=1
        """, (prev_week,)).fetchall()

        reposted = conn.execute("""
            SELECT * FROM postings
            WHERE status='reposted' AND week_last_seen=? AND is_relevant=1
        """, (week,)).fetchall()

        excluded = conn.execute("""
            SELECT exclusion_reason, COUNT(*) as cnt
            FROM postings WHERE week_found=? AND is_relevant=0
            GROUP BY exclusion_reason
        """, (week,)).fetchall()

        prev_counts = conn.execute("""
            SELECT normalized_category, COUNT(*) as cnt
            FROM postings WHERE week_found=? AND is_relevant=1
            GROUP BY normalized_category
        """, (prev_week,)).fetchall()

        curr_counts = conn.execute("""
            SELECT normalized_category, COUNT(*) as cnt
            FROM postings WHERE week_found=? AND is_relevant=1
            GROUP BY normalized_category
        """, (week,)).fetchall()

        top_companies = conn.execute("""
            SELECT company, COUNT(*) as cnt FROM postings
            WHERE week_found=? AND is_relevant=1 AND company IS NOT NULL
            GROUP BY company ORDER BY cnt DESC LIMIT 10
        """, (week,)).fetchall()

        top_titles = conn.execute("""
            SELECT title, COUNT(*) as cnt FROM postings
            WHERE week_found=? AND is_relevant=1
            GROUP BY title ORDER BY cnt DESC LIMIT 10
        """, (week,)).fetchall()

        wage_data = conn.execute("""
            SELECT normalized_category, AVG(wage_min) as avg_min, COUNT(*) as cnt
            FROM postings
            WHERE week_found=? AND is_relevant=1 AND wage_min IS NOT NULL
            GROUP BY normalized_category
        """, (week,)).fetchall()

        flagged = conn.execute("""
            SELECT * FROM postings
            WHERE week_found=? AND flagged_for_review=1
        """, (week,)).fetchall()

    return {
        "week": week,
        "prev_week": prev_week,
        "new_relevant": [dict(r) for r in new_relevant],
        "removed": [dict(r) for r in removed],
        "reposted": [dict(r) for r in reposted],
        "excluded": [dict(r) for r in excluded],
        "prev_counts": {r["normalized_category"]: r["cnt"] for r in prev_counts},
        "curr_counts": {r["normalized_category"]: r["cnt"] for r in curr_counts},
        "top_companies": [dict(r) for r in top_companies],
        "top_titles": [dict(r) for r in top_titles],
        "wage_data": [dict(r) for r in wage_data],
        "flagged": [dict(r) for r in flagged],
    }


def get_previous_week_label() -> str:
    from datetime import timedelta
    last_week = date.today() - timedelta(weeks=1)
    return get_week_label(last_week)
