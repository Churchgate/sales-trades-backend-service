"""Bulk-import rep-collected leads from the event "DAILY TEAM REPORT" sheet.

At the event, sales reps walked the floor collecting business cards / ID cards and
logged prospects into a shared Google Sheet — one tab per rep ("JESSICA - DAY 1",
"FAVOUR- DAY 1", …). This backfills those into the same `nog-2026` campaign as the
kiosk/QR leads so they get the digital pack and flow to Freshsales like any other
lead. The rep who captured each prospect is preserved (tag `Rep: <name>` + stored
in `responses.captured_by`), and `source` is set to `rep_upload` so these are
distinguishable from `qr`/`tablet` leads in the stats/CSV.

Guarantees the task called for:
  * a row with no email (or no name) is NOT imported — email + name are required;
  * no duplicate rows — dedup is by (campaign, email) via `lead_service`, so a
    prospect two reps both logged (or a re-run of this script) merges onto one
    lead instead of creating a second.

Pack email is NOT sent at import time: leads land `pack_delivery_status=pending`
(the requested materials are the standard NOG pack), so once SendGrid billing is
restored the normal delivery job / "Resend packs" sends them. This script never
emails anyone itself.

Usage (run from backend/, against prod via Railway):

    # preview only — parses the sheet, prints what WOULD import, touches nothing:
    railway run uv run python scripts/import_rep_leads.py

    # actually write to the database:
    railway run uv run python scripts/import_rep_leads.py --commit

    # a different sheet / already-downloaded xlsx:
    railway run uv run python scripts/import_rep_leads.py --source <sheet-url|file.xlsx>
"""

import argparse
import asyncio
import io
import re
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.repositories import campaigns_repo, leads_repo  # noqa: E402
from app.schemas.campaigns import LeadCreateRequest  # noqa: E402
from app.services import lead_service  # noqa: E402

CAMPAIGN_SLUG = "nog-2026"
DEFAULT_SHEET_ID = "1ll1fLd4oEiOUHJShk-bPl4_UstjCX_3cJgAF89RcLm8"
# Standard NOG digital-pack materials (must match the nog-2026 campaign config's
# `materials` labels, or nothing is deliverable). Every imported lead is queued to
# receive the full pack.
PACK_MATERIALS = ["Corporate Prospectus", "Office Floorplates", "Residence Floorplans"]
# Day-1 capture date from the sheet (the "Date" column is a spreadsheet serial we
# don't trust cell-by-cell; the whole batch is one event day).
CAPTURED_AT = datetime(2026, 7, 7, 12, 0)
# The day this backfill was first run. Used by --fix-phones to tell the leads this
# script inserted (created on/after this day) from pre-existing kiosk leads it merged
# onto (captured during the event, 5–7 Jul), so the phone fix never touches the latter.
IMPORT_RUN_DAY = datetime(2026, 7, 8)

# Fixed column layout, identical across every rep tab (header on row 4):
#   0 Date | 1 Full Name | 2 Company | 3 Designation | 4 Email | 5 Phone
#   6 Exhibition visit? | 7 Interested in Tour | 8 Support Needed | 9 Tomorrow Priority
COL_NAME, COL_COMPANY, COL_TITLE, COL_EMAIL, COL_PHONE = 1, 2, 3, 4, 5
COL_EXHIBITION, COL_TOUR, COL_SUPPORT, COL_PRIORITY = 6, 7, 8, 9
DATA_START_ROW = 4  # rows 0-3 are title/blank/header

