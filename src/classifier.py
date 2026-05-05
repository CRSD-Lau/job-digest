import re
import yaml
from pathlib import Path


_keywords: dict | None = None


def _load_keywords() -> dict:
    global _keywords
    if _keywords is None:
        path = Path(__file__).parent.parent / "config" / "keywords.yaml"
        with open(path, encoding="utf-8") as f:
            _keywords = yaml.safe_load(f)
    return _keywords


def _normalize(text: str) -> str:
    return " " + text.lower().strip() + " "


def _matches_any(text: str, phrases: list[str]) -> str | None:
    """Return the first matching phrase, or None."""
    norm = _normalize(text)
    for phrase in phrases:
        pattern = r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)"
        if re.search(pattern, norm):
            return phrase
    return None


def classify(title: str, snippet: str = "", raw_category: str = "") -> dict:
    """
    Classify a job posting using keyword rules.

    Returns dict with:
        normalized_category, is_relevant, exclusion_reason,
        classification_confidence, flagged_for_review
    """
    kw = _load_keywords()
    search_text = f"{title} {snippet} {raw_category}"

    # --- Exclusion check first ---
    for reason, phrases in kw.get("exclude", {}).items():
        match = _matches_any(search_text, phrases)
        if match:
            # Check if a strong include overrides (e.g. "forklift operator" at a food company)
            include_match, include_cat = _find_include(search_text, kw)
            if include_match and include_cat in ("trades", "equipment", "labour", "maintenance"):
                # Trades/equipment keywords beat retail/food exclusions
                return _build_result(include_cat, True, None, 0.75, False)
            return _build_result(None, False, reason, 0.88, False)

    # --- Scam flag check ---
    for phrase in kw.get("scam_flags", []):
        if _matches_any(search_text, [phrase]):
            return _build_result(None, False, "scam", 0.85, False)

    # --- Include check ---
    include_match, include_cat = _find_include(search_text, kw)
    if include_match:
        return _build_result(include_cat, True, None, 0.85, False)

    # --- No match: flag for review ---
    return _build_result("unknown", True, None, 0.40, True)


def _find_include(text: str, kw: dict) -> tuple[str | None, str | None]:
    """Return (matched_phrase, category) for the first include match."""
    for category, phrases in kw.get("include", {}).items():
        match = _matches_any(text, phrases)
        if match:
            return match, category
    return None, None


def _build_result(category: str | None, is_relevant: bool,
                  exclusion_reason: str | None, confidence: float,
                  flagged: bool) -> dict:
    return {
        "normalized_category": category,
        "is_relevant": is_relevant,
        "exclusion_reason": exclusion_reason,
        "classification_confidence": confidence,
        "flagged_for_review": flagged,
    }
