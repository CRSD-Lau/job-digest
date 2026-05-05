import hashlib
import httpx
from bs4 import BeautifulSoup

SOURCE = "gnb_ere"
URL = "https://www.ere.gnb.ca/competition.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "JobDigestBot/1.0 (personal use)"
}

# GNB roles relevant to this digest (others like nurses/teachers are auto-excluded by classifier)
RELEVANT_DEPARTMENTS = {
    "snb", "dtir", "transportation", "env", "environment",
    "public works", "energy", "natural resources",
}


def fetch(config: dict) -> list[dict]:
    try:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = client.get(config.get("url", URL))
            resp.raise_for_status()
    except Exception as e:
        print(f"  [gnb_ere] Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    postings = []

    # Page is an HTML table: Competition Title | Competition # | Department | Location | Closing Date
    # Find the competitions table by its header row
    table = None
    for t in soup.find_all("table"):
        header = t.find("tr")
        if header and "Competition Title" in header.get_text():
            table = t
            break

    if not table:
        print("  [gnb_ere] No table found on page")
        return []

    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        title = cells[0].get_text(strip=True)
        comp_num = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        department = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        location = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        closing = cells[4].get_text(strip=True) if len(cells) > 4 else ""

        if not title:
            continue

        # GNB ERE uses ASP.NET PostBack — no direct competition URLs available
        url = "https://www.ere.gnb.ca/competition.aspx"

        posting_id = f"gnb-{comp_num}" if comp_num else f"gnb-{hashlib.md5(title.encode()).hexdigest()[:10]}"

        postings.append({
            "posting_id": posting_id,
            "source": SOURCE,
            "source_url": url,
            "title": title,
            "company": f"Government of NB – {department}" if department else "Government of NB",
            "location": location,
            "date_posted": None,
            "description_snippet": f"Competition: {comp_num} | Closes: {closing}",
            "raw_category": department,
        })

    return postings