_HONORIFICS = {
    "dr", "engr", "mr", "mrs", "ms", "miss", "alh", "alhaji", "sir", "prof",
    "barr", "chief", "capt", "hon", "pastor", "rev", "mallam", "mal", "arc",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# --- xlsx parsing (stdlib only — Google Sheets exports every tab as one .xlsx) ---


def _download_xlsx(source: str) -> bytes:
    if source.startswith("http"):
        m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", source)
        sheet_id = m.group(1) if m else source
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
        with urllib.request.urlopen(url) as resp:  # noqa: S310 — trusted Google host
            return resp.read()
    if re.fullmatch(r"[A-Za-z0-9_-]{30,}", source):  # bare sheet id
        return _download_xlsx(f"https://docs.google.com/spreadsheets/d/{source}/edit")
    return Path(source).read_bytes()


def _col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_tabs(data: bytes) -> list[tuple[str, list[list[str]]]]:
    """Return [(tab_name, rows)] for every worksheet, cells as displayed-ish strings."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    relmap = {
        r.get("Id"): r.get("Target")
        for r in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    }
    tabs: list[tuple[str, list[list[str]]]] = []
    for sheet in wb.find(f"{_NS}sheets"):
        target = relmap[sheet.get(f"{_RNS}id")]
        tabs.append((sheet.get("name"), _read_sheet(zf, target, shared)))
    return tabs


def _read_sheet(zf: zipfile.ZipFile, target: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read("xl/" + target.lstrip("/")))
    rows: list[list[str]] = []
    for row in root.iter(f"{_NS}row"):
        cells: dict[int, str] = {}
        max_c = -1
        for c in row.findall(f"{_NS}c"):
            idx = _col_index(c.get("r"))
            t = c.get("t")
            v = c.find(f"{_NS}v")
            inline = c.find(f"{_NS}is")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif inline is not None:
                val = "".join(x.text or "" for x in inline.iter(f"{_NS}t"))
            elif v is not None:
                val = v.text or ""
            else:
                val = ""
            cells[idx] = val
            max_c = max(max_c, idx)
        rows.append([cells.get(i, "") for i in range(max_c + 1)])
    return rows


# --- field cleaning ---


def _rep_name(tab: str) -> str:
    """'FAVOUR- DAY 1' -> 'Favour'."""
    return re.split(r"\s*-?\s*DAY\b", tab, flags=re.I)[0].strip().title()


def _split_name(raw: str) -> tuple[str, str]:
    """Best-effort first/last from a single 'Full Name' cell.

    Drops leading honorifics and any trailing comma-credentials
    ('Engr.Vincent Agoha,MNSE,COREN' -> 'Vincent', 'Agoha'). A single-token name
    keeps a placeholder last name so the CRM contact (which requires one) is valid.
    """
    name = raw.split(",")[0].strip()
    name = re.sub(r"\s+", " ", name.replace(".", ". ")).strip()
    tokens = [t for t in name.split(" ") if t]
    while len(tokens) > 1 and tokens[0].rstrip(".").lower() in _HONORIFICS:
        tokens.pop(0)
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], "—"
    return tokens[0], " ".join(tokens[1:])


def _clean_email(raw: str) -> str | None:
    # Trailing punctuation slips in when a card is transcribed ('name@co.com/').
    email = raw.strip().strip("/.,;:").lower()
    if _EMAIL_RE.match(email):
        return email
    # A rep sometimes logs two emails in one cell, space-separated
    # ('a@co.com b@other.com') — take the first valid one rather than dropping
    # the whole lead.
    for token in email.split():
        token = token.strip("/.,;:")
        if _EMAIL_RE.match(token):
            return token
    return None


def _clean_phone(raw: str) -> str:
    """Best-effort normalise. NG mobiles -> local 0-format; longer numbers are
    treated as international and kept with a leading '+' (Google dropped it when it
    stored the cell as a number, e.g. '8618010563698' -> '+8618010563698')."""
    val = raw.strip()
    if not val:
        return ""
    had_plus = val.lstrip().startswith("+")
    # Google stored numeric phones as floats ('8.023529937E9'); expand to an int.
    if re.fullmatch(r"[0-9.]+E[0-9]+", val, re.I) or re.fullmatch(r"[0-9]+\.[0-9]+", val):
        try:
            val = str(int(float(val)))
        except ValueError:
            pass
    digits = re.sub(r"\D", "", val)
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("0"):  # NG local, already correct
        return digits
    if len(digits) == 10 and digits[0] in "789":  # NG mobile, lost its leading 0
        return "0" + digits
    if digits.startswith("234"):  # NG in international form
        return "+" + digits
    if had_plus or len(digits) >= 11:  # other country — keep international marker
        return "+" + digits
    return digits  # short/ambiguous — leave the digits as captured


def _interests(support: str) -> list[str]:
    s = support.lower()
    out: list[str] = []
    if "office" in s:
        out.append("Office Leasing")
    if "resid" in s:
        out.append("Executive Residences")
    return out


def _wants_tour(tour: str) -> bool:
    return tour.strip().lower().startswith("yes")


# --- row -> lead payload ---


class SkipRow(Exception):
    """Row can't/shouldn't be imported (recorded with a reason)."""


def _row_to_payload(rep: str, row: list[str]) -> LeadCreateRequest:
    row = (row + [""] * 10)[:10]
    raw_name = row[COL_NAME].strip()
    if not raw_name:
        raise SkipRow("no name")
    email = _clean_email(row[COL_EMAIL])
    if not email:
        raise SkipRow("no valid email")
    first, last = _split_name(raw_name)
    if not first:
        raise SkipRow("no name")
    company = row[COL_COMPANY].strip() or "—"  # column is NOT NULL / required
    try:
        return LeadCreateRequest(
            first_name=first[:120],
            last_name=last[:120],
            email=email,
            phone=_clean_phone(row[COL_PHONE]) or None,
            company=company[:200],
            job_title=(row[COL_TITLE].strip() or None),
            source="rep_upload",
            interests=_interests(row[COL_SUPPORT]) or None,
            requested_materials=list(PACK_MATERIALS),
            inspection_requested=_wants_tour(row[COL_TOUR]),
            consent_status=True,  # handed their card to a rep for follow-up
            captured_at=CAPTURED_AT,
            responses={
                "captured_by": rep,
                "raw_name": raw_name,
                "support_needed": row[COL_SUPPORT].strip(),
                "exhibition_visit": row[COL_EXHIBITION].strip(),
                "interested_in_tour": row[COL_TOUR].strip(),
                "tomorrow_priority": row[COL_PRIORITY].strip(),
                "import_batch": "nog-2026-rep-sheet-day1",
            },
        )
    except ValidationError as exc:  # stricter EmailStr etc. — skip, don't crash batch
        raise SkipRow("invalid email/field") from exc


def _collect(tabs: list[tuple[str, list[list[str]]]]):
    """Parse every tab into (payloads, skipped). Dedups by email, first rep wins."""
    payloads: dict[str, tuple[str, LeadCreateRequest]] = {}
    skipped: list[tuple[str, str, str]] = []  # (rep, raw_name, reason)
    merged: list[tuple[str, str]] = []  # (email, later-rep) collapsed onto first
    for tab, rows in tabs:
        rep = _rep_name(tab)
        for row in rows[DATA_START_ROW:]:
            if not any(str(c).strip() for c in row):
                continue
            raw_name = (row + [""] * 2)[COL_NAME].strip()
            try:
                payload = _row_to_payload(rep, row)
            except SkipRow as exc:
                skipped.append((rep, raw_name or "(blank)", str(exc)))
                continue
            if payload.email in payloads:
                merged.append((payload.email, rep))
                continue
            payloads[payload.email] = (rep, payload)
    return payloads, skipped, merged


# --- persistence helpers ---


def _with_rep_tag(tags: list[str] | None, rep: str) -> list[str]:
    rep_tag = f"Rep: {rep}"
    tags = list(tags or [])
    # `_derive_tags` rebuilds tags from campaign config + selections and doesn't know
    # about the rep, so the attribution tag is appended here.
    return tags if rep_tag in tags else [*tags, rep_tag]


def _augment_existing(lead, rep: str, payload: LeadCreateRequest) -> bool:
    """Attach rep attribution to an already-existing lead WITHOUT overwriting it.

    A rep-sheet row is a thinner source than a real kiosk/QR capture, so we only:
    add the `Rep: <name>` tag, record who captured it, and fill a blank phone. We
    never touch consent, marketing opt-in, materials, source, or the original
    `responses`. Returns True if anything changed (so the caller persists)."""
    changed = False
    tags = _with_rep_tag(lead.tags, rep)
    if tags != (lead.tags or []):
        lead.tags = tags
        changed = True
    resp = dict(lead.responses or {})
    if not resp.get("captured_by"):
        resp["captured_by"] = rep
        resp.setdefault("import_batch", payload.responses.get("import_batch"))
        lead.responses = resp
        changed = True
    if payload.phone and not lead.phone:  # only fill a blank — never replace a real one
        lead.phone = payload.phone
        changed = True
    return changed


# --- run ---


async def run(source: str, commit: bool) -> None:
    print(f"Reading sheet: {source}")
    tabs = _read_tabs(_download_xlsx(source))
    print(f"Tabs found: {[t for t, _ in tabs]}\n")

    payloads, skipped, merged = _collect(tabs)
    per_rep: dict[str, int] = {}
    for rep, _ in payloads.values():
        per_rep[rep] = per_rep.get(rep, 0) + 1

    print("=== Importable leads (name + valid email, deduped) ===")
    for rep, n in sorted(per_rep.items()):
        print(f"  {rep:10} {n}")
    print(f"  {'TOTAL':10} {len(payloads)}\n")
    print(f"Skipped rows: {len(skipped)}  |  duplicate rows merged: {len(merged)}")
    reasons: dict[str, int] = {}
    for _, _, reason in skipped:
        reasons[reason] = reasons.get(reason, 0) + 1
    for reason, n in reasons.items():
        print(f"    - {reason}: {n}")
    print()

    if not commit:
        print("Sample of parsed leads (first 8):")
        for rep, p in list(payloads.values())[:8]:
            tour = "tour" if p.inspection_requested else "-"
            print(
                f"  [{rep:8}] {p.first_name} {p.last_name} <{p.email}> "
                f"{p.company[:28]:28} ph={p.phone or '-':12} {tour}"
            )
        print("\nDRY RUN — nothing written. Re-run with --commit to import.")
        return

    inserted = augmented = failed = 0
    async with session_scope() as session:
        campaign = await campaigns_repo.get_by_slug(session, CAMPAIGN_SLUG)
        for rep, payload in payloads.values():
            email = str(payload.email).strip().lower()
            try:
                existing = await leads_repo.get_by_campaign_email(session, campaign.id, email)
                if existing is None:
                    # New prospect — full create through the normal capture path.
                    lead, _ = await lead_service.capture_lead_created(
                        session, CAMPAIGN_SLUG, payload
                    )
                    lead.tags = _with_rep_tag(lead.tags, rep)
                    await leads_repo.update(session, lead)
                    inserted += 1
                else:
                    # Lead already exists (a kiosk/QR capture, or a prior run of this
                    # script). NEVER overwrite it — a rep sheet is a thinner data source
                    # than a real capture. Only attach rep attribution + fill blanks.
                    if _augment_existing(existing, rep, payload):
                        await leads_repo.update(session, existing)
                    augmented += 1
            except Exception as exc:  # noqa: BLE001 — report and continue the batch
                failed += 1
                print(f"  ! FAILED {email}: {exc}")

    print(
        f"\nDone. inserted={inserted}  augmented (existing, not overwritten)={augmented}  "
        f"failed={failed}\n"
        "Imported leads are pack_delivery_status=pending (no email sent) and "
        "crm_sync_status=pending — the normal jobs deliver/sync them."
    )


async def fix_phones(source: str) -> None:
    """Re-normalise phone ONLY (no pack/CRM side effects) on leads this batch created.

    Scoped to `source=rep_upload` leads created on/after the batch day, so pre-existing
    kiosk leads that the import merged onto keep their own phone untouched. Safe to run
    while pack emails are being delivered — it writes nothing but the phone column."""
    payloads, _, _ = _collect(_read_tabs(_download_xlsx(source)))
    email_to_phone = {p.email: (p.phone or "") for _, p in payloads.values()}
    fixed = 0
    async with session_scope() as session:
        campaign = await campaigns_repo.get_by_slug(session, CAMPAIGN_SLUG)
        leads = await leads_repo.list_for_campaign(session, campaign.id, limit=100_000)
        for lead in leads:
            if lead.source != "rep_upload":
                continue
            if lead.created_at.replace(tzinfo=None) < IMPORT_RUN_DAY:
                continue  # pre-existing kiosk lead the import merged onto — don't touch
            new_phone = email_to_phone.get(lead.email)
            if new_phone and new_phone != lead.phone:
                print(f"  {lead.email}: {lead.phone!r} -> {new_phone!r}")
                lead.phone = new_phone
                await leads_repo.update(session, lead)
                fixed += 1
    print(f"\nPhone-only fix done. updated={fixed}")


async def backdate_created() -> None:
    """Set created_at = the capture day (7 Jul) on this batch's own inserts.

    The dashboard's daily trend groups leads by `created_at`; a backfill inserted
    days later would otherwise land on the import date, not the event day. Scoped to
    `source=rep_upload` leads created on/after the import day, so the pre-existing
    kiosk leads (already correctly dated during the event) are never touched. Writes
    only `created_at`."""
    fixed = 0
    async with session_scope() as session:
        campaign = await campaigns_repo.get_by_slug(session, CAMPAIGN_SLUG)
        leads = await leads_repo.list_for_campaign(session, campaign.id, limit=100_000)
        for lead in leads:
            if lead.source != "rep_upload":
                continue
            if lead.created_at.replace(tzinfo=None) < IMPORT_RUN_DAY:
                continue  # pre-existing kiosk lead — keep its real event-day timestamp
            lead.created_at = CAPTURED_AT
            await leads_repo.update(session, lead)
            fixed += 1
    print(f"Backdated created_at -> {CAPTURED_AT:%Y-%m-%d} on {fixed} backfilled leads.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SHEET_ID, help="Sheet URL, id, or .xlsx path")
    parser.add_argument("--commit", action="store_true", help="Write to the DB (default: dry run)")
    parser.add_argument(
        "--fix-phones",
        action="store_true",
        help="Only re-normalise phone on this batch's own inserts (no other writes)",
    )
    parser.add_argument(
        "--backdate",
        action="store_true",
        help="Set created_at to the capture day on this batch's inserts (no other writes)",
    )
    args = parser.parse_args()
    if args.fix_phones:
        asyncio.run(fix_phones(args.source))
    elif args.backdate:
        asyncio.run(backdate_created())
    else:
        asyncio.run(run(args.source, args.commit))


if __name__ == "__main__":
    main()
