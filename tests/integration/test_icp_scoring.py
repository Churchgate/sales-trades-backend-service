import httpx
import pytest
import respx

from app.models.lead import Lead
from app.services import icp_scoring


def _lead(**overrides) -> Lead:
    data = {
        "campaign_id": 1,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@energyco.com",
        "phone": "+2348012345678",
        "company": "Energy Co",
        "job_title": "VP Operations",
        "responses": {
            "enrichment": {
                "industry": "oil & gas",
                "estimated_num_employees": 5000,
                "organization_revenue_printed": "500M",
                "country": "Nigeria",
                "city": "Lagos",
                "founded_year": 2001,
                "short_description": "A large Nigerian energy company.",
                "keywords": ["energy", "oil"],
                "domain": "energyco.com",
            },
        },
    }
    data.update(overrides)
    return Lead(**data)


_VALID_RESPONSE = {
    "industry_score": 30,
    "financial_capacity_score": 25,
    "footprint_score": 30,
    "trigger_score": 8,
    "icp_score": 93,
    "lead_tier": "Tier 1",
    "trigger_event": "1000+ employees, Nigeria-confirmed operations",
    "rationale": "Large multinational energy company with confirmed Nigeria operations.",
}


def _openrouter_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_score_lead_parses_valid_response() -> None:
    import json

    lead = _lead()
    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(
            return_value=_openrouter_response(json.dumps(_VALID_RESPONSE))
        )
        async with httpx.AsyncClient() as client:
            result = await icp_scoring.score_lead(client, "test-key", lead)

    assert result.lead_tier == "Tier 1"
    assert result.icp_score == 93
    assert result.industry_score == 30


async def test_score_lead_strips_markdown_fences() -> None:
    import json

    lead = _lead()
    fenced = f"```json\n{json.dumps(_VALID_RESPONSE)}\n```"
    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(return_value=_openrouter_response(fenced))
        async with httpx.AsyncClient() as client:
            result = await icp_scoring.score_lead(client, "test-key", lead)

    assert result.lead_tier == "Tier 1"


async def test_score_lead_raises_on_invalid_tier() -> None:
    import json

    lead = _lead()
    bad = {**_VALID_RESPONSE, "lead_tier": "Definitely Interested"}
    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(
            return_value=_openrouter_response(json.dumps(bad))
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="unexpected lead_tier"):
                await icp_scoring.score_lead(client, "test-key", lead)


async def test_score_lead_raises_on_malformed_json() -> None:
    lead = _lead()
    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(
            return_value=_openrouter_response("not json at all")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="invalid ICP response"):
                await icp_scoring.score_lead(client, "test-key", lead)


async def test_score_lead_raises_on_out_of_range_score() -> None:
    """Pydantic's ge/le bounds catch a model that ignores the 0-30/0-25/etc.
    anchor ranges (e.g. returns 45 for a dimension capped at 30)."""
    import json

    lead = _lead()
    bad = {**_VALID_RESPONSE, "industry_score": 45}
    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(
            return_value=_openrouter_response(json.dumps(bad))
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="invalid ICP response"):
                await icp_scoring.score_lead(client, "test-key", lead)


def test_build_prompt_includes_lead_and_enrichment_data() -> None:
    lead = _lead()
    prompt = icp_scoring._build_prompt(lead)

    assert "Ada Lovelace" in prompt
    assert "VP Operations" in prompt
    assert "oil & gas" in prompt
    assert "5000" in prompt
    assert "Nigeria" in prompt


def test_build_prompt_handles_missing_enrichment() -> None:
    lead = _lead(responses={})
    prompt = icp_scoring._build_prompt(lead)

    assert "Unknown" in prompt


# --- residential vs. office rubric selection ---


def test_is_residential_only_true_for_residential_interest_alone() -> None:
    lead = _lead(interests=["Executive Residences"])
    assert icp_scoring._is_residential_only(lead) is True


