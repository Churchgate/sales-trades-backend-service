"""ICP (Ideal Customer Profile) fit scoring via OpenRouter.

Ports the 4-dimension rubric from the retired n8n "Lead analysis agent"
workflow (`01_News watcher agent`) — that scoring logic was sound; it was
just pointed at the wrong input (random news articles, via a broken
duplicate-checking pipeline that failed ~87% of the time). This service
scores our own real, already-captured leads instead, using the Apollo
firmographic data from `scripts/enrich_leads.py`
(`Lead.responses['enrichment']`) in place of a news article.

Two framing changes from the original rubric, since a captured lead isn't a
news story about a company that *might* expand into Nigeria:
- "Nigeria Market Intent" becomes "Nigeria/regional footprint" — does the
  company already operate here, or are they a foreign visitor scouting the
  market? (A lead showing up at a Nigeria-based event already signals some
  intent; this dimension differentiates local operators from first-time
  scouts, rather than guessing at expansion plans from a headline.)
- "High-Value Triggers" scores COMPANY-level signals from Apollo (funding,
  headcount growth, multi-site presence) — not the individual's own
  behaviour (opens/clicks/tour requests), which is `engagement_score`'s job
  (see lead_scoring.py). Keeping these separate is deliberate: Phase 3
  combines them as `priority = engagement × ICP fit`, and conflating them
  here would double-count the same signal.

WTC Abuja sells two different products, and the office rubric above only
makes sense for one of them — a lead interested in "Executive Residences"
is an individual buying/renting housing, not a company evaluating office
space. Apollo enrichment describes their EMPLOYER, not their personal
finances, so a second rubric reinterprets the same company data as an
income/affluence PROXY (job-title seniority, employer prestige) rather than
"will this company lease space". One dimension is a deliberate inversion of
the office rubric: for office tenants, no confirmed Nigeria presence is a
weak signal (an unconfirmed foreign scout); for residences, a senior foreign
executive with no current Nigeria presence is often the STRONGEST lead — the
person who needs corporate housing *because* they're relocating here.

A lead interested in both products (tags like "both"/"full") uses the office
rubric — evaluating both is fundamentally a corporate decision-maker sizing
up a package deal, and residential interest is noted as cross-sell upside in
the rationale rather than scored separately.

Scored once per lead (via scripts/score_leads_icp.py), not recomputed per
request like engagement_score, since it costs an LLM call.
"""

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.models.lead import Lead

logger = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# The original n8n workflow used anthropic/claude-sonnet-4, but this task is
# strict rule-following — fixed numeric anchor points and a mandatory tier
# mapping (score >= 75 -> Tier 1, etc.), not open-ended reasoning — so a
# cheaper model in the same family gives equivalent reliability at roughly
# 1/10th the cost. Revisit if tier assignments look inconsistent in practice.
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

VALID_TIERS = {"Tier 1", "Tier 2", "Tier 3", "Skip"}

# Substring match (case-insensitive), not an exact-tag check — interests
# aren't always the canonical "Executive Residences"/"Office Leasing" tags.
# Some capture paths (kiosk form) store raw values like "residences"/"offices"
# /"both"/"full" verbatim (see scripts/import_rep_leads.py's own _interests(),
# which uses the same "resid"/"office" substring convention). An exact-match
# check would silently miss those and route residential leads to the wrong
# rubric — caught by checking real production data before shipping this.
_RESIDENTIAL_SUBSTRING = "resid"
_OFFICE_SUBSTRING = "offic"

# --- shared: the lead/company data block is identical for both rubrics ---

_LEAD_DATA_TEMPLATE = """LEAD:
Name: {name}
Job title: {job_title}
Company (self-reported): {company}
Interests selected: {interests}
Materials requested: {requested_materials}
Decision timing: {timing}

COMPANY DATA (from Apollo, may be partial or absent):
Industry: {industry}
Estimated employees: {estimated_num_employees}
Annual revenue: {annual_revenue}
Country: {country}
City: {city}
Founded: {founded_year}
Description: {short_description}
Keywords: {keywords}
"""

