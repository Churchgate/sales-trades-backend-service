"""Engagement / intent score for a captured lead — the dashboard ranking signal.

A booth lead leaves several intent signals at capture: how many materials they
asked for, which topics interest them, whether they booked an inspection, how
soon they want to act, and whether they opted into marketing. This rolls those
into a single 0–100 score so the dashboard can rank "who is hottest" at a glance,
and so the request→delivery loop (did they ask for a pack, did we deliver it) is
visible per lead.

Weights are a deliberately simple, transparent starting point — tune the
constants below as the sales team learns what actually predicts conversion.
"""

from app.models.lead import PACK_SENT, Lead

# --- tunable weights ---
_PER_MATERIAL = 4  # each requested document (content demand)
_PER_INTEREST = 6  # each interest topic selected
_INSPECTION = 30  # booked a private inspection — strongest in-person intent
_MARKETING_OPT_IN = 8  # agreed to ongoing contact
_PACK_DELIVERED = 6  # we successfully fulfilled their request (loop closed)
_MAX_SCORE = 100

# Decision timing → points. Matched case-insensitively by substring so both the
# canonical vocabulary ("Immediate", "0-3 months", "Future") and the kiosk form's
# wording ("Immediately", "Within 3 months", "Researching") score sensibly.
_TIMING_POINTS: list[tuple[str, int]] = [
    ("immediat", 25),
    ("today", 25),
    ("tomorrow", 22),
    ("0-3", 18),
    ("within 3", 18),
    ("this week", 18),
    ("3-6", 12),
    ("3–6", 12),
    ("6-12", 6),
    ("6–12", 6),
    ("flexible", 4),
    ("future", 2),
    ("research", 2),
]


def _timing_points(timing: str | None) -> int:
    if not timing:
        return 0
    needle = timing.strip().lower()
    for token, points in _TIMING_POINTS:
        if token in needle:
            return points
    return 0


def engagement_score(lead: Lead) -> int:
    """0–100 intent score for ranking leads on the dashboard."""
    score = 0
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
