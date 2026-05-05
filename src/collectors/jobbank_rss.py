import feedparser
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import urlencode
from bs4 import BeautifulSoup

SOURCE = "jobbank"
BASE_URL = "https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed"


def _build_feed_url(terms: list[str], mid: str, radius_km: int) -> str:
    params = [("sort", "D"), ("rows", "100"), ("mid", mid), ("d", str(radius_km))]
    for t in terms:
        params.append(("term", t))
    return f"{BASE_URL}?{urlencode(params)}"


def _parse_date(entry) -> str | None:
    # Job Bank uses 'updated' not 'published' in its Atom feed
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime(*val[:6]).date().isoformat()
            except Exception:
                pass
    return None


def _extract_posting_id(link: str) -> str:
    # URL format: .../jobposting/12345678
    if "/jobposting/" in link:
        return "jb-" + link.split("/jobposting/")[-1].split("?")[0].strip()
    return "jb-" + hashlib.md5(link.encode()).hexdigest()[:10]


def fetch(config: dict) -> list[dict]:
    feeds_config = config.get("feeds", [])
    mid = config.get("_mid", "22380")
    radius = config.get("_radius_km", 75)

    postings = []
    seen_ids = set()

    for feed_def in feeds_config:
        terms = feed_def.get("terms", [])
        url = _build_feed_url(terms, mid, radius)

        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            print(f"  [jobbank] Feed error ({feed_def.get('name')}): {e}")
            continue

        for entry in parsed.entries:
            link = getattr(entry, "link", "") or ""
            posting_id = _extract_posting_id(link)

            if posting_id in seen_ids:
                continue
            seen_ids.add(posting_id)

            title = getattr(entry, "title", "").strip()
            if not title:
                continue

            summary = getattr(entry, "summary", "") or ""
            company, location, wage_raw, snippet = _parse_summary(summary)

            postings.append({
                "posting_id": posting_id,
                "source": SOURCE,
                "source_url": link,
                "title": title,
                "company": company,
                "location": location or "New Brunswick",
                "date_posted": _parse_date(entry),
                "wage_raw": wage_raw,
                "description_snippet": snippet,
                "raw_category": feed_def.get("name"),
            })

        time.sleep(2)  # polite delay between feeds

    return postings


def _parse_summary(summary: str) -> tuple[str | None, str | None, str | None, str | None]:
    """
    Job Bank RSS summaries are HTML fragments like:
    <strong>Location:</strong> Saint John (NB)<br/><strong>Employer:</strong> Acme Co<br/>...
    Returns (company, location, wage_raw, clean_snippet)
    """
    if not summary:
        return None, None, None, None

    soup = BeautifulSoup(summary, "lxml")
    text = soup.get_text(separator="\n")

    company = None
    location = None
    wage_raw = None

    # BeautifulSoup splits <strong>Label:</strong> value into two lines:
    #   "Label:"  on one line, value on the next
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i, line in enumerate(lines):
        low = line.lower().rstrip(":")
        value = lines[i + 1] if i + 1 < len(lines) else ""
        # Also handle "Label: value" on same line
        if ":" in line:
            parts = line.split(":", 1)
            if parts[1].strip():
                value = parts[1].strip()
        if low in ("employer", "company"):
            company = value or None
        elif low == "location":
            location = value or None
        elif low in ("salary", "wage"):
            wage_raw = value or None

    return company, location, wage_raw, text[:400].strip()