_TIER_AND_OUTPUT_TEMPLATE = """
---

LEAD TIER — MANDATORY MAPPING
Calculate total_score = sum of all four dimensions.
Assign lead_tier strictly as follows. Do not override.

  Tier 1 -> 75-100  (Hot lead - immediate outreach)
  Tier 2 -> 55-74   (Warm lead - nurture sequence)
  Tier 3 -> 40-54   (Monitor - long-term pipeline)
  Skip   -> 0-39    (Disqualified - no relevance)

---

RATIONALE
Write 2-4 sentences explaining the score. Reference only evidence present in
the data above. Explain the dominant factor driving the tier assignment.
No sales language. No speculation.

---

OUTPUT - STRICT JSON ONLY:
{{
  "industry_score": 0,
  "financial_capacity_score": 0,
  "footprint_score": 0,
  "trigger_score": 0,
  "icp_score": 0,
  "lead_tier": "",
  "trigger_event": "",
  "rationale": ""
}}"""

# --- office rubric (company evaluating commercial space) ---

_OFFICE_SYSTEM_PROMPT = """You are a senior commercial intelligence analyst for WTC Abuja — a \
premium mixed-use real estate and business hub in Nigeria's Federal Capital Territory.

Your function is dual:
1. Assess the company behind an already-captured sales lead
2. Score the lead against WTC Abuja's Ideal Customer Profile (ICP) for its \
Grade-A office space

WTC Abuja's target office tenants require one or more of:
- Premium grade-A office space (1,000-50,000 sqft)
- Regional or continental headquarters
- Long-term corporate presence (3+ year horizon)
- High-quality mixed-use business environment

This lead already attended a WTC Abuja event or visited the website and gave us \
their details directly — they are not a cold prospect sourced from news. Score the \
COMPANY's fit; do not re-score the individual's personal engagement (opens, clicks, \
tour requests) — that is tracked separately.

Be conservative and evidence-based. Never infer beyond what the data states. If \
company data is thin or missing, score those dimensions low rather than guessing.
Return ONLY valid JSON. No markdown, no explanation, no preamble."""

_OFFICE_TASK_TEMPLATE = """
---

TASK — ICP SCORING (OFFICE)
Score the lead on four dimensions. Apply the anchor points strictly.

DIMENSION 1 — Industry Match (0-30)
Does this company's sector align with WTC Abuja's established tenant profile?

  30 — Perfect fit: multinational energy, finance, infrastructure, technology,
       telecoms, development finance, sovereign/quasi-sovereign
  20 — Strong fit: professional services, logistics, healthcare, real estate,
       regional enterprise
  10 — Partial fit: NGO, media, education, public sector
   0 — Poor fit: retail, hospitality, agriculture, individual, SMB with no
       regional presence, OR no company data available

DIMENSION 2 — Financial Capacity (0-25)
Can this entity sustain premium commercial real estate costs long-term?

  25 — Confirmed: multinational, listed company, large revenue (>$50m),
       1000+ employees
  18 — Probable: mid-large regional company, $10m-$50m revenue, 100-999 employees
  10 — Possible: smaller regional company, some revenue signal, <100 employees
   0 — Unlikely: no financial signal present, or clearly early-stage/underfunded

DIMENSION 3 — Nigeria/Regional Footprint (0-30)
Does this company already operate in Nigeria or West Africa, or are they a
foreign visitor scouting the market?

  30 — Confirmed Nigeria/Abuja operations (country/city data confirms it, or
       job title implies a Nigeria-based role)
  22 — Confirmed West Africa or pan-African operations, Nigeria not specified
  14 — Multinational with African presence elsewhere, Nigeria/WA not confirmed
   6 — No confirmed African presence — a first-time visitor/scout
   0 — No signal either way

DIMENSION 4 — Company-Level Growth Signals (0-15)
Concrete COMPANY (not individual) signals of expanding space demand — from
the Apollo data only (funding, headcount growth, multi-site scale). Do NOT
score the lead's own opens/clicks/tour request here.

  15 — Multiple signals: e.g. large recent headcount growth + multi-country
       presence
   8 — Single signal: notable size/scale or growth indicator present
   0 — No company-level growth signal available

- trigger_event: The specific company-level signal driving the score (e.g.
  "1000+ employees, multinational presence"). If score is 0, state
  "None identified"."""

