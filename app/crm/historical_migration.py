"""Read-only historical migration planning for Livre CRM.

This module deliberately contains no INSERT/UPDATE/DELETE code.  It classifies
provider snapshots and builds a deterministic dry-run report before any future
migration writer is approved.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import re
import unicodedata
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


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return the provider payload without losing any source fields."""
    value = row.get("raw_payload")
    return value if isinstance(value, dict) else {
        key: value for key, value in row.items() if key != "magiis_id"
    }


_COMPANY_MARKERS = {
    "sa", "s.a", "srl", "s.r.l", "sas", "s.a.s", "sau", "s.a.u",
    "sc", "cooperativa", "fundacion", "asociacion", "municipalidad",
    "empresa", "transporte", "servicios", "constructora", "comercial",
    "consorcio", "instituto", "iglesia", "club", "sociedad",
}


def _name_tokens(value: Any) -> list[str]:
    return re.findall(r"[^\s]+", _text(value))


def classify_owner_name(value: Any) -> dict[str, Any]:
    """Classify titularDescription without mutating or guessing ambiguous names."""
    original = _text(value)
    tokens = _name_tokens(original)
    folded = {
        unicodedata.normalize("NFKD", token.casefold()).encode("ascii", "ignore").decode()
        for token in tokens
    }
    if not original:
        category = "ambiguous"
        reason = "missing_titularDescription"
    elif folded & _COMPANY_MARKERS:
        category = "company"
        reason = "company_marker"
    elif len(tokens) < 2 or len(tokens) > 4 or any(not re.fullmatch(r"[\wÁÉÍÓÚÜÑáéíóúüñ'’-]+", token) for token in tokens):
        category = "ambiguous"
        reason = "insufficient_or_irregular_name_tokens"
    else:
        category = "person"
        reason = "two_to_four_name_tokens"
    return {"full_name": original, "category": category, "reason": reason}


def split_owner_name(value: Any) -> tuple[str, str] | None:
    """Split only high-confidence person names; preserve all other names whole."""
    classification = classify_owner_name(value)
    if classification["category"] != "person":
        return None
    parts = _name_tokens(value)
    return parts[0], " ".join(parts[1:])


