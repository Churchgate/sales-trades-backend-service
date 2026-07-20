"""Engagement / intent score for a captured lead — the dashboard ranking signal.

A booth lead leaves signals at two moments: what they ticked on the capture
form (materials, interests, inspection, timing), and — critically — what they
actually did after we delivered their digital pack (opens, document clicks,
how recently). The latter is the stronger signal: a prospect who opened the
pack five times and clicked the floorplates last week is a hotter lead than
one who merely checked "immediate" on a form and never engaged again, but the
original version of this scorer only ever looked at the form — pack opens,
click-throughs and recency were tracked on the model
(`pack_opened_count`/`pack_opened_at`/`pack_clicked_materials`, populated by
`services/email_event_ingest.py` from the SendGrid webhook) but never scored,
so the dashboard's default sort buried the most engaged prospects.

Weights are a deliberately simple, transparent starting point — tune the
constants below as the sales team learns what actually predicts conversion.
"""

from datetime import UTC, datetime

from app.models.lead import PACK_SENT, Lead

# --- behavioural signals (post-delivery — the dominant factor) ---

# (minimum opens, points) — largest threshold first; first match wins. Extra
# tiers above 5 exist because a flat "5+" bucket scored a lead who opened the
# pack 34 times identically to one who opened it 5 times — under-rewarding
# exactly the extreme-engagement case this rebalancing exists to surface.
_OPEN_TIERS: list[tuple[int, int]] = [
    (25, 40),
    (15, 34),
    (10, 30),
    (5, 26),
    (3, 20),
    (2, 14),
    (1, 8),
]
_PER_CLICK = 12
_MAX_CLICK_POINTS = 30  # ~3 distinct documents clicked

# (max days since last open, points) — smallest threshold first; first match
# wins. Rewards a prospect who engaged recently over one who's gone cold, but
# tuned to a commercial-real-estate sales cycle (weeks/months), not a SaaS
# trial cycle — a touch from 10 days ago is still fresh, not stale.
_RECENCY_TIERS: list[tuple[int, int]] = [
    (7, 15),
    (30, 10),
    (60, 5),
    (90, 2),
]

# --- form-submission signals (weaker than demonstrated engagement, so these
# weights are intentionally smaller than the pre-engagement-aware version) ---
_PER_MATERIAL = 2  # each requested document (content demand)
_PER_INTEREST = 4  # each interest topic selected
_INSPECTION = 18  # booked a private inspection — strong, but a ticked box alone
_MARKETING_OPT_IN = 5  # agreed to ongoing contact
_PACK_DELIVERED = 3  # we successfully fulfilled their request (loop closed)
_MAX_SCORE = 100

# Decision timing → points. Matched case-insensitively by substring so both the
# canonical vocabulary ("Immediate", "0-3 months", "Future") and the kiosk form's
# wording ("Immediately", "Within 3 months", "Researching") score sensibly.
_TIMING_POINTS: list[tuple[str, int]] = [
    ("immediat", 18),
    ("today", 18),
    ("tomorrow", 15),
    ("0-3", 12),
    ("within 3", 12),
    ("this week", 12),
    ("3-6", 8),
    ("3–6", 8),
    ("6-12", 4),
    ("6–12", 4),
    ("flexible", 2),
    ("future", 1),
    ("research", 1),
]


def _timing_points(timing: str | None) -> int:
    if not timing:
        return 0
    needle = timing.strip().lower()
    for token, points in _TIMING_POINTS:
        if token in needle:
            return points
    return 0


def _open_points(opens: int) -> int:
    for threshold, points in _OPEN_TIERS:
        if opens >= threshold:
            return points
    return 0


def _click_points(clicked_materials: list[str] | None) -> int:
    return min(_PER_CLICK * len(clicked_materials or []), _MAX_CLICK_POINTS)


def _recency_points(opened_at: datetime | None, *, now: datetime | None = None) -> int:
    if opened_at is None:
        return 0
    now = now or datetime.now(UTC)
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    days_ago = (now - opened_at).total_seconds() / 86400
    for threshold, points in _RECENCY_TIERS:
        if days_ago <= threshold:
            return points
    return 0


def engagement_score(lead: Lead, *, now: datetime | None = None) -> int:
    """0–100 intent score for ranking leads on the dashboard.

    Behavioural signals (opens/clicks/recency) are weighted more heavily than
    form-submission signals — see module docstring for why.
    """
    score = 0
    # Behavioural (post-delivery)
    score += _open_points(lead.pack_opened_count or 0)
    score += _click_points(lead.pack_clicked_materials)
    score += _recency_points(lead.pack_opened_at, now=now)
    # Form submission
    score += _PER_MATERIAL * len(lead.requested_materials or [])
    score += _PER_INTEREST * len(lead.interests or [])
    score += _timing_points(lead.timing)
    if lead.inspection_requested:
        score += _INSPECTION
    if lead.marketing_opt_in:
        score += _MARKETING_OPT_IN
    if lead.pack_delivery_status == PACK_SENT:
        score += _PACK_DELIVERED
    return min(score, _MAX_SCORE)


def pack_fulfilled(lead: Lead) -> bool:
    """True when this lead asked for a digital pack and we delivered it."""
    return bool(lead.requested_materials) and lead.pack_delivery_status == PACK_SENT
