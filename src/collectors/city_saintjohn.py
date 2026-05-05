"""
City of Saint John job postings via njoyn.com portal.
Scrapes the public job listing table at saintjohn.njoyn.com.
"""
import hashlib
import logging
import re

import httpx
from bs4 import BeautifulSoup

SOURCE = "city_sj"

LISTING_URL = "https://saintjohn.njoyn.com/CL2/xweb/xweb.asp?page=joblisting&CLID=51331"
BASE_URL = "https://saintjohn.njoyn.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def _parse_date(text: str) -> str | None:
    """Parse dates like '01/05/2026' or '2026-05-01' to ISO format."""
    if not text:
        return None
    text = text.strip()
    # MM/DD/YYYY
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    # YYYY-MM-DD already
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    return None


def fetch(config: dict) -> list[dict]:
    url = config.get("url", LISTING_URL)
    postings = []

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=HEADERS, follow_redirects=True)
            resp.raise_for_status()
    except Exception as e:
        logging.error(f"  [{SOURCE}] Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Find the jobs table — look for a table with "Title" and "Date" headers
    target_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers:
            # Check first row for header-like cells
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True).lower() for td in first_row.find_all(["th", "td"])]
        if any("title" in h or "position" in h for h in headers):
            target_table = table
            break

    if not target_table:
        # Fallback: look for any table with job-like link text
        for table in soup.find_all("table"):
            links = table.find_all("a")
            if len(links) >= 3:
                target_table = table
                break

    if not target_table:
        logging.warning(f"  [{SOURCE}] Could not find jobs table on page")
        return []

    rows = target_table.find_all("tr")
    # Skip header row(s)
    data_rows = [r for r in rows if r.find("a") or (len(r.find_all("td")) >= 2)]
    # If the first row has th elements, skip it
    if rows and rows[0].find("th"):
        data_rows = rows[1:]

    for row in data_rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        # Find the cell with a link (job title)
        title_cell = None
        job_url = None
        for cell in cells:
            a = cell.find("a")
            if a and a.get_text(strip=True):
                title_cell = cell
                href = a.get("href", "")
                if href and not href.startswith("javascript"):
                    job_url = href if href.startswith("http") else BASE_URL + "/" + href.lstrip("/")
                break

        if not title_cell:
            continue

        title = title_cell.get_text(strip=True)
        if not title:
            continue

        # Use listing URL as fallback if no direct link
        if not job_url:
            job_url = url

        # Collect remaining cell texts
        cell_texts = [c.get_text(strip=True) for c in cells if c != title_cell]

        # Try to find a date — look for date-pattern text
        date_posted = None
        closing_date = None
        for text in cell_texts:
            d = _parse_date(text)
            if d and not date_posted:
                date_posted = d
            elif d and not closing_date:
                closing_date = d

        # Category / job type (usually 2nd or 3rd cell)
        category = cell_texts[0] if cell_texts else None

        posting_id = SOURCE + "-" + hashlib.md5((title + (job_url or "")).encode()).hexdigest()[:10]

        postings.append({
            "posting_id": posting_id,
            "source": SOURCE,
            "source_url": job_url or url,
            "title": title,
            "company": "City of Saint John",
            "location": "Saint John, NB",
            "date_posted": date_posted,
            "wage_raw": None,
            "description_snippet": f"Category: {category}" if category else None,
            "raw_category": "municipal",
        })

    logging.info(f"  [{SOURCE}] {len(postings)} postings found")
    return postings
