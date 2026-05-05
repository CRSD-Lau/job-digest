from pathlib import Path
from datetime import date
from jinja2 import Environment, FileSystemLoader


CATEGORY_ORDER = [
    "trades", "labour", "equipment", "warehousing",
    "maintenance", "transportation", "manufacturing",
    "construction", "municipal", "apprenticeship", "unknown",
]

CATEGORY_LABELS = {
    "trades": "Trades",
    "labour": "Labour",
    "equipment": "Equipment Operation",
    "warehousing": "Warehousing",
    "maintenance": "Maintenance",
    "transportation": "Transportation / Drivers",
    "manufacturing": "Manufacturing",
    "construction": "Construction",
    "municipal": "Municipal / Public Works",
    "apprenticeship": "Apprenticeships",
    "unknown": "Other / Unclassified",
}


def _group_by_category(postings: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for p in postings:
        cat = p.get("normalized_category") or "unknown"
        if cat == "unknown":
            continue  # unknown/unclassified shown only in flagged section
        groups.setdefault(cat, []).append(p)
    return {cat: groups[cat] for cat in CATEGORY_ORDER if cat in groups}


def _compute_deltas(curr: dict, prev: dict) -> list[dict]:
    all_cats = set(curr) | set(prev)
    rows = []
    for cat in CATEGORY_ORDER:
        if cat not in all_cats:
            continue
        c = curr.get(cat, 0)
        p = prev.get(cat, 0)
        delta = c - p
        pct = round((delta / p * 100)) if p else None
        rows.append({
            "category": CATEGORY_LABELS.get(cat, cat),
            "this_week": c,
            "last_week": p,
            "delta": delta,
            "pct": pct,
        })
    return rows


def generate(data: dict, reports_dir: str) -> str:
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=False)
    template = env.get_template("digest.md.jinja")

    grouped_new = _group_by_category(data["new_relevant"])
    deltas = _compute_deltas(data["curr_counts"], data["prev_counts"])

    total_relevant = sum(data["curr_counts"].values())
    total_new = len(data["new_relevant"])
    total_removed = len(data["removed"])
    total_reposted = len(data["reposted"])

    rendered = template.render(
        week=data["week"],
        prev_week=data["prev_week"],
        generated=date.today().isoformat(),
        total_relevant=total_relevant,
        total_new=total_new,
        total_removed=total_removed,
        total_reposted=total_reposted,
        grouped_new=grouped_new,
        category_labels=CATEGORY_LABELS,
        deltas=deltas,
        removed=data["removed"],
        reposted=data["reposted"],
        top_companies=data["top_companies"],
        top_titles=data["top_titles"],
        wage_data=data["wage_data"],
        excluded=data["excluded"],
        flagged=data["flagged"],
    )

    out_dir = Path(reports_dir)
    out_dir.mkdir(exist_ok=True)
    report_path = out_dir / f"{data['week']}-digest.md"
    report_path.write_text(rendered, encoding="utf-8")
    return str(report_path)