def test_is_residential_only_false_for_office_interest() -> None:
    lead = _lead(interests=["Office Leasing"])
    assert icp_scoring._is_residential_only(lead) is False


def test_is_residential_only_false_for_mixed_interest() -> None:
    """Mixed office+residence interest uses the office rubric — a corporate
    decision-maker evaluating a package deal, per product decision."""
    lead = _lead(interests=["Office Leasing", "Executive Residences"])
    assert icp_scoring._is_residential_only(lead) is False


def test_is_residential_only_matches_raw_kiosk_form_values() -> None:
    """The kiosk capture path stores raw values ("residences"/"offices") not
    the canonical NOG rep-import tags ("Executive Residences"/"Office
    Leasing") — an exact-tag check would silently misroute these. Verified
    against real production data: 4 leads carry the bare "residences" value."""
    assert icp_scoring._is_residential_only(_lead(interests=["residences"])) is True
    assert icp_scoring._is_residential_only(_lead(interests=["offices"])) is False
    # "both"/"full" represent mixed intent under the raw kiosk vocabulary too
    # (see scripts/import_rep_leads.py's own _interests()) — correctly fall
    # through to the office rubric since they match neither substring alone.
    assert icp_scoring._is_residential_only(_lead(interests=["both"])) is False
    assert icp_scoring._is_residential_only(_lead(interests=["full"])) is False


def test_is_residential_only_false_for_no_interests() -> None:
    lead = _lead(interests=None)
    assert icp_scoring._is_residential_only(lead) is False


def test_build_prompt_uses_residential_dimensions_for_residential_lead() -> None:
    lead = _lead(job_title="Regional Director", interests=["Executive Residences"])
    prompt = icp_scoring._build_prompt(lead)

    assert "DIMENSION 1 — Seniority / Role Signal" in prompt
    assert "DIMENSION 3 — Relocation Signal" in prompt
    assert "DIMENSION 1 — Industry Match" not in prompt


def test_build_prompt_uses_office_dimensions_for_office_and_mixed_leads() -> None:
    office_only = _lead(interests=["Office Leasing"])
    mixed = _lead(interests=["Office Leasing", "Executive Residences"])
    for lead in (office_only, mixed):
        prompt = icp_scoring._build_prompt(lead)
        assert "DIMENSION 1 — Industry Match" in prompt
        assert "DIMENSION 1 — Seniority / Role Signal" not in prompt


def test_system_prompt_selection_matches_rubric() -> None:
    residential = _lead(interests=["Executive Residences"])
    office = _lead(interests=["Office Leasing"])

    assert "INDIVIDUAL housing decision" in icp_scoring._system_prompt_for(residential)
    assert "INDIVIDUAL housing decision" not in icp_scoring._system_prompt_for(office)


async def test_score_lead_sets_rubric_field() -> None:
    import json

    residential = _lead(interests=["Executive Residences"])
    office = _lead(interests=["Office Leasing"])

    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(
            return_value=_openrouter_response(json.dumps(_VALID_RESPONSE))
        )
        async with httpx.AsyncClient() as client:
            residential_result = await icp_scoring.score_lead(client, "test-key", residential)
            office_result = await icp_scoring.score_lead(client, "test-key", office)

    assert residential_result.rubric == "residential"
    assert office_result.rubric == "office"


async def test_score_lead_ignores_llm_supplied_rubric_field() -> None:
    """rubric is set deterministically in Python, never trusted from the
    model's JSON — even if the model somehow echoed one back, our own
    selection (based on lead.interests) must win."""
    import json

    lead = _lead(interests=["Executive Residences"])
    spoofed = {**_VALID_RESPONSE, "rubric": "office"}
    with respx.mock(base_url="https://openrouter.ai") as router:
        router.post("/api/v1/chat/completions").mock(
            return_value=_openrouter_response(json.dumps(spoofed))
        )
        async with httpx.AsyncClient() as client:
            result = await icp_scoring.score_lead(client, "test-key", lead)

    assert result.rubric == "residential"
