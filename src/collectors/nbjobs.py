import hashlib
import time
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote_plus

SOURCE = "nbjobs"
BASE_URL = "https://www.nbjobs.ca/jobs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "JobDigestBot/1.0 (personal use)"
}


def _build_url(category: str, location: str = "Saint John", radius: int = 75) -> str:
    params = {"category": category, "keyword": "", "location": location, "radius": str(radius)}
    return f"{BASE_URL}?{urlencode(params)}"


def _parse_page(html: str, category: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    postings = []

    # NBjobs uses repeated job card divs — find by common patterns
    cards = (
        soup.find_all("div", class_=lambda c: c and "job" in c.lower() and "card" in c.lower())
        or soup.find_all("article")
        or soup.find_all("li", class_=lambda c: c and "job" in c.lower())
    )

    if not cards:
        # Fallback: find all <a> tags with job-like hrefs
        cards = [a.parent for a in soup.find_all("a", href=lambda h: h and "/jobs/" in h)]

    for card in cards:
        title_tag = (
            card.find("h2") or card.find("h3") or
            card.find("a", class_=lambda c: c and "title" in (c or "").lower())
        )
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        link_tag = title_tag.find("a") or (title_tag if title_tag.name == "a" else card.find("a"))
        url = link_tag["href"] if link_tag and link_tag.get("href") else ""
        if url and not url.startswith("http"):
            url = "https://www.nbjobs.ca" + url

        posting_id = "nbj-" + (url.split("/")[-1].split("?")[0] if url else hashlib.md5(title.encode()).hexdigest()[:10])

        # Company
        company_tag = card.find(class_=lambda c: c and "company" in (c or "").lower())
        company = company_tag.get_text(strip=True) if company_tag else None

        # Location
        location_tag = card.find(class_=lambda c: c and "location" in (c or "").lower())
        location = location_tag.get_text(strip=True) if location_tag else None

        # Date
        date_tag = card.find("time") or card.find(class_=lambda c: c and "date" in (c or "").lower())
        date_str = None
        if date_tag:
            date_str = date_tag.get("datetime") or date_tag.get_text(strip=True)

        # Snippet
        snippet_tag = card.find("p") or card.find(class_=lambda c: c and "desc" in (c or "").lower())
        snippet = snippet_tag.get_text(strip=True)[:500] if snippet_tag else None

        postings.append({
            "posting_id": posting_id,
            "source": SOURCE,
            "source_url": url,
            "title": title,
            "company": company,
            "location": location or "New Brunswick",
            "date_posted": date_str,
            "description_snippet": snippet,
            "raw_category": category,
        })

    return postings


def fetch(config: dict) -> list[dict]:
    categories = config.get("categories", [])
    postings = []
    seen_ids = set()

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for category in categories:
            url = _build_url(category)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                page_postings = _parse_page(resp.text, category)
                for p in page_postings:
                    if p["posting_id"] not in seen_ids:
                        seen_ids.add(p["posting_id"])
                        postings.append(p)
            except Exception as e:
                print(f"  [nbjobs] Error fetching '{category}': {e}")

            time.sleep(3)

    return postings
