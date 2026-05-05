"""
Weekly job digest runner.
Usage: python run_weekly.py [--dry-run] [--source SOURCE]
"""
import sys
import time
import argparse
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
import yaml
import os
from rich.console import Console
from rich.table import Table

load_dotenv(override=True)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src import db, classifier, deduplicator, digest, llm_classifier
from src.collectors import jobbank_rss, nbjobs, gnb_ere, email_ingestion, irving, city_saintjohn, nbpower

console = Console()

DB_PATH = os.getenv("DB_PATH", "data/jobs.db")
REPORTS_DIR = os.getenv("REPORTS_DIR", "reports")
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


def setup_logging():
    Path(LOGS_DIR).mkdir(exist_ok=True)
    log_file = Path(LOGS_DIR) / f"{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config() -> dict:
    with open("config/sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_collector(name: str, fetch_fn, fetch_args: dict) -> tuple[list[dict], str | None]:
    """Run a single collector. Returns (postings, error_msg)."""
    try:
        postings = fetch_fn(fetch_args)
        return postings, None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        logging.error(f"[{name}] {msg}")
        return [], msg


def process_postings(raw_postings: list[dict], dry_run: bool) -> dict:
    stats = {"new": 0, "updated": 0, "excluded": 0, "errors": []}

    for raw in raw_postings:
        try:
            # Classification already done upstream — only run if missing
            if "normalized_category" not in raw:
                result = classifier.classify(
                    title=raw.get("title", ""),
                    snippet=raw.get("description_snippet", "") or "",
                    raw_category=raw.get("raw_category", "") or "",
                )
                raw.update(result)

            # Deduplicate
            raw["content_hash"] = deduplicator.make_hash(
                raw.get("title", ""),
                raw.get("company", ""),
                raw.get("location", ""),
            )

            if not raw.get("is_relevant"):
                stats["excluded"] += 1

            if not dry_run:
                outcome = db.upsert_posting(DB_PATH, raw)
                if outcome == "new":
                    stats["new"] += 1
                else:
                    stats["updated"] += 1

        except Exception as e:
            stats["errors"].append(f"{raw.get('title', '?')}: {e}")
            logging.warning(f"  Processing error: {e}")

    return stats


def send_digest_email(report_path: str, week: str, gmail_user: str, gmail_pass: str, recipients: list[str] | None = None) -> None:
    import markdown as md_lib
    import re as _re

    body_md = Path(report_path).read_text(encoding="utf-8")
    body_html = md_lib.markdown(body_md, extensions=["tables", "nl2br"])

    # Pull headline stats from markdown for the hero strip
    total_m   = _re.search(r'\*\*(\d+)\*\* relevant', body_md)
    new_m     = _re.search(r'\*\*(\d+) new\*\*', body_md)
    removed_m = _re.search(r'\*\*(\d+) removed\*\*', body_md)
    total_jobs   = total_m.group(1)   if total_m   else "0"
    new_jobs     = new_m.group(1)     if new_m     else "0"
    removed_jobs = removed_m.group(1) if removed_m else "0"

    # Colours & spacing as Python vars so f-string stays readable
    C_NAVY   = "#0c1f3d"
    C_NAVY2  = "#1a3a5c"
    C_ORANGE = "#f97316"
    C_WHITE  = "#ffffff"
    C_BG     = "#e8edf2"
    C_BODY   = "#ffffff"
    C_BORDER = "#e2e8f0"
    C_TEXT   = "#0f172a"
    C_MUTED  = "#64748b"
    C_GREEN  = "#059669"

    html = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<!--[if mso]>
<noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
<![endif]-->
<title>Job Digest &mdash; {week}</title>
<style type="text/css">
  /* Client resets */
  body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; }}
  body {{ margin: 0 !important; padding: 0 !important; background-color: {C_BG}; }}
  /* Content styles (Gmail/Apple Mail pick these up) */
  .mc h2 {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
    text-transform: uppercase; color: #94a3b8;
    margin: 28px 0 12px; padding-bottom: 8px;
    border-bottom: 1px solid #f1f5f9;
  }}
  .mc h3 {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    font-size: 13px; font-weight: 700; color: {C_TEXT};
    background: #f8fafc; border-left: 3px solid {C_ORANGE};
    padding: 8px 12px; margin: 18px 0 8px;
  }}
  .mc table {{ width: 100% !important; border-collapse: collapse; font-size: 13px; margin-bottom: 8px; }}
  .mc th {{
    background: {C_NAVY} !important; color: rgba(255,255,255,0.75) !important;
    font-size: 10px; font-weight: 700; letter-spacing: 1px;
    text-transform: uppercase; text-align: left; padding: 9px 12px;
  }}
  .mc td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; color: #334155; font-size: 13px; }}
  .mc tr:last-child td {{ border-bottom: none; }}
  .mc tbody tr:nth-child(even) td {{ background: #f9fafb; }}
  .mc td a {{ color: {C_NAVY2}; font-weight: 600; text-decoration: none; }}
  .mc td:last-child {{ font-size: 10px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; color: #94a3b8; white-space: nowrap; }}
  .mc td:nth-child(3) {{ color: {C_GREEN}; font-weight: 600; font-size: 12px; }}
  .mc ul {{ list-style: disc; padding-left: 20px; margin: 8px 0; }}
  .mc li {{ font-size: 14px; line-height: 1.6; padding: 3px 0; color: #334155; }}
  .mc ol {{ list-style: decimal; padding-left: 20px; margin: 8px 0; }}
  .mc p {{ font-size: 14px; line-height: 1.6; color: {C_MUTED}; margin: 6px 0; }}
  .mc em {{ color: #94a3b8; font-style: normal; }}
  .mc strong {{ color: {C_TEXT}; }}
  .mc hr {{ border: none; border-top: 1px solid #f1f5f9; margin: 20px 0; }}
  /* Mobile */
  @media screen and (max-width: 600px) {{
    .shell {{ width: 100% !important; }}
    .mob-pad {{ padding: 24px 16px !important; }}
    .mob-stack {{ display: block !important; width: 100% !important; }}
    .mob-hide {{ display: none !important; }}
    .stat-num {{ font-size: 20px !important; }}
    .hdr-title {{ font-size: 22px !important; }}
    .mc td, .mc th {{ padding: 7px 8px !important; font-size: 12px !important; }}
    .mc td a {{ font-size: 12px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:{C_BG};">

<!-- Preheader (hidden preview text) -->
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">
  {new_jobs} new trades &amp; labour jobs this week in Saint John, NB &mdash; {week}
  &zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;
</div>

<!-- Outer wrapper -->
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:{C_BG};">
<tr><td align="center" style="padding:24px 12px;">

  <!-- Shell (600px) -->
  <table class="shell" role="presentation" border="0" cellpadding="0" cellspacing="0" width="600" style="width:600px;">

    <!-- ═══ HEADER ═══ -->
    <tr>
      <td style="background-color:{C_NAVY};border-radius:10px 10px 0 0;padding:36px 40px 28px;" class="mob-pad">
        <!--[if gte mso 9]>
        <v:rect xmlns:v="urn:schemas-microsoft-com:vml" fill="true" stroke="false" style="width:600px;">
          <v:fill type="gradient" color="{C_NAVY}" color2="{C_NAVY2}" angle="135" focus="100%"/>
          <v:textbox style="mso-fit-shape-to-text:true" inset="36px,36px,36px,28px">
        <![endif]-->
        <!-- Eyebrow -->
        <p style="margin:0 0 10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{C_ORANGE};">
          Atlantic Canada &bull; Trades &amp; Labour
        </p>
        <!-- Title -->
        <h1 style="margin:0 0 6px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:28px;font-weight:800;color:{C_WHITE};letter-spacing:-0.5px;line-height:1.2;">
          Job Market Digest
        </h1>
        <!-- Subtitle -->
        <p style="margin:0 0 28px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;color:rgba(255,255,255,0.55);">
          Week {week} &nbsp;&bull;&nbsp; Saint John, NB &nbsp;&bull;&nbsp; 75 km radius
        </p>
        <!-- Stat pills — 4-column using native table cells -->
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td width="25%" valign="top" style="padding-right:5px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.18);border-radius:8px;padding:12px 8px;text-align:center;">
                  <div class="stat-num" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:26px;font-weight:800;color:{C_ORANGE};line-height:1;">{total_jobs}</div>
                  <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-top:5px;">Total</div>
                </td></tr>
              </table>
            </td>
            <td width="25%" valign="top" style="padding:0 4px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.18);border-radius:8px;padding:12px 8px;text-align:center;">
                  <div class="stat-num" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:26px;font-weight:800;color:{C_WHITE};line-height:1;">{new_jobs}</div>
                  <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-top:5px;">New</div>
                </td></tr>
              </table>
            </td>
            <td width="25%" valign="top" style="padding:0 4px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.18);border-radius:8px;padding:12px 8px;text-align:center;">
                  <div class="stat-num" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:26px;font-weight:800;color:{C_WHITE};line-height:1;">{removed_jobs}</div>
                  <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-top:5px;">Removed</div>
                </td></tr>
              </table>
            </td>
            <td width="25%" valign="top" style="padding-left:5px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.18);border-radius:8px;padding:12px 8px;text-align:center;">
                  <div class="stat-num" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:26px;font-weight:800;color:{C_WHITE};line-height:1;">4</div>
                  <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-top:5px;">Sources</div>
                </td></tr>
              </table>
            </td>
          </tr>
        </table>
        <!--[if gte mso 9]></v:textbox></v:rect><![endif]-->
      </td>
    </tr>

    <!-- Thin orange accent bar -->
    <tr><td style="background-color:{C_ORANGE};height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- ═══ BODY ═══ -->
    <tr>
      <td class="mc mob-pad" style="background-color:{C_BODY};padding:36px 40px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
        {body_html}
      </td>
    </tr>

    <!-- Divider -->
    <tr><td style="background-color:{C_BORDER};height:1px;font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- ═══ RESOURCES ═══ -->
    <tr>
      <td style="background-color:#f8fafc;padding:28px 40px;" class="mob-pad">
        <p style="margin:0 0 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#94a3b8;">Resources</p>
        <!-- 2×2 resource cards — native table cells, no inline-block -->
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td class="mob-stack" width="50%" valign="top" style="padding:0 6px 6px 0;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:{C_NAVY};border-radius:8px;padding:14px 16px;">
                  <p style="margin:0 0 5px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:{C_ORANGE};">Training</p>
                  <p style="margin:0 0 3px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;font-weight:700;color:{C_WHITE};">NBCC Skilled Trades</p>
                  <a href="https://www.nbcc.ca/programs/skilled-trades" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:11px;color:rgba(255,255,255,0.45);text-decoration:none;">Apprenticeship programs &rarr;</a>
                </td></tr>
              </table>
            </td>
            <td class="mob-stack" width="50%" valign="top" style="padding:0 0 6px 6px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:{C_NAVY};border-radius:8px;padding:14px 16px;">
                  <p style="margin:0 0 5px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:{C_ORANGE};">Career Help</p>
                  <p style="margin:0 0 3px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;font-weight:700;color:{C_WHITE};">WorkingNB</p>
                  <a href="https://www.workingnb.ca" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:11px;color:rgba(255,255,255,0.45);text-decoration:none;">Employment services &rarr;</a>
                </td></tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="mob-stack" width="50%" valign="top" style="padding:0 6px 0 0;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:{C_NAVY};border-radius:8px;padding:14px 16px;">
                  <p style="margin:0 0 5px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:{C_ORANGE};">Job Search</p>
                  <p style="margin:0 0 3px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;font-weight:700;color:{C_WHITE};">Job Bank Canada</p>
                  <a href="https://www.jobbank.gc.ca/home" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:11px;color:rgba(255,255,255,0.45);text-decoration:none;">Full national database &rarr;</a>
                </td></tr>
              </table>
            </td>
            <td class="mob-stack" width="50%" valign="top" style="padding:0 0 0 6px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td style="background:{C_NAVY};border-radius:8px;padding:14px 16px;">
                  <p style="margin:0 0 5px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:{C_ORANGE};">Certification</p>
                  <p style="margin:0 0 3px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;font-weight:700;color:{C_WHITE};">Apprenticeship NB</p>
                  <a href="https://www.apprenticeship.nb.ca" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:11px;color:rgba(255,255,255,0.45);text-decoration:none;">Red Seal &amp; certification &rarr;</a>
                </td></tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- ═══ SPONSOR ═══ -->
    <tr>
      <td style="background-color:#fff7ed;border-top:1px solid #fed7aa;border-bottom:1px solid #fed7aa;padding:18px 40px;" class="mob-pad">
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:13px;color:#92400e;line-height:1.5;">
              <strong style="color:#9a3412;">Reach 300+ active trades workers every week.</strong>
              Interested in a sponsored placement?
              <a href="mailto:saintjohn.job.digest@gmail.com" style="color:{C_ORANGE};font-weight:600;text-decoration:none;">Get in touch &rarr;</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- ═══ FOOTER ═══ -->
    <tr>
      <td style="background-color:{C_NAVY};border-radius:0 0 10px 10px;padding:28px 40px;" class="mob-pad">
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
              <p style="margin:0 0 2px;font-size:15px;font-weight:700;color:{C_WHITE};">Neil Mitchell</p>
              <p style="margin:0 0 20px;font-size:12px;color:rgba(255,255,255,0.4);">Curator &nbsp;&bull;&nbsp; Saint John, NB</p>
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="border-top:1px solid rgba(255,255,255,0.1);margin-top:4px;">
                <tr>
                  <td style="padding-top:16px;font-size:11px;color:rgba(255,255,255,0.3);">
                    Auto-generated every Sunday at 9am ADT &bull; saintjohn.job.digest@gmail.com
                  </td>
                  <td style="padding-top:16px;text-align:right;white-space:nowrap;">
                    <a href="mailto:saintjohn.job.digest@gmail.com" style="font-size:11px;color:rgba(255,255,255,0.3);text-decoration:none;">Unsubscribe</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>

  </table>
  <!-- /Shell -->

</td></tr>
</table>
<!-- /Outer wrapper -->

</body>
</html>"""

    # Build recipient list: always send to self, CC any extra recipients
    all_recipients = [gmail_user]
    if recipients:
        all_recipients.extend(r.strip() for r in recipients if r.strip())

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Job Digest — {week} | Saint John, NB"
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    if recipients:
        msg["CC"] = ", ".join(r.strip() for r in recipients if r.strip())

    msg.attach(MIMEText(body_md, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, all_recipients, msg.as_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Collect but do not write to DB")
    parser.add_argument("--source", help="Run only this source (jobbank, nbjobs, gnb_ere, email)")
    args = parser.parse_args()

    dry_run = args.dry_run or DRY_RUN
    setup_logging()

    console.rule("[bold blue]Job Digest — Weekly Run")
    console.print(f"Date: {date.today()} | Dry run: {dry_run}")

    config = load_config()
    sources_cfg = config.get("sources", {})
    location_cfg = config.get("location", {})

    if not dry_run:
        db.init_db(DB_PATH)

    run_id = None
    if not dry_run:
        run_id = db.start_run(DB_PATH, list(sources_cfg.keys()))

    all_raw: list[dict] = []
    source_results = []

    # --- Job Bank RSS ---
    if (not args.source or args.source == "jobbank") and sources_cfg.get("jobbank_rss", {}).get("enabled"):
        console.print("\n[cyan]Fetching Job Bank Canada RSS...")
        t0 = time.time()
        jb_config = sources_cfg["jobbank_rss"]
        jb_config["_mid"] = location_cfg.get("jobbank_mid", "22380")
        jb_config["_radius_km"] = location_cfg.get("radius_km", 75)
        postings, err = run_collector("jobbank", jobbank_rss.fetch, jb_config)
        elapsed = int((time.time() - t0) * 1000)
        console.print(f"  -> {len(postings)} postings fetched")
        all_raw.extend(postings)
        source_results.append(("jobbank", len(postings), err, elapsed))

    # --- NBjobs.ca ---
    if (not args.source or args.source == "nbjobs") and sources_cfg.get("nbjobs", {}).get("enabled"):
        console.print("\n[cyan]Fetching NBjobs.ca...")
        t0 = time.time()
        postings, err = run_collector("nbjobs", nbjobs.fetch, sources_cfg["nbjobs"])
        elapsed = int((time.time() - t0) * 1000)
        console.print(f"  -> {len(postings)} postings fetched")
        all_raw.extend(postings)
        source_results.append(("nbjobs", len(postings), err, elapsed))

    # --- GNB ERE ---
    if (not args.source or args.source == "gnb_ere") and sources_cfg.get("gnb_ere", {}).get("enabled"):
        console.print("\n[cyan]Fetching GNB ERE Portal...")
        t0 = time.time()
        postings, err = run_collector("gnb_ere", gnb_ere.fetch, sources_cfg["gnb_ere"])
        elapsed = int((time.time() - t0) * 1000)
        console.print(f"  -> {len(postings)} postings fetched")
        all_raw.extend(postings)
        source_results.append(("gnb_ere", len(postings), err, elapsed))

    # --- Irving Oil / J.D. Irving (Workday) ---
    if (not args.source or args.source == "irving") and sources_cfg.get("irving", {}).get("enabled"):
        console.print("\n[cyan]Fetching Irving Oil / J.D. Irving (Workday)...")
        t0 = time.time()
        postings, err = run_collector("irving", irving.fetch, sources_cfg["irving"])
        elapsed = int((time.time() - t0) * 1000)
        console.print(f"  -> {len(postings)} postings fetched")
        all_raw.extend(postings)
        source_results.append(("irving", len(postings), err, elapsed))

    # --- City of Saint John (njoyn) ---
    if (not args.source or args.source == "city_sj") and sources_cfg.get("city_saintjohn", {}).get("enabled"):
        console.print("\n[cyan]Fetching City of Saint John (njoyn)...")
        t0 = time.time()
        postings, err = run_collector("city_sj", city_saintjohn.fetch, sources_cfg["city_saintjohn"])
        elapsed = int((time.time() - t0) * 1000)
        console.print(f"  -> {len(postings)} postings fetched")
        all_raw.extend(postings)
        source_results.append(("city_sj", len(postings), err, elapsed))

    # --- NB Power (PeopleSoft) ---
    if (not args.source or args.source == "nbpower") and sources_cfg.get("nbpower", {}).get("enabled"):
        console.print("\n[cyan]Fetching NB Power careers...")
        t0 = time.time()
        postings, err = run_collector("nbpower", nbpower.fetch, sources_cfg["nbpower"])
        elapsed = int((time.time() - t0) * 1000)
        console.print(f"  -> {len(postings)} postings fetched")
        all_raw.extend(postings)
        source_results.append(("nbpower", len(postings), err, elapsed))

    # --- Email Ingestion ---
    if (not args.source or args.source == "email") and sources_cfg.get("email_ingestion", {}).get("enabled"):
        console.print("\n[cyan]Reading email alerts...")
        gmail_user = os.getenv("GMAIL_USER", "")
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
        if gmail_user and gmail_pass:
            t0 = time.time()

            def email_fetch(cfg):
                return email_ingestion.fetch(cfg, gmail_user, gmail_pass)

            postings, err = run_collector("email", email_fetch, sources_cfg["email_ingestion"])
            elapsed = int((time.time() - t0) * 1000)
            console.print(f"  -> {len(postings)} postings from email")
            all_raw.extend(postings)
            source_results.append(("email", len(postings), err, elapsed))
        else:
            console.print("  [yellow]Skipping email: GMAIL_USER or GMAIL_APP_PASSWORD not set")

    # --- Stage 1: keyword classify everything ---
    console.print(f"\n[green]Classifying {len(all_raw)} postings...")
    for p in all_raw:
        result = classifier.classify(
            p.get("title", ""),
            p.get("description_snippet", "") or "",
            p.get("raw_category", "") or "",
        )
        p.update(result)

    # --- Stage 2: LLM classify borderline postings ---
    if os.getenv("ANTHROPIC_API_KEY"):
        flagged_posts = [p for p in all_raw if p.get("flagged_for_review")]
        if flagged_posts:
            console.print(f"[cyan]LLM classifying {len(flagged_posts)} borderline postings...")
            for i in range(0, len(flagged_posts), 30):
                batch = flagged_posts[i:i + 30]
                llm_classifier.classify_batch(batch)
            console.print(f"  -> Done")
    else:
        console.print("[yellow]  ANTHROPIC_API_KEY not set — skipping LLM classification")

    # --- Process all collected postings ---
    console.print(f"[green]Writing to database...")
    stats = process_postings(all_raw, dry_run)

    # --- Mark removed postings ---
    removed_count = 0
    if not dry_run:
        removed_count = db.mark_removed_postings(DB_PATH)

    # --- Print summary table ---
    table = Table(title="Collection Summary")
    table.add_column("Source")
    table.add_column("Fetched", justify="right")
    table.add_column("Status")
    for source, count, err, ms in source_results:
        status = f"[red]ERROR: {err[:40]}" if err else f"[green]OK ({ms}ms)"
        table.add_row(source, str(count), status)
    console.print(table)

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  New postings:     {stats['new']}")
    console.print(f"  Updated:          {stats['updated']}")
    console.print(f"  Excluded:         {stats['excluded']}")
    console.print(f"  Marked removed:   {removed_count}")
    if stats["errors"]:
        console.print(f"  [red]Errors: {len(stats['errors'])}")

    # --- Generate digest ---
    if not dry_run:
        all_errors = [e for _, _, e, _ in source_results if e] + stats["errors"]
        db.end_run(DB_PATH, run_id, {
            "total_fetched": len(all_raw),
            "total_new": stats["new"],
            "total_relevant": stats["new"] + stats["updated"],
            "total_excluded": stats["excluded"],
            "errors": all_errors,
        })

        console.print("\n[cyan]Generating digest report...")
        digest_data = db.get_digest_data(DB_PATH)
        report_path = digest.generate(digest_data, REPORTS_DIR)
        console.print(f"[bold green]Report written: {report_path}")

        gmail_user = os.getenv("GMAIL_USER", "")
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
        extra = os.getenv("DIGEST_RECIPIENTS", "")
        recipients = [r.strip() for r in extra.split(",") if r.strip()] if extra else []
        if gmail_user and gmail_pass:
            console.print("[cyan]Sending digest email...")
            if recipients:
                console.print(f"  CC: {', '.join(recipients)}")
            try:
                send_digest_email(report_path, digest_data["week"], gmail_user, gmail_pass, recipients)
                console.print("[bold green]Email sent.")
            except Exception as e:
                console.print(f"[red]Email failed: {e}")
    else:
        console.print("\n[yellow]Dry run — no DB writes, no report generated.")

    console.rule("[bold blue]Done")


if __name__ == "__main__":
    main()