# --- residential rubric (individual seeking executive housing) ---

_RESIDENTIAL_SYSTEM_PROMPT = """You are a senior residential intelligence analyst for WTC Abuja's \
Executive Residences — premium corporate housing for senior executives, expatriates, and \
diplomatic staff in Nigeria's Federal Capital Territory.

This is an INDIVIDUAL housing decision, not a corporate one. We have no direct data on this \
person's personal finances — score using PROXIES: job-title seniority, and their employer's \
profile as an income/housing-allowance signal (large multinational employers commonly provide \
housing packages to relocating senior staff).

This lead already attended a WTC Abuja event or visited the website and gave us their details \
directly — they are not a cold prospect sourced from news. Score the INDIVIDUAL's residential \
fit; do not re-score their personal engagement (opens, clicks, tour requests) — that is tracked \
separately.

Be conservative and evidence-based. Never infer beyond what the data states. If data is thin or \
missing, score those dimensions low rather than guessing.
Return ONLY valid JSON. No markdown, no explanation, no preamble."""

_RESIDENTIAL_TASK_TEMPLATE = """
---

TASK — ICP SCORING (RESIDENTIAL)
Score the lead on four dimensions. Apply the anchor points strictly.

DIMENSION 1 — Seniority / Role Signal (0-30)
Does the job title indicate a senior role consistent with executive housing needs?

  30 — C-suite, Managing Director, Country Head, Ambassador/senior diplomat
  20 — VP, Director, Senior Manager, Regional Head
  10 — Manager, mid-level professional
   0 — Junior/individual contributor, unclear, or no title given

DIMENSION 2 — Employer Affluence Proxy (0-25)
Use the company data as a proxy for likely income/housing allowance — NOT as
a signal of whether the company itself would lease space.

  25 — Multinational or large employer (1000+ employees, or clear large revenue)
  18 — Mid-large regional employer
  10 — Smaller company, some signal present
   0 — No employer data, or clearly small/early-stage

DIMENSION 3 — Relocation Signal (0-30)
IMPORTANT — this is the INVERSE of a typical corporate-footprint check. A
senior professional based OUTSIDE Nigeria is often the STRONGEST residential
lead: they are the one who needs to relocate and requires housing. A
long-established local resident has a real but lower-urgency need.

  30 — Senior/international professional (dimension-1 role of VP or above)
       based outside Nigeria/West Africa, or explicitly relocating —
       the classic incoming-expat executive housing need
  20 — Confirmed Nigeria-based professional taking on a new senior role, or
       transferring within a multinational — likely upgrading housing
  10 — Long-standing Nigeria-based professional, stable role — some
       residential interest but lower urgency
   0 — No seniority or company signal at all — cannot determine housing need

DIMENSION 4 — Personal Urgency Signals (0-15)
Signals of near-term housing intent, from this lead's own submission only.

  15 — Requested a private inspection/tour AND immediate/short decision timing
   8 — One of: requested inspection, OR immediate timing, OR explicit
       "Executive Residences" interest with no competing office interest
   0 — No urgency signal

- trigger_event: The specific signal driving the score (e.g. "Senior expat
  role, no confirmed Nigeria presence — likely relocation"). If score is 0,
  state "None identified"."""


