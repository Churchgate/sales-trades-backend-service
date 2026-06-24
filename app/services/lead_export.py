"""CSV export of a campaign's leads — the guaranteed failsafe (brief §9, §23).

Stable column order so nightly Freshsales imports stay predictable regardless of
whether live CRM sync is enabled.
"""

import csv
import io

from app.models.lead import Lead

_FIELDS = [
    "id",
    "captured_at",
    "created_at",
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "job_title",
    "source",
    "device_id",
    "timing",
    "interests",
    "requested_materials",
    "tags",
    "inspection_requested",
    "inspection_type",
    "marketing_opt_in",
    "consent_status",
    "consent_at",
    "crm_sync_status",
    "crm_contact_id",
]


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def leads_to_csv(leads: list[Lead]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_FIELDS)
    for lead in leads:
        writer.writerow([_fmt(getattr(lead, field)) for field in _FIELDS])
    return buf.getvalue()
