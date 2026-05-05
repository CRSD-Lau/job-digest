# Job Digest

Private Saint John job-posting digest automation.

The project collects jobs from configured sources, keeps local history in SQLite, and can generate/send a weekly digest email.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` locally. Do not commit it.

## Run

```powershell
python run_weekly.py
```

## Local State

These stay local and are intentionally ignored:

- `.env`
- `.venv/`
- `data/*.db`
- `logs/`
- `reports/*.md`
- `reports/*.html`
