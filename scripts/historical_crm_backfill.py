#!/usr/bin/env python3
"""Apply the reviewed historical CRM backfill only with explicit opt-in.

Default mode is read-only. This file is prepared for the later approved window;
no production execution is performed by this change.
"""
from __future__ import annotations

import argparse
import os
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_batch

from app.crm.historical_migration import (
    driver_vehicle_relationship_plan,
    owner_migration_plan,
    passenger_migration_plan,
    vehicle_migration_plan,
)


def _rows(cur, table: str) -> list[dict[str, Any]]:
    cur.execute(f"SELECT magiis_id, raw_payload FROM {table} ORDER BY magiis_id")
    return [{**(payload or {}), "magiis_id": source_id} for source_id, payload in cur.fetchall()]


def apply_backfill(dsn: str) -> dict[str, int]:
    """Run one atomic, source-key-idempotent backfill transaction."""
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            for table in ("livre_crm_passengers", "livre_crm_vehicle_owners", "livre_crm_vehicles"):
                cur.execute("""SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s AND column_name='source_magiis_id'""", (table,))
                if cur.fetchone() is None:
                    raise RuntimeError(f"missing source_magiis_id on {table}; apply schema migration first")

            passengers = passenger_migration_plan(_rows(cur, "magiis_crm_passengers"))
            owners = owner_migration_plan(_rows(cur, "magiis_crm_vehicle_owners"))
            vehicles_source = _rows(cur, "magiis_crm_vehicles")
            vehicles = vehicle_migration_plan(vehicles_source, {r["magiis_id"] for r in _rows(cur, "magiis_crm_vehicle_owners")})

            owner_sql = """INSERT INTO livre_crm_vehicle_owners
                (source_magiis_id, first_name, last_name, identification, email, phone,
                 city, province, street, street_number, raw_payload)
                VALUES (%(source_magiis_id)s, %(first_name)s, %(last_name)s, %(identification)s,
                        %(email)s, %(phone)s, %(city)s, %(province)s, %(street)s,
                        %(street_number)s, %(raw_payload)s)
                ON CONFLICT (source_magiis_id) WHERE source_magiis_id IS NOT NULL DO NOTHING"""
            owner_params = [{**r, "raw_payload": Json(r["raw_payload"])} for r in owners if r["eligible"]]
            execute_batch(cur, owner_sql, owner_params)

            cur.execute("SELECT source_magiis_id, id FROM livre_crm_vehicle_owners WHERE source_magiis_id IS NOT NULL")
            owner_local_ids = {source: local for source, local in cur.fetchall()}

            passenger_sql = """INSERT INTO livre_crm_passengers
                (source_magiis_id, first_name, last_name, email, phone, country, raw_payload)
                VALUES (%(source_magiis_id)s, %(first_name)s, %(last_name)s, %(email)s,
                        %(phone)s, %(country)s, %(raw_payload)s)
                ON CONFLICT (source_magiis_id) WHERE source_magiis_id IS NOT NULL DO NOTHING"""
            passenger_params = [{**r, "raw_payload": Json(r["raw_payload"])} for r in passengers if r["eligible"]]
            execute_batch(cur, passenger_sql, passenger_params)

            vehicle_sql = """INSERT INTO livre_crm_vehicles
                (source_magiis_id, plate, brand, model, year, color, chassis, motor,
                 vehicle_type, owner_id, image_url, raw_payload)
                VALUES (%(source_magiis_id)s, %(plate)s, %(brand)s, %(model)s, %(year)s,
                        %(color)s, %(chassis)s, %(motor)s, %(vehicle_type)s, %(owner_id)s,
                        %(image_url)s, %(raw_payload)s)
                ON CONFLICT (source_magiis_id) WHERE source_magiis_id IS NOT NULL DO NOTHING"""
            vehicle_params = []
            for row in vehicles:
                if not row["promote_to_operational"] or row["owner_source_magiis_id"] not in owner_local_ids:
                    continue
                vehicle_params.append({**row, "owner_id": owner_local_ids[row["owner_source_magiis_id"]], "raw_payload": Json(row["raw_payload"])})
            execute_batch(cur, vehicle_sql, vehicle_params)

            cur.execute("SELECT id::text FROM livre_users")
            local_driver_ids = {row[0] for row in cur.fetchall()}
            driver_rows = _rows(cur, "magiis_crm_drivers")
            links = driver_vehicle_relationship_plan(
                driver_rows, {r["magiis_id"] for r in vehicles_source}, local_driver_ids
            )
            link_sql = """INSERT INTO livre_driver_vehicles
                (driver_user_id, vehicle_magiis_id, vehicle_label, is_primary)
                VALUES (%(driver_user_id)s, %(vehicle_magiis_id)s, %(vehicle_label)s, FALSE)
                ON CONFLICT (driver_user_id, vehicle_magiis_id) DO NOTHING"""
            execute_batch(cur, link_sql, [r for r in links if r["eligible"]])
            return {
                "passengers": len(passenger_params),
                "owners": len(owner_params),
                "vehicles": len(vehicle_params),
                "driver_vehicle_links": sum(r["eligible"] for r in links),
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the approved transaction")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("read-only by default; use historical_crm_dry_run.py for the dry-run")
    if os.getenv("HISTORICAL_CRM_ALLOW_WRITE") != "1":
        raise SystemExit("refusing write: set HISTORICAL_CRM_ALLOW_WRITE=1 only after explicit approval")
    if not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required")
    print(apply_backfill(os.environ["DATABASE_URL"]))


if __name__ == "__main__":
    main()
