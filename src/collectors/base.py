from typing import TypedDict


class RawPosting(TypedDict, total=False):
    posting_id: str        # required
    source: str            # required
    title: str             # required
    source_url: str
    company: str
    location: str
    date_posted: str       # ISO date string YYYY-MM-DD or None
    employment_type: str
    wage_raw: str
    description_snippet: str
    raw_category: str