class ICPResult(BaseModel):
    industry_score: int = Field(ge=0, le=30)
    financial_capacity_score: int = Field(ge=0, le=25)
    footprint_score: int = Field(ge=0, le=30)
    trigger_score: int = Field(ge=0, le=15)
    icp_score: int = Field(ge=0, le=100)
    lead_tier: str
    trigger_event: str
    rationale: str
    # Set deterministically in score_lead (never trusted from the LLM) so
    # downstream consumers know which rubric's dimension semantics apply —
    # e.g. industry_score means "industry fit" for office, but is repurposed
    # to mean "seniority" for residential. Same field names, same JSON
    # schema, so Phase 3 doesn't need to branch on which rubric ran.
    rubric: Literal["office", "residential"] = "office"


def _is_residential_only(lead: Lead) -> bool:
    """True only when residential interest is expressed with NO competing
    office interest — mixed/"both"/"full" leads use the office rubric (a
    corporate decision-maker evaluating a package deal), per product
    decision. Falls through to the office rubric (False) for ambiguous
    values like "both"/"full"/"general" — they don't match either substring,
    which is the correct outcome since they represent mixed or unclear
    intent, not residential-only."""
    interests_lower = [i.lower() for i in (lead.interests or [])]
    has_residential = any(_RESIDENTIAL_SUBSTRING in i for i in interests_lower)
    has_office = any(_OFFICE_SUBSTRING in i for i in interests_lower)
    return has_residential and not has_office


def _format_lead_data(lead: Lead) -> str:
    enrichment: dict[str, Any] = (lead.responses or {}).get("enrichment") or {}
    return _LEAD_DATA_TEMPLATE.format(
        name=f"{lead.first_name} {lead.last_name}".strip(),
        job_title=lead.job_title or "Unknown",
        company=lead.company or "Unknown",
        interests=", ".join(lead.interests or []) or "None stated",
        requested_materials=", ".join(lead.requested_materials or []) or "None",
        timing=lead.timing or "Not stated",
        industry=enrichment.get("industry") or "Unknown",
        estimated_num_employees=enrichment.get("estimated_num_employees") or "Unknown",
        annual_revenue=(
            enrichment.get("organization_revenue_printed")
            or enrichment.get("annual_revenue_printed")
            or "Unknown"
        ),
        country=enrichment.get("country") or "Unknown",
        city=enrichment.get("city") or "Unknown",
        founded_year=enrichment.get("founded_year") or "Unknown",
        short_description=enrichment.get("short_description") or "None available",
        keywords=", ".join(enrichment.get("keywords") or []) or "None",
    )


def _build_prompt(lead: Lead) -> str:
    task = _RESIDENTIAL_TASK_TEMPLATE if _is_residential_only(lead) else _OFFICE_TASK_TEMPLATE
    return _format_lead_data(lead) + task + _TIER_AND_OUTPUT_TEMPLATE


def _system_prompt_for(lead: Lead) -> str:
    return _RESIDENTIAL_SYSTEM_PROMPT if _is_residential_only(lead) else _OFFICE_SYSTEM_PROMPT


def _parse_json_response(content: str) -> dict[str, Any]:
    """The model is instructed to return pure JSON, but strip markdown code
    fences defensively in case it wraps the response anyway."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    return json.loads(text)


async def score_lead(
    client: httpx.AsyncClient, api_key: str, lead: Lead, *, model: str = DEFAULT_MODEL
) -> ICPResult:
    """Score one lead's fit — company (office) or individual (residential),
    selected automatically from `lead.interests`. Raises on API error or
    invalid response — callers (scripts/score_leads_icp.py) catch and skip
    per-lead so one bad response doesn't fail the whole batch."""
    rubric: Literal["office", "residential"] = (
        "residential" if _is_residential_only(lead) else "office"
    )
    resp = await client.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": _system_prompt_for(lead)},
                {"role": "user", "content": _build_prompt(lead)},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]

    try:
        data = _parse_json_response(content)
        result = ICPResult(**{**data, "rubric": rubric})
    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
        raise ValueError(
            f"invalid ICP response for lead {lead.id}: {exc}\n{content[:300]}"
        ) from exc

    if result.lead_tier not in VALID_TIERS:
        raise ValueError(f"lead {lead.id}: unexpected lead_tier {result.lead_tier!r}")
    return result
