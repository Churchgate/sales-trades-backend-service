"""One-off: set the Freshsales record owner (owner_id) on NOG contacts from a
rep-assignment spreadsheet (`Assigned Leads.xlsx`).

The sheet maps named nog-2026 leads to one of two reps. The script matches each row
to a nog-2026 lead by name (+ company tiebreak) to recover its email, then upserts
the contact's owner_id by email.

Matching (in order): exact (first_name, last_name); if several leads share that name,
break the tie on Company; for rows with only one name (last name is a stray "—"), match
first_name + Company. Anything unresolved is printed for manual handling — never guessed.

Setting the owner reuses `FreshsalesClient.upsert_contact` with a MINIMAL payload
(`{"unique_identifier": {"emails": <email>}, "contact": {"owner_id": <id>}}`). Freshsales
upsert only touches the fields you send, so this changes the owner and nothing else. The
regular lead_crm_sync never sends owner_id, so these assignments survive future re-syncs.

Usage (from backend/, against prod via Railway):

    railway run uv run python scripts/assign_owners.py                 # dry run (default)
    railway run uv run python scripts/assign_owners.py --commit --limit 1   # test one
    railway run uv run python scripts/assign_owners.py --commit        # apply all
"""

import argparse
import asyncio
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.freshsales.client import FreshsalesClient  # noqa: E402
from app.models.campaign import Campaign  # noqa: E402
from app.models.lead import Lead  # noqa: E402

CAMPAIGN_SLUG = "nog-2026"
DEFAULT_XLSX = str(Path.home() / "Downloads" / "Assigned Leads.xlsx")

# Sheet layout (row 0 is the header):
#   A First name | B Last name | C Job title | D Company | E Assigned To
COL_FIRST, COL_LAST, COL_TITLE, COL_COMPANY, COL_ASSIGNED = 0, 1, 2, 3, 4

# Rep first name (as written in the "Assigned To" column) -> Freshsales owner_id.
# Verified live against get_owners(): Jennifer Obute / Clinton Osuji. We re-verify each
# id against the live owner list at runtime so a rename/removal fails loud, not silent.
REP_TO_OWNER: dict[str, int] = {
    "jennifer": 17000094102,  # Jennifer Obute
    "clinton": 17000101584,   # Clinton Osuji
}

# The visible "Owner" column is a custom dropdown (cf_owner), NOT the system owner_id
# (which is relabelled "Created By" in this instance). We set both: cf_owner drives the
# "Owner" column the sales team reads, and owner_id keeps the system owner consistent AND
# must be sent on every upsert — a Freshsales upsert that omits owner_id resets it to the
# API user. These strings must exactly match the cf_owner dropdown choices.
REP_TO_OWNER_NAME: dict[str, str] = {
    "jennifer": "Jennifer Obute",
    "clinton": "Clinton Osuji",
}

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# --- xlsx parsing (stdlib only) ---


def _col_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_rows(path: str) -> list[list[str]]:
    """First worksheet's cells as strings, indexed by column so blanks are preserved."""
    zf = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in root.iter(f"{_NS}row"):
        cells: dict[int, str] = {}
        max_c = -1
        for c in row.findall(f"{_NS}c"):
            idx = _col_index(c.get("r"))
            v = c.find(f"{_NS}v")
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif v is not None:
                val = v.text or ""
            else:
                val = ""
            cells[idx] = val
            max_c = max(max_c, idx)
        rows.append([cells.get(i, "") for i in range(max_c + 1)])
    return rows


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _norm(s: str) -> str:
    return (s or "").strip().lower()


# --- matching ---


def _is_placeholder_name(last: str) -> bool:
    """A row whose 'last name' is just a dash/blank has only one usable name."""
    stripped = last.strip()
    return stripped in {"", "-", "—", "–"} or stripped == "â€”"  # mojibake em-dash


def _match_lead(
    row: list[str],
    by_name: dict[tuple[str, str], list[Lead]],
    by_first: dict[str, list[Lead]],
) -> tuple[Lead | None, str]:
    """Return (lead, how) or (None, reason). `how` explains an accepted match; `reason`
    explains a rejection so the operator can act on it."""
    first = _norm(_cell(row, COL_FIRST))
    last = _norm(_cell(row, COL_LAST))
    company = _norm(_cell(row, COL_COMPANY))

    if not _is_placeholder_name(_cell(row, COL_LAST)):
        hits = by_name.get((first, last), [])
        if len(hits) == 1:
            return hits[0], "name"
        if len(hits) > 1:
            comp = [h for h in hits if _norm(h.company) == company]
            if len(comp) == 1:
                return comp[0], "name+company"
            return None, f"ambiguous ({len(hits)} name-hits, {len(comp)} company-hits)"
        # fall through to first-name+company if exact name missed

    # Single-name row (or exact name miss): match on first name + company.
    hits = [h for h in by_first.get(first, []) if _norm(h.company) == company]
    if len(hits) == 1:
        return hits[0], "first+company"
    if len(hits) > 1:
        return None, f"ambiguous first+company ({len(hits)} hits)"
    return None, "no match"


