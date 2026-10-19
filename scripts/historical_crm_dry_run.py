#!/usr/bin/env python3
"""Read-only historical CRM dry-run. Never applies migrations or writes rows."""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

import psycopg

from app.crm.historical_migration import (
    commission_rule,
    owner_source_audit,
    passenger_classification,
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
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            passengers = rows(cur, "magiis_crm_passengers")
            owners = rows(cur, "magiis_crm_vehicle_owners")
            vehicles = rows(cur, "magiis_crm_vehicles")
            cur.execute("SELECT email FROM livre_users WHERE email IS NOT NULL")
            livre_emails = [row[0] for row in cur.fetchall()]
            cur.execute("""SELECT count(*), coalesce(sum(gross_fare),0),
                coalesce(sum(livre_commission),0), coalesce(sum(driver_net),0)
                FROM livi_trips WHERE gross_fare IS NOT NULL""")
            financial_count, gross, commission, driver_net = cur.fetchone()
            cur.execute("""SELECT count(*) FROM livre_crm_passengers""")
            local_passengers = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM livre_crm_vehicle_owners""")
            local_owners = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM livre_crm_vehicles""")
            local_vehicles = cur.fetchone()[0]

    passenger_report = passenger_classification(passengers)
    owner_report = owner_source_audit(owners, livre_emails)
    vehicle_report = vehicle_relationship_report(
        vehicles, {row["magiis_id"] for row in owners}
    )
    report = {
        "mode": "read-only dry-run",
        "writes": False,
        "source": {
            "passengers": passenger_report,
            "owners": owner_report,
            "vehicles": vehicle_report,
        },
        "local_counts_before": {
            "passengers": local_passengers,
            "owners": local_owners,
            "vehicles": local_vehicles,
        },
        "migration_candidates": {
            "passengers": passenger_report["eligible"],
            "owners": owner_report["safe_candidates"],
        "vehicles_operational": 0,
            "vehicles_linked_but_blocked": vehicle_report["linked_owner_count"],
        },
        "commissions": {
            "trips_with_financial_data": financial_count,
            "gross": str(Decimal(str(gross))),
            "livre_commission_existing": str(Decimal(str(commission))),
            "driver_net_existing": str(Decimal(str(driver_net))),
            "rule": {key: str(value) for key, value in commission_rule().items()},
            "liquidations_generated": False,
            "payments_marked": False,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
