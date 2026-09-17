"""Sincronización completa y segura del CRM local con MAGIIS Web."""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor

from app.core.database import get_conn
from app.integrations import magiis_web
from app.integrations.magiis_crm import MagiisCrmReader

log = logging.getLogger(__name__)
_LOCK_KEY = 72819431
_RESOURCES = {
    "passengers": "magiis_crm_passengers",
    "drivers": "magiis_crm_drivers",
    "vehicles": "magiis_crm_vehicles",
    "travels": "magiis_crm_travels",
    "vehicle_owners": "magiis_crm_vehicle_owners",
}


def _page_items(payload: Any) -> tuple[list[dict[str, Any]], int]:
    page = payload.get("page", payload) if isinstance(payload, dict) else {}
    if not isinstance(page, dict):
        return [], 1
    content = page.get("content", [])
    return [item for item in content if isinstance(item, dict)], int(page.get("totalPages", 1) or 1)


def _fetch_pages(fetch: Callable[[int], Any]) -> list[dict[str, Any]]:
    first = fetch(1)
    items, total_pages = _page_items(first)
    if total_pages <= 1:
        return items
    with ThreadPoolExecutor(max_workers=min(8, total_pages - 1)) as executor:
        futures = [executor.submit(fetch, page) for page in range(2, total_pages + 1)]
        for future in futures:
            next_items, _ = _page_items(future.result())
            items.extend(next_items)
    return items


def _id(item: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _upsert(cur, table: str, item: dict[str, Any], provider_id: int) -> None:
    import json
    cur.execute(
        f"""INSERT INTO {table} (magiis_id, raw_payload)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (magiis_id) DO UPDATE
              SET raw_payload=EXCLUDED.raw_payload, last_seen_at=now()""",
        (provider_id, json.dumps(item, ensure_ascii=False, separators=(",", ":"))),
    )


def _sync_resource(cur, name: str, items: list[dict[str, Any]], id_keys: tuple[str, ...]) -> tuple[int, int]:
    table = _RESOURCES[name]
    ids: set[int] = set()
    for item in items:
        provider_id = _id(item, *id_keys)
        if provider_id is None:
            continue
        ids.add(provider_id)
        _upsert(cur, table, item, provider_id)
    if not ids:
        raise RuntimeError(f"MAGIIS devolvió cero IDs válidos para {name}; no se reconcilia")
    cur.execute(f"DELETE FROM {table} WHERE magiis_id <> ALL(%s)", (list(ids),))
    return len(ids), cur.rowcount


def _sync_travels(cur, items: list[dict[str, Any]]) -> tuple[int, int]:
    import json
    ids: set[int] = set()
    for item in items:
        travel_id = _id(item, "travelId", "id")
        if travel_id is None:
            continue
        ids.add(travel_id)
        travel_date = item.get("travelDate") or item.get("creationDate")
        cur.execute(
            """INSERT INTO magiis_crm_travels
                (magiis_id, raw_payload, travel_date, state, passenger_magiis_id, driver_magiis_id)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (magiis_id) DO UPDATE SET
                  raw_payload=EXCLUDED.raw_payload, travel_date=EXCLUDED.travel_date,
                  state=EXCLUDED.state, passenger_magiis_id=EXCLUDED.passenger_magiis_id,
                  driver_magiis_id=EXCLUDED.driver_magiis_id, last_seen_at=now()""",
            (travel_id, json.dumps(item, ensure_ascii=False, separators=(",", ":")),
             travel_date, item.get("state"), _id(item, "passengerId", "passengerUserId"),
             _id(item, "driverId", "driverUserId")),
        )
    if not ids:
        raise RuntimeError("MAGIIS devolvió cero IDs válidos para travels; no se reconcilia")
    cur.execute("DELETE FROM magiis_crm_travels WHERE magiis_id <> ALL(%s)", (list(ids),))
    return len(ids), cur.rowcount


def sync_all() -> dict[str, Any]:
    """Trae el estado completo de MAGIIS y reconcilia las cinco tablas CRM."""
    import json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,))
            lock_row = cur.fetchone()
            if not lock_row or not lock_row[0]:
                return {"status": "skipped", "reason": "sync already running"}
            run_id = None
            try:
                cur.execute("UPDATE magiis_crm_sync_runs SET status='failed', finished_at=now(), error='superseded' WHERE status='running'")
                cur.execute("INSERT INTO magiis_crm_sync_runs(status) VALUES ('running') RETURNING id")
                run_id = cur.fetchone()[0]
                session = magiis_web.login()
                with MagiisCrmReader(session) as reader:
                    passengers = _fetch_pages(lambda p: reader.passenger_page(page=p, size=100))
                    vehicles = _fetch_pages(lambda p: reader.vehicle_page(page=p, size=100))
                    owners = _fetch_pages(lambda p: reader.vehicle_owner_page(page=p, size=100))
                    drivers = reader.driver_report_page(page=1, size=1000000).get("items", [])
                    travel_from = os.getenv("MAGIIS_CRM_TRAVEL_FROM", "2020-01-01")
                    travel_to = os.getenv("MAGIIS_CRM_TRAVEL_TO", (date.today() + timedelta(days=1)).isoformat())
                    travels = _fetch_pages(lambda p: reader.travel_page(
                        page=p, size=100, travel_date_from=travel_from, travel_date_to=travel_to,
                    ))
                counts: dict[str, Any] = {}
                counts["passengers"], counts["passengers_deleted"] = _sync_resource(cur, "passengers", passengers, ("passengerUserId", "id", "passengerId"))
                counts["drivers"], counts["drivers_deleted"] = _sync_resource(cur, "drivers", drivers, ("driverUserId", "driverId", "id"))
                counts["vehicles"], counts["vehicles_deleted"] = _sync_resource(cur, "vehicles", vehicles, ("vehicleId", "id"))
                counts["vehicle_owners"], counts["vehicle_owners_deleted"] = _sync_resource(cur, "vehicle_owners", owners, ("vehicleOwnerId", "id"))
                counts["travels"], counts["travels_deleted"] = _sync_travels(cur, travels)
                cur.execute("UPDATE magiis_crm_sync_runs SET status='completed', finished_at=now(), resources=%s::jsonb WHERE id=%s", (json.dumps(counts), run_id))
                log.info("MAGIIS CRM sincronizado: %s", counts)
                return {"status": "completed", "run_id": run_id, "resources": counts}
            except Exception as exc:
                log.exception("Falló sincronización MAGIIS CRM")
                if run_id is not None:
                    conn.rollback()
                    cur.execute(
                        "UPDATE magiis_crm_sync_runs SET status='failed', finished_at=now(), error=%s WHERE id=%s",
                        (str(exc)[:1000], run_id),
                    )
                    conn.commit()
                raise
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
