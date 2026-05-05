import hashlib
import re


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def make_hash(title: str, company: str | None = "", location: str | None = "") -> str:
    """Produce a stable fingerprint for deduplication across sources."""
    parts = [
        _normalize(title or ""),
        _normalize(company or ""),
        _normalize(location or ""),
    ]
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]
