"""Read-only historical migration planning for Livre CRM.

This module deliberately contains no INSERT/UPDATE/DELETE code.  It classifies
provider snapshots and builds a deterministic dry-run report before any future
migration writer is approved.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any, Iterable

CENT = Decimal("0.01")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", _text(value))


def passenger_classification(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    groups: defaultdict[tuple[str, str, str], list[Any]] = defaultdict(list)
    missing_phone: list[Any] = []
    for row in rows:
        source_id = row.get("magiis_id", row.get("id"))
        first = _first(row, "first_name", "firstName", "name")
        last = _first(row, "last_name", "lastName", "surname")
        phone = _first(row, "phone", "telephone", "phoneNumber", "contactPhone")
        if not phone:
            missing_phone.append(source_id)
        # A duplicate candidate is only flagged when the identifying tuple is
        # complete. No rows are merged or discarded here.
        if first and last and _digits(phone):
            groups[(first.casefold(), last.casefold(), _digits(phone))].append(source_id)

    ambiguous_groups = {key: ids for key, ids in groups.items() if len(ids) > 1}
    ambiguous_ids = {item for ids in ambiguous_groups.values() for item in ids}
    missing_ids = set(missing_phone)
    eligible = [
        row.get("magiis_id", row.get("id"))
        for row in rows
        if row.get("magiis_id", row.get("id")) not in ambiguous_ids
        and row.get("magiis_id", row.get("id")) not in missing_ids
    ]
    return {
        "total": len(rows),
        "eligible": len(eligible),
        "eligible_ids": eligible,
        "missing_phone": len(missing_phone),
        "missing_phone_ids": sorted(missing_ids),
        "ambiguous_groups": len(ambiguous_groups),
        "ambiguous_records": len(ambiguous_ids),
        "ambiguous_ids": sorted(ambiguous_ids),
        "does_not_merge": True,
    }


def owner_source_audit(owner_rows: Iterable[dict[str, Any]], livre_user_emails: Iterable[str]) -> dict[str, Any]:
    """Compare owners only with an authorized first-party Livre user source."""
    owner_rows = list(owner_rows)
    emails = {email.strip().casefold() for email in livre_user_emails if _text(email)}
    matches = []
    for row in owner_rows:
        email = _first(row, "email", "contactEmail").casefold()
        if email and email in emails:
            matches.append(row.get("magiis_id", row.get("id")))
    complete_names = sum(
        bool(_first(row, "first_name", "firstName")) and bool(_first(row, "last_name", "lastName"))
        for row in owner_rows
    )
    return {
        "owners_total": len(list(owner_rows)) if not isinstance(owner_rows, list) else len(owner_rows),
        "complete_names": complete_names,
        "authorized_email_matches": len(matches),
        "matched_owner_ids": matches,
        "safe_candidates": 0 if complete_names == 0 else len(matches),
        "source": "livre_users.email",
    }


def vehicle_relationship_report(rows: Iterable[dict[str, Any]], owner_ids: set[Any]) -> dict[str, Any]:
    rows = list(rows)
    missing_owner = []
    unresolved_owner = []
    for row in rows:
        vehicle_id = row.get("magiis_id", row.get("id"))
        owner_id = row.get("owner_id", row.get("ownerId"))
        if owner_id in (None, ""):
            missing_owner.append(vehicle_id)
        elif owner_id not in owner_ids:
            unresolved_owner.append((vehicle_id, owner_id))
    return {
        "total": len(rows),
        "missing_owner": len(missing_owner),
        "unresolved_owner": len(unresolved_owner),
        "linked_owner_count": len(rows) - len(missing_owner) - len(unresolved_owner),
        "promotable": 0,
        "blocked_until_owner_resolution": True,
    }


def commission_split(total: Any, livre_percent: Decimal = Decimal("0.15")) -> dict[str, Decimal]:
    gross = Decimal(str(total)).quantize(CENT, rounding=ROUND_HALF_UP)
    livre = (gross * livre_percent).quantize(CENT, rounding=ROUND_HALF_UP)
    driver = gross - livre
    return {"gross": gross, "livre": livre, "driver": driver, "livre_percent": livre_percent * 100}


def commission_rule() -> dict[str, Any]:
    return {
        "rule": "gross_total * 15% once; driver receives gross_total - commission",
        "livre_percent": Decimal("15.00"),
        "driver_percent": Decimal("85.00"),
        "settled_at": None,
        "creates_liquidation": False,
        "marks_paid": False,
    }
