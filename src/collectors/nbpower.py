"""
NB Power careers collector.
PeopleSoft career portal at careers.nbpower.com — scrapes the search results page.
"""
import hashlib
import logging
import re

import httpx
from bs4 import BeautifulSoup

SOURCE = "nbpower"

CAREERS_URL = (
    "https://www.careers.nbpower.com/psc/prod/EMPLOYEE/HRMS/c/"
    "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
    "?FOCUS=Applicant&Page=HRS_APP_SCHJOB&Action=U&FOCUS=Applicant&SiteId=2"
)
BASE_URL = "https://www.careers.nbpower.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}


def _parse_date(text: str) -> str | None:
    """Convert various date formats to ISO YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    # MM/DD/YYYY
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    # YYYY-MM-DD
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    # Month DD, YYYY  e.g. "May 1, 2026"
    months = {"january": "01", "february": "02", "march": "03", "april": "04",
               "may": "05", "june": "06", "july": "07", "august": "08",
               "september": "09", "october": "10", "november": "11", "december": "12"}
    m = re.match(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", text, re.IGNORECASE)
    if m:
        mon = months.get(m.group(1).lower())
        if mon:
            return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"
    return None


def fetch(config: dict) -> list[dict]:
    url = config.get("url", CAREERS_URL)
    postings = []

    try:
        # verify=False handles Windows SSL cert store issues with PeopleSoft certs
        with httpx.Client(timeout=30, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=HEADERS)
            resp.raise_for_status()
    except Exception as e:
        logging.error(f"  [{SOURCE}] Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # PeopleSoft job listings are typically in a div with class containing "job" or a table
    # Look for job listing containers
    job_items = []

    # Strategy 1: look for elements with job title links
    # PeopleSoft often uses <a> tags with job posting IDs in the href
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        # PeopleSoft job links contain HRS_CE_JOB_DTL or similar markers
        if ("HRS_CE_JOB_DTL" in href or "jobId" in href.lower() or
                "jobpostingid" in href.lower()) and text:
            job_items.append((text, href))

    # Strategy 2: look for table rows with job data
    if not job_items:
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                # Look for rows where first or second cell has a link
                for cell in cells[:3]:
                    a = cell.find("a")
                    if a and a.get_text(strip=True) and len(a.get_text(strip=True)) > 5:
                        job_items.append((a.get_text(strip=True), a.get("href", "")))
                        break

    # Strategy 3: div-based listings (newer PeopleSoft Fluid UI)
    if not job_items:
        for div in soup.find_all("div", class_=re.compile(r"job|posting|result", re.I)):
            a = div.find("a")
            if a and a.get_text(strip=True):
                job_items.append((a.get_text(strip=True), a.get("href", "")))

    if not job_items:
        # If we got a page but no jobs, it might be JavaScript-rendered
        logging.warning(f"  [{SOURCE}] No job listings found — page may require JavaScript")
        logging.info(f"  [{SOURCE}] Page title: {soup.title.string if soup.title else 'N/A'}")
        return []

    seen = set()
    for title, href in job_items:
        title = title.strip()
        if not title or title.lower() in ("search", "apply", "view", "more"):
            continue

        if href:
            job_url = href if href.startswith("http") else BASE_URL + href
        else:
            job_url = url

        key = title.lower() + job_url
        if key in seen:
            continue
        seen.add(key)

        posting_id = SOURCE + "-" + hashlib.md5(key.encode()).hexdigest()[:10]

        postings.append({
            "posting_id": posting_id,
            "source": SOURCE,
            "source_url": job_url,
            "title": title,
            "company": "NB Power",
            "location": "New Brunswick",
            "date_posted": None,
            "wage_raw": None,
            "description_snippet": None,
            "raw_category": "employer_direct",
        })

    logging.info(f"  [{SOURCE}] {len(postings)} postings found")
    return postings
