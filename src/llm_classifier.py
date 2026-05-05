"""
LLM-assisted classification using Claude API.
Called only for postings that the keyword classifier flagged for review
(normalized_category = 'unknown', confidence < 0.5).

Cost: ~$0.001 per batch of 50 postings using haiku.
"""
import json
import os
import anthropic

SYSTEM_PROMPT = """You are a job posting classifier for a labour market digest focused on Saint John, New Brunswick, Canada.

Your job is to classify whether each posting is RELEVANT or EXCLUDED for a friend looking for practical, hands-on, non-customer-facing work.

RELEVANT categories (include these):
- Trades: welder, electrician, plumber, pipefitter, millwright, HVAC, carpenter, roofer, boilermaker, ironworker, gasfitter, red seal
- Labour: general labour, construction worker, site worker, flagperson, landscaping labour
- Equipment operation: heavy equipment, crane, excavator, forklift, loader, dozer, skid steer
- Warehousing: warehouse, order picker, shipping/receiving, material handler, distribution
- Maintenance: building maintenance, facilities, custodian, groundskeeper, property maintenance
- Transportation: truck driver, delivery driver, Class 1/3, dispatcher, logistics
- Manufacturing: production worker, machine operator, assembly, fabricator, machinist, CNC
- Construction: site superintendent, foreman, safety officer (on-site), estimator
- Municipal/public works: public works, city crew, parks, water/wastewater, road maintenance, transit operator
- Apprenticeship: apprentice programs, pre-apprenticeship, registered apprentice

EXCLUDED (do not include):
- Retail: cashier, sales associate, store clerk, merchandise
- Food service: server, cook, barista, front of house, drive-through crew
- Customer service: CSR, call centre, help desk, phone support, client service rep
- Sales: sales rep, commission, door-to-door, insurance sales, financial advisor, MLM
- Professional/office: nurse, doctor, lawyer, social worker, accountant, HR, teacher, IT developer, marketing, translator, psychologist
- Scam/vague: "be your own boss", "unlimited earnings", network marketing

BORDERLINE GUIDANCE:
- Security officer at a site/facility = RELEVANT (physical, non-retail)
- Security officer at a park/attraction = EXCLUDED (customer-facing)
- Mechanic/technician = RELEVANT
- Bridge worker, highway worker, grounds worker = RELEVANT
- Cleaning attendant at a government facility = RELEVANT
- Inspector (trades/safety/building) = RELEVANT
- Correctional officer = EXCLUDED (professional/law enforcement)
- Student positions at parks/tourism = EXCLUDED
- Operations worker (government inventory/roads) = RELEVANT"""


def classify_batch(postings: list[dict]) -> list[dict]:
    """
    Classify a batch of flagged postings using Claude.
    Returns the same list with updated classification fields.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return postings  # skip silently if no key

    client = anthropic.Anthropic(api_key=api_key)

    # Build the user message with all postings as a JSON list
    items = []
    for p in postings:
        items.append({
            "id": p.get("posting_id", ""),
            "title": p.get("title", ""),
            "snippet": (p.get("description_snippet") or "")[:300],
            "company": p.get("company") or "",
            "location": p.get("location") or "",
            "source_category": p.get("raw_category") or "",
        })

    user_msg = f"""Classify each of these {len(items)} job postings.

Postings:
{json.dumps(items, indent=2)}

For each posting return a JSON array with one object per posting in the same order:
{{
  "id": "<posting_id>",
  "is_relevant": true or false,
  "category": "trades|labour|equipment|warehousing|maintenance|transportation|manufacturing|construction|municipal|apprenticeship|exclude",
  "exclusion_reason": null or "retail|food_service|customer_service|sales|professional_office|scam",
  "confidence": 0.0 to 1.0,
  "reasoning": "one short sentence"
}}

Return ONLY the JSON array, no other text."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        results = json.loads(raw)

        # Map results back to postings by id
        result_map = {r["id"]: r for r in results}

        for p in postings:
            pid = p.get("posting_id", "")
            r = result_map.get(pid)
            if not r:
                continue

            p["is_relevant"] = r.get("is_relevant", False)
            p["normalized_category"] = r.get("category") if r.get("is_relevant") else None
            p["exclusion_reason"] = r.get("exclusion_reason")
            p["classification_confidence"] = r.get("confidence", 0.7)
            p["flagged_for_review"] = False  # LLM made a decision
            p["llm_classified"] = True

    except Exception as e:
        print(f"  [llm_classifier] Error: {e}")

    return postings


def needs_llm(posting: dict) -> bool:
    """Return True if this posting should be sent to the LLM for classification."""
    return (
        posting.get("flagged_for_review", False)
        and posting.get("classification_confidence", 1.0) < 0.5
    )
