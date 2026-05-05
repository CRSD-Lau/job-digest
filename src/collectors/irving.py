"""
Irving Oil / J.D. Irving collector via Workday JSON API.
Hits the public Workday jobs endpoint — no authentication needed.
"""
import hashlib
import logging
import time

import httpx

SOURCE = "irving"

WORKDAY_SOURCES = [
    {
        "name": "Irving Oil",
        "api_url": "https://irvingoil.wd3.myworkdayjobs.com/wday/cxs/irvingoil/IOL_Careers_Primary/jobs",
        "base_url": "https://irvingoil.wd3.myworkdayjobs.com",
        "path_prefix": "/en-US/IOL_Careers_Primary/job/",
    },
    {
        "name": "J.D. Irving",
        "api_url": "https://jdirving.wd3.myworkdayjobs.com/wday/cxs/jdirving/JDI_Careers/jobs",
        "base_url": "https://jdirving.wd3.myworkdayjobs.com",
        "path_prefix": "/en-US/JDI_Careers/job/",
    },
]

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

SAINT_JOHN_KEYWORDS = {"saint john", "sj", "new brunswick", "nb", "sussex", "rothesay", "quispamsis"}


def _is_local(job: dict) -> bool:
    """Return True if the job appears to be in the Saint John area."""
    location_parts = []

    # locationsText may be a string or a list of strings
    locs_text = job.get("locationsText", "")
    if isinstance(locs_text, list):
        location_parts.extend(loc.lower() for loc in locs_text)
    elif isinstance(locs_text, str) and locs_text:
        location_parts.append(locs_text.lower())

    # locations may be a list of dicts with city/region fields
    for loc in job.get("locations", []):
        if isinstance(loc, dict):
            for k in ("city", "region", "countrySubdivisionCode"):
                v = loc.get(k, "")
                if v:
                    location_parts.append(v.lower())
        elif isinstance(loc, str):
            location_parts.append(loc.lower())

    combined = " ".join(location_parts)
    if not combined:
        return True  # no location info — include and let classifier decide
    return any(kw in combined for kw in SAINT_JOHN_KEYWORDS)


def _fetch_workday(source: dict, limit: int = 100) -> list[dict]:
    """Fetch jobs from a single Workday tenant."""
    postings = []
    offset = 0

    with httpx.Client(timeout=20) as client:
        while True:
            payload = {
                "appliedFacets": {},
                "limit": min(limit, 20),
                "offset": offset,
                "searchText": "",
            }
            try:
                resp = client.post(source["api_url"], json=payload, headers=HEADERS)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logging.warning(f"  [{SOURCE}] {source['name']} request error at offset {offset}: {e}")
                break

            jobs = data.get("jobPostings", [])
            if not jobs:
                break

            for job in jobs:
                if not _is_local(job):
                    continue

                title = job.get("title", "").strip()
                if not title:
                    continue

                # Build canonical URL from job path
                external_path = job.get("externalPath", "")
                if external_path:
                    job_url = source["base_url"] + external_path
                else:
                    # Fallback: slug-based URL
                    bulletin = job.get("bulletFields", [])
                    slug = bulletin[0] if bulletin else title.replace(" ", "-")
                    job_url = source["base_url"] + source["path_prefix"] + slug

                posting_id_raw = job.get("bulletFields", [None])[0] or job_url
                posting_id = SOURCE + "-" + hashlib.md5(posting_id_raw.encode()).hexdigest()[:10]

                # Location string (may be str or list)
                locs = job.get("locationsText", "")
                if isinstance(locs, list):
                    location = ", ".join(locs) if locs else "Saint John, NB"
                else:
                    location = locs or "Saint John, NB"

                # Posted date
                date_posted = job.get("postedOn", None)
                if date_posted:
                    # Workday gives "Posted 2 Days Ago" or ISO
                    if date_posted.startswith("Posted"):
                        date_posted = None  # relative, skip
                    else:
                        date_posted = date_posted[:10]  # trim to YYYY-MM-DD

                postings.append({
                    "posting_id": posting_id,
                    "source": SOURCE,
                    "source_url": job_url,
                    "title": title,
                    "company": source["name"],
                    "location": location,
                    "date_posted": date_posted,
                    "wage_raw": None,
                    "description_snippet": None,
                    "raw_category": "employer_direct",
                })

            total = data.get("total", 0)
            offset += len(jobs)
            if offset >= total or offset >= limit:
                break

            time.sleep(1)

    return postings


def fetch(config: dict) -> list[dict]:
    all_postings = []
    sources = config.get("workday_sources", WORKDAY_SOURCES)

    for source in sources:
        if not source.get("enabled", True):
            continue
        try:
            postings = _fetch_workday(source, limit=config.get("limit_per_source", 200))
            logging.info(f"  [{SOURCE}] {source['name']}: {len(postings)} local postings")
            all_postings.extend(postings)
        except Exception as e:
            logging.warning(f"  [{SOURCE}] {source['name']} error: {e}")
        time.sleep(2)

    return all_postings
