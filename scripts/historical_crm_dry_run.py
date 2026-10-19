#!/usr/bin/env python3
"""Read-only historical CRM dry-run. Never applies migrations or writes rows."""
from __future__ import annotations

import json
import os
import sys
import psycopg2

from app.crm.historical_migration import (
    commission_rule,
    financial_audit,
    manual_review_queue,
    driver_vehicle_relationship_plan,
    owner_migration_plan,
    owner_source_audit,
    passenger_migration_plan,
    passenger_classification,
    source_identity_audit,
    vehicle_migration_plan,
    vehicle_relationship_report,
)


def rows(cur, table: str):
    cur.execute(f"SELECT magiis_id, raw_payload FROM {table} ORDER BY magiis_id")
    return [{**payload, "magiis_id": source_id} for source_id, payload in cur.fetchall()]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    # The connection is read-only at the server level for this process. Every
    # statement below is SELECT; no migration runner is imported or invoked.
    with psycopg2.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            passengers = rows(cur, "magiis_crm_passengers")
            owners = rows(cur, "magiis_crm_vehicle_owners")
            vehicles = rows(cur, "magiis_crm_vehicles")
            drivers = rows(cur, "magiis_crm_drivers")
            cur.execute("SELECT email FROM livre_users WHERE email IS NOT NULL")
            livre_emails = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT id::text FROM livre_users")
            livre_user_ids = {row[0] for row in cur.fetchall()}
            cur.execute("""SELECT id, gross_fare, livre_commission, driver_net,
                commission_rate_percent, commission_amount, driver_net_amount,
                financial_status, payment_confirmed
                FROM livi_trips WHERE gross_fare IS NOT NULL ORDER BY id""")
            financial_rows = [
                dict(zip(("id", "gross_fare", "livre_commission", "driver_net",
                          "commission_rate_percent", "commission_amount", "driver_net_amount",
                          "financial_status", "payment_confirmed"), row))
                for row in cur.fetchall()
            ]
            cur.execute("""SELECT count(*) FROM livre_crm_passengers""")
            local_passengers = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM livre_crm_vehicle_owners""")
            local_owners = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM livre_crm_vehicles""")
            local_vehicles = cur.fetchone()[0]
            local_source_ids = {}
            source_key_available = set()
            for table in ("livre_crm_passengers", "livre_crm_vehicle_owners", "livre_crm_vehicles"):
                cur.execute("""SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s AND column_name='source_magiis_id'""", (table,))
                if cur.fetchone():
                    source_key_available.add(table)
                    cur.execute(f"SELECT source_magiis_id FROM {table} WHERE source_magiis_id IS NOT NULL")
                    local_source_ids[table] = {row[0] for row in cur.fetchall()}
                else:
                    local_source_ids[table] = set()

    passenger_report = passenger_classification(passengers)
    owner_report = owner_source_audit(owners, livre_emails)
    passenger_plan = passenger_migration_plan(passengers)
    owner_plan = owner_migration_plan(owners)
    eligible_owner_ids = {
        item["source_magiis_id"] for item in owner_plan if item["eligible"]
    }
    vehicle_report = vehicle_relationship_report(
        vehicles, eligible_owner_ids
    )
    financial_report = financial_audit(financial_rows)
    review_queue = manual_review_queue(passengers)
    vehicle_plan = vehicle_migration_plan(vehicles, eligible_owner_ids)
    driver_link_plan = driver_vehicle_relationship_plan(
        drivers,
        {row["magiis_id"] for row in vehicles},
        livre_user_ids,
    )
    def idempotency(plan, table, eligibility_key="eligible"):
        existing = local_source_ids[table]
        eligible = [item for item in plan if item.get(eligibility_key)]
        return {
            "eligible": len(eligible),
            "already_present_by_source_id": sum(item["source_magiis_id"] in existing for item in eligible),
            "would_insert": sum(item["source_magiis_id"] not in existing for item in eligible),
            "source_id_unique_in_plan": len({item["source_magiis_id"] for item in eligible}) == len(eligible),
            "eligible_identity_audit": source_identity_audit(eligible),
            "source_key_column_available": table in source_key_available,
        }
    report = {
        "mode": "read-only dry-run",
        "writes": False,
        "source": {
            "identity_audit": {
                "passengers": source_identity_audit(passengers),
                "owners": source_identity_audit(owners),
                "vehicles": source_identity_audit(vehicles),
            },
            "passengers": {**passenger_report, "migration_eligible": sum(item["eligible"] for item in passenger_plan)},
            "manual_review": {
                "total": len(review_queue),
                "ambiguous": sum(item["reason"] == "ambiguous_identity" for item in review_queue),
                "missing_phone": sum(item["reason"] == "missing_phone" for item in review_queue),
                "all_pending": all(item["status"] == "pending_manual_review" and item["decision"] is None for item in review_queue),
            },
            "owners": owner_report,
            "vehicles": vehicle_report,
            "driver_vehicle_links": {
                "source_rows": len(driver_link_plan),
                "eligible_local_links": sum(item["eligible"] for item in driver_link_plan),
                "blocked_driver_not_local": sum(item["exclusion_reason"] == "driver_not_local" for item in driver_link_plan),
                "blocked_vehicle_not_in_source": sum(item["exclusion_reason"] == "vehicle_not_in_source" for item in driver_link_plan),
            },
        },
        "local_counts_before": {
            "passengers": local_passengers,
            "owners": local_owners,
            "vehicles": local_vehicles,
        },
        "idempotency": {
            "passengers": idempotency(passenger_plan, "livre_crm_passengers"),
            "owners": idempotency(owner_plan, "livre_crm_vehicle_owners"),
            "vehicles": idempotency(vehicle_plan, "livre_crm_vehicles", "promote_to_operational"),
            "conflict_policy": {
                "source_entities": "ON CONFLICT (source_magiis_id) DO NOTHING",
                "driver_vehicle_links": "ON CONFLICT (driver_user_id, vehicle_magiis_id) DO NOTHING",
                "preserves_local_changes": True,
                "does_not_overwrite_raw_payload": True,
            },
        },
        "migration_candidates": {
            "passengers": sum(item["eligible"] for item in passenger_plan),
            "owners": sum(item["eligible"] for item in owner_plan),
            "vehicles_operational": sum(item["promote_to_operational"] for item in vehicle_plan),
            "vehicles_without_owner": vehicle_report["missing_owner"],
            "driver_vehicle_links": sum(item["eligible"] for item in driver_link_plan),
        },
        "commissions": {
            **financial_report,
            "rule": {key: str(value) for key, value in commission_rule().items()},
            "liquidations_generated": False,
            "payments_marked": False,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