# --- run ---


async def run(xlsx_path: str, commit: bool, limit: int | None) -> None:
    rows = _read_rows(xlsx_path)
    if not rows:
        print(f"no rows read from {xlsx_path}")
        return
    data = rows[1:]  # drop header
    print(f"read {len(data)} data rows from {xlsx_path}\n")

    settings = get_settings()
    if not settings.freshsales_api_key:
        print("freshsales_api_key not set — aborting.")
        return

    async with FreshsalesClient(settings) as client:
        owners_raw = await client.get_owners()
        owner_ids = {u.get("id") for u in owners_raw.get("users", [])}
        owner_name = {
            u.get("id"): (u.get("display_name") or u.get("name"))
            for u in owners_raw.get("users", [])
        }
        # Fail loud if a mapped owner id no longer exists.
        for rep, oid in REP_TO_OWNER.items():
            if oid not in owner_ids:
                print(f"ERROR: owner_id {oid} for rep {rep!r} not in live owners — aborting.")
                return

        async with session_scope() as session:
            campaign = (
                await session.execute(
                    select(Campaign).where(Campaign.slug == CAMPAIGN_SLUG)
                )
            ).scalar_one()
            leads = (
                await session.execute(
                    select(Lead).where(Lead.campaign_id == campaign.id)
                )
            ).scalars().all()

        by_name: dict[tuple[str, str], list[Lead]] = {}
        by_first: dict[str, list[Lead]] = {}
        for lead in leads:
            by_name.setdefault((_norm(lead.first_name), _norm(lead.last_name)), []).append(lead)
            by_first.setdefault(_norm(lead.first_name), []).append(lead)

        planned: list[tuple[Lead, int, str, str]] = []  # (lead, owner_id, rep, how)
        unresolved: list[tuple[list[str], str]] = []
        bad_rep: list[list[str]] = []

        for row in data:
            rep = _norm(_cell(row, COL_ASSIGNED))
            owner_id = REP_TO_OWNER.get(rep)
            if owner_id is None:
                bad_rep.append(row)
                continue
            lead, how = _match_lead(row, by_name, by_first)
            if lead is None:
                unresolved.append((row, how))
                continue
            planned.append((lead, owner_id, rep, how))

        # --- report ---
        by_rep: dict[str, int] = {}
        by_how: dict[str, int] = {}
        for _, _, rep, how in planned:
            by_rep[rep] = by_rep.get(rep, 0) + 1
            by_how[how] = by_how.get(how, 0) + 1
        print("resolved assignments:", len(planned))
        for rep, n in sorted(by_rep.items()):
            print(f"   {rep:>10} -> {n}")
        print("   by match type:", by_how)
        if bad_rep:
            print(f"\nunknown rep name ({len(bad_rep)}) — not in REP_TO_OWNER:")
            for r in bad_rep:
                print(f"   {_cell(r, COL_FIRST)} {_cell(r, COL_LAST)} | {_cell(r, COL_ASSIGNED)!r}")
        if unresolved:
            print(f"\nUNRESOLVED ({len(unresolved)}) — assign manually in Freshsales:")
            for r, why in unresolved:
                print(f"   {_cell(r, COL_FIRST)} {_cell(r, COL_LAST)} | {_cell(r, COL_COMPANY)} | {_cell(r, COL_ASSIGNED)} — {why}")

        if limit is not None:
            planned = planned[:limit]
            print(f"\n--limit {limit}: applying to first {len(planned)} only")

        if not commit:
            print("\nDRY RUN — no changes written. Re-run with --commit to apply.")
            for lead, oid, rep, how in planned[:10]:
                print(f"   would set {lead.email} -> {owner_name.get(oid)} ({rep}) [{how}]")
            return

        print(f"\ncommitting {len(planned)} owner assignments to Freshsales…")
        assigned = 0
        failed = 0
        for lead, oid, rep, _ in planned:
            payload = {
                "unique_identifier": {"emails": lead.email},
                "contact": {
                    "owner_id": oid,
                    "custom_field": {"cf_owner": REP_TO_OWNER_NAME[rep]},
                },
            }
            try:
                await client.upsert_contact(payload)
                assigned += 1
            except Exception as exc:  # noqa: BLE001 — report and continue
                failed += 1
                print(f"   FAILED {lead.email} -> {rep}: {exc}")
        print(f"\ndone: assigned={assigned} failed={failed}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", default=DEFAULT_XLSX, help="Path to the assignment .xlsx")
    parser.add_argument("--commit", action="store_true", help="Write to Freshsales (default: dry run)")
    parser.add_argument("--limit", type=int, default=None, help="Apply to only the first N resolved rows")
    args = parser.parse_args()
    asyncio.run(run(args.xlsx, args.commit, args.limit))


if __name__ == "__main__":
    main()
