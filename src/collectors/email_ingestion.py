"""
Reads job alert emails from Gmail via IMAP.
Requires GMAIL_USER and GMAIL_APP_PASSWORD in .env.
Enable this collector by setting email_ingestion.enabled: true in sources.yaml.
"""
import imaplib
import email
import hashlib
import re
from datetime import date, timedelta
from email.header import decode_header
from bs4 import BeautifulSoup

SOURCE = "email"
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def _decode_str(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _get_body(msg) -> str:
    """Extract plain text or HTML body from email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
            elif ct == "text/plain" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")
    return body


def _parse_indeed_email(body: str, date_str: str) -> list[dict]:
    soup = BeautifulSoup(body, "lxml")
    postings = []

    # Indeed job cards typically have a job title link and metadata below
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "indeed.com" not in href and "clk?" not in href:
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        # Walk up to find the card container, then extract company/location
        container = a.find_parent("td") or a.find_parent("div") or a.parent
        card_text = container.get_text(separator="\n", strip=True) if container else ""

        company, location, wage = _extract_card_metadata(card_text, title)

        pid = "em-ind-" + hashlib.md5((title + href).encode()).hexdigest()[:10]
        postings.append({
            "posting_id": pid,
            "source": SOURCE,
            "source_url": href,
            "title": title,
            "company": company,
            "location": location or "Saint John, NB",
            "date_posted": date_str,
            "wage_raw": wage,
            "description_snippet": card_text[:300],
            "raw_category": "indeed_alert",
        })

    return postings


def _parse_linkedin_email(body: str, date_str: str) -> list[dict]:
    """
    LinkedIn alert emails structure each job as a <tr> row containing:
      Line 0: Job title
      Line 1: Company · Location
      Line 2: (optional) social proof / status
    The <a> links inside the row point to /jobs/view/<id> but don't contain the title text.
    """
    soup = BeautifulSoup(body, "lxml")
    postings = []
    seen_job_ids = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "linkedin.com/jobs" not in href and "linkedin.com/comm/jobs" not in href:
            continue

        job_id_match = re.search(r"/jobs/view/(\d+)", href)
        if not job_id_match:
            continue
        job_id = job_id_match.group(1)
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        clean_url = f"https://www.linkedin.com/jobs/view/{job_id}"

        # Walk up to find the <tr> job card container
        row = a.find_parent("tr")
        if not row:
            continue

        lines = [l.strip() for l in row.get_text(separator="\n", strip=True).split("\n") if l.strip()]
        if not lines:
            continue

        title = lines[0]
        if not title or len(title) < 4:
            continue

        # Line 1 is typically "Company · City, Province, Country"
        company = None
        location = None
        if len(lines) > 1:
            company_loc = lines[1]
            if "·" in company_loc:
                parts = company_loc.split("·", 1)
                company = parts[0].strip()
                location = parts[1].strip()
            else:
                company = company_loc

        card_text = "\n".join(lines)
        pid = "em-li-" + hashlib.md5((title + clean_url).encode()).hexdigest()[:10]
        postings.append({
            "posting_id": pid,
            "source": SOURCE,
            "source_url": clean_url,
            "title": title,
            "company": company,
            "location": location or "Saint John, NB",
            "date_posted": date_str,
            "wage_raw": None,
            "description_snippet": card_text[:300],
            "raw_category": "linkedin_alert",
        })

    return postings


def _extract_card_metadata(text: str, title: str) -> tuple[str | None, str | None, str | None]:
    company = None
    location = None
    wage = None

    lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() != title]

    # Wage pattern: $XX/hr or $XX,XXX
    for line in lines:
        if re.search(r"\$[\d,]+", line):
            wage = line[:80]
            break

    # Location: line containing province abbreviation or postal-code-like pattern
    for line in lines:
        if re.search(r"\bNB\b|\bNew Brunswick\b|Saint John|Moncton|Fredericton", line, re.I):
            location = line[:100]
            break

    # Company: first non-empty line that isn't title/location/wage
    for line in lines:
        if line != location and line != wage and len(line) > 1:
            company = line[:100]
            break

    return company, location, wage


def fetch(config: dict, gmail_user: str, gmail_password: str) -> list[dict]:
    folder = "JobAlerts"
    postings = []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(gmail_user, gmail_password)
        mail.select(f'"{folder}"')

        # Fetch all emails from the past 8 days (catches read + unread)
        since = (date.today() - timedelta(days=8)).strftime("%d-%b-%Y")
        _, msg_ids = mail.search(None, f'SINCE "{since}"')
        if not msg_ids[0]:
            return []

        source_senders = {s["sender"]: s["name"] for s in config.get("sources", [])}

        for mid in msg_ids[0].split():
            _, data = mail.fetch(mid, "(RFC822)")
            raw = data[0][1]
            msg = email.message_from_bytes(raw)

            sender = msg.get("From", "")
            date_str = msg.get("Date", "")
            body = _get_body(msg)

            if "indeed.com" in sender:
                postings.extend(_parse_indeed_email(body, date_str))
            elif "linkedin.com" in sender:
                postings.extend(_parse_linkedin_email(body, date_str))

            # Mark as read
            mail.store(mid, "+FLAGS", "\\Seen")

        mail.logout()

    except Exception as e:
        print(f"  [email] IMAP error: {e}")

    return postings