def passenger_migration_plan(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare one local row per source passenger; duplicates are not merged."""
    plan = []
    for row in rows:
        source_id = row.get("magiis_id", row.get("id"))
        first = _first(row, "first_name", "firstName", "name")
        last = _first(row, "last_name", "lastName", "surname")
        phone = _first(row, "phone", "telephone", "phoneNumber", "contactPhone")
        eligible = bool(source_id not in (None, "") and first and last and phone)
        plan.append({
            "source_magiis_id": source_id,
            "first_name": first,
            "last_name": last,
            "email": _first(row, "email", "passengerUserEmail"),
            "phone": phone,
            "country": _first(row, "country") or "Argentina",
            "raw_payload": _payload(row),
            "eligible": eligible,
            "exclusion_reason": None if eligible else "missing_required_identity_field",
        })
    return plan


def source_identity_audit(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Verify immutable source IDs before any local insert is considered."""
    ids = [row.get("source_magiis_id", row.get("magiis_id", row.get("id"))) for row in rows]
    non_null = [source_id for source_id in ids if source_id not in (None, "")]
    groups: defaultdict[Any, int] = defaultdict(int)
    for source_id in non_null:
        groups[source_id] += 1
    duplicate_groups = {source_id: count for source_id, count in groups.items() if count > 1}
    return {
        "total": len(ids),
        "null_or_empty": len(ids) - len(non_null),
        "non_null": len(non_null),
        "unique_non_null": len(groups),
        "duplicate_groups": len(duplicate_groups),
        "duplicate_rows": sum(count - 1 for count in duplicate_groups.values()),
        "unique_and_non_null": not duplicate_groups and len(ids) == len(non_null),
        "duplicate_examples": dict(list(duplicate_groups.items())[:20]),
    }


def owner_migration_plan(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map historical owners from titularDescription, preserving the payload."""
    plan = []
    for row in rows:
        source_id = row.get("magiis_id", row.get("id"))
        classification = classify_owner_name(row.get("titularDescription"))
        name = split_owner_name(row.get("titularDescription"))
        eligible = bool(source_id not in (None, "") and name)
        plan.append({
            "source_magiis_id": source_id,
            "first_name": name[0] if name else "",
            "last_name": name[1] if name else "",
            "identification": _first(row, "identification"),
            "email": _first(row, "email", "contactEmail"),
            "phone": _first(row, "phone", "contactPhone"),
            "city": _first(row, "city"),
            "province": _first(row, "province"),
            "street": _first(row, "street", "streetName"),
            "street_number": _first(row, "street_number", "streetNumber"),
            "raw_payload": _payload(row),
            "titular_description": classification["full_name"],
            "name_category": classification["category"],
            "name_classification_reason": classification["reason"],
            "eligible": eligible,
            "exclusion_reason": None if eligible else (
                "missing_source_id" if source_id in (None, "") else
                f"name_review_{classification['category']}"
            ),
        })
    return plan


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
    owner_plan = owner_migration_plan(owner_rows)
    emails = {email.strip().casefold() for email in livre_user_emails if _text(email)}
    matches = []
    for row in owner_rows:
        email = _first(row, "email", "contactEmail").casefold()
        if email and email in emails:
            matches.append(row.get("magiis_id", row.get("id")))
    complete_names = sum(bool(item["eligible"]) for item in owner_plan)
    by_category = {category: [
        {"source_magiis_id": item["source_magiis_id"], "full_name": item["titular_description"], "reason": item["name_classification_reason"]}
        for item in owner_plan if item["name_category"] == category
    ] for category in ("person", "company", "ambiguous")}
    return {
        "owners_total": len(owner_rows),
        "complete_names": complete_names,
        "authorized_email_matches": len(matches),
        "matched_owner_ids": matches,
        "safe_candidates": complete_names,
        "titular_description_names": sum(bool(split_owner_name(row.get("titularDescription"))) for row in owner_rows),
        "excluded_missing_titular_description": sum(not item["eligible"] for item in owner_plan),
        "source": "historical.titularDescription",
        "name_categories": {category: len(items) for category, items in by_category.items()},
        "name_review": {category: by_category[category] for category in ("company", "ambiguous")},
        "person_examples": by_category["person"][:10],
        "automatic_split_policy": "person only; company and ambiguous names remain whole in raw_payload and are excluded pending review",
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
        "promotable": len(rows) - len(missing_owner) - len(unresolved_owner),
        "blocked_until_owner_resolution": bool(unresolved_owner),
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


def manual_review_queue(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a non-destructive queue for the two passenger exception classes."""
    rows = list(rows)
    classification = passenger_classification(rows)
    ambiguous = set(classification["ambiguous_ids"])
    missing = set(classification["missing_phone_ids"])
    queue = []
    for row in rows:
        source_id = row.get("magiis_id", row.get("id"))
        if source_id in ambiguous:
            reason = "ambiguous_identity"
        elif source_id in missing:
            reason = "missing_phone"
        else:
            continue
        queue.append({
            "source_magiis_id": source_id,
            "reason": reason,
            "status": "pending_manual_review",
            "decision": None,
            "evidence": {
                "first_name": _first(row, "first_name", "firstName", "name"),
                "last_name": _first(row, "last_name", "lastName", "surname"),
                "phone": _first(row, "phone", "telephone", "phoneNumber", "contactPhone"),
            },
        })
    return queue


def financial_audit(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Check each populated financial row without changing settlement state."""
    audited = []
    for row in rows:
        gross_value = row.get("gross_fare")
        if gross_value is None:
            continue
        split = commission_split(gross_value)
        commission = Decimal(str(row.get("livre_commission"))) if row.get("livre_commission") is not None else None
        driver = Decimal(str(row.get("driver_net"))) if row.get("driver_net") is not None else None
        explicit_commission = Decimal(str(row.get("commission_amount"))) if row.get("commission_amount") is not None else None
        explicit_driver = Decimal(str(row.get("driver_net_amount"))) if row.get("driver_net_amount") is not None else None
        explicit_rate = Decimal(str(row.get("commission_rate_percent"))) if row.get("commission_rate_percent") is not None else None
        explicit_fields_close = (
            (explicit_commission is None or explicit_commission == split["livre"])
            and (explicit_driver is None or explicit_driver == split["driver"])
            and (explicit_rate is None or explicit_rate == Decimal("15.00"))
        )
        status = _text(row.get("financial_status"))
        confirmed = row.get("payment_confirmed") is True
        paid = confirmed and status not in {"pending_settlement", "pending"}
        audited.append({
            "trip_id": row.get("id"),
            "gross": split["gross"],
            "expected_livre_commission": split["livre"],
            "expected_driver_net": split["driver"],
            "commission_closes": commission == split["livre"],
            "driver_net_closes": driver == split["driver"],
            "sum_closes": commission is not None and driver is not None and commission + driver == split["gross"],
            "commission_applied_once": commission == split["livre"] and explicit_fields_close,
            "explicit_fields_close": explicit_fields_close,
            "explicit_rate_present": explicit_rate is not None,
            "financial_status": status,
            "payment_confirmed": confirmed,
            "paid": paid,
        })
    closing = [item for item in audited if item["commission_closes"] and item["driver_net_closes"] and item["sum_closes"]]
    return {
        "count": len(audited),
        "closing_rows": len(closing),
        "explicit_fields_closing_rows": sum(item["explicit_fields_close"] for item in audited),
        "explicit_rate_rows": sum(item["explicit_rate_present"] for item in audited),
        "single_commission_rows": sum(item["commission_applied_once"] for item in audited),
        "gross_total": sum((item["gross"] for item in audited), Decimal("0.00")),
        "commission_total": sum((item["expected_livre_commission"] for item in audited), Decimal("0.00")),
        "driver_net_total": sum((item["expected_driver_net"] for item in audited), Decimal("0.00")),
        "non_closing_trip_ids": [item["trip_id"] for item in audited if item not in closing],
        "paid_rows": sum(item["paid"] for item in audited),
        "pending_rows": sum(item["financial_status"] in {"pending_settlement", "pending"} for item in audited),
        "unpaid_rows": sum(not item["paid"] for item in audited),
        "commission_rule": "gross * 15% once; driver = gross - commission",
        "rows": audited,
    }


def vehicle_migration_plan(rows: Iterable[dict[str, Any]], owner_source_ids: set[Any]) -> list[dict[str, Any]]:
    """Prepare vehicle records while retaining provider owner identity separately."""
    plan = []
    for row in rows:
        card = row.get("identificationCard") or {}
        source_id = row.get("magiis_id", row.get("id"))
        owner_id = row.get("owner_id", row.get("ownerId"))
        linked = owner_id not in (None, "") and owner_id in owner_source_ids
        year = _first(card, "year", "yearOfVehicle")
        vehicle_type = _first(card, "vehicleType", "vehicleTypeId", "type")
        eligible = bool(source_id not in (None, "") and _first(card, "domain", "plate") and
                        _first(card, "mark", "brand") and _first(card, "model") and year and
                        _first(card, "color") and vehicle_type)
        plan.append({
            "source_magiis_id": source_id,
            "owner_source_magiis_id": owner_id,
            "local_owner_id": None,
            "plate": _first(card, "domain", "plate"),
            "brand": _first(card, "mark", "brand"),
            "model": _first(card, "model"),
            "year": year,
            "color": _first(card, "color"),
            "chassis": _first(card, "chassis"),
            "motor": _first(card, "motor"),
            "vehicle_type": vehicle_type,
            "image_url": _first(row, "image_url", "imageUrl", "photo"),
            "raw_payload": _payload(row),
            "status": "ready_after_owner_resolution" if eligible and linked else "blocked_until_owner_resolution",
            "owner_source_exists": linked,
            "eligible": eligible,
            "promote_to_operational": eligible and linked,
            "exclusion_reason": None if eligible else "missing_required_vehicle_field",
        })
    return plan


def driver_vehicle_relationship_plan(
    rows: Iterable[dict[str, Any]],
    vehicle_source_ids: set[Any],
    livre_user_ids: set[str],
) -> list[dict[str, Any]]:
    """Prepare only links whose driver identity is already local and authorized."""
    plan = []
    for row in rows:
        driver_id = _first(row, "driverUserId")
        vehicle_id = row.get("vehicleId")
        eligible = bool(driver_id and vehicle_id in vehicle_source_ids and driver_id in livre_user_ids)
        plan.append({
            "driver_user_id": driver_id,
            "vehicle_magiis_id": vehicle_id,
            "vehicle_label": _first(row, "vehicleDomain", "vehiclePlate"),
            "eligible": eligible,
            "exclusion_reason": None if eligible else (
                "driver_not_local" if driver_id not in livre_user_ids else
                "vehicle_not_in_source" if vehicle_id not in vehicle_source_ids else
                "missing_driver_or_vehicle_id"
            ),
        })
    return plan
