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
