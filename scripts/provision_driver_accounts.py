#!/usr/bin/env python3
"""Provisiona una cuenta Livre por chofer del padrón local.

Por seguridad, el modo predeterminado solo informa qué haría. Usar --apply
para crear cuentas y --credentials-file para guardar las claves generadas.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

import bcrypt
import psycopg2


def first(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def driver_name(data: dict, driver_id: int) -> str:
    name = first(data, "name", "fullName", "driverName")
    if not name:
        name = " ".join(
            part for part in (
                str(first(data, "firstName", "firstname") or ""),
                str(first(data, "lastName", "lastname") or ""),
            ) if part
        ).strip()
    return name or f"Chofer {driver_id}"


def vehicle_id(data: dict):
    value = first(data, "vehicleId", "vehicle_id", "carId", "car_id")
    vehicle = data.get("vehicle")
    if value in (None, "") and isinstance(vehicle, dict):
        value = first(vehicle, "id", "vehicleId")
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def vehicle_label(data: dict):
    vehicle = data.get("vehicle")
    if not isinstance(vehicle, dict):
        vehicle = data
    return first(vehicle, "plate", "domain", "vehicleDomain", "carPlate")


def related_ids(data: object, keys: tuple[str, ...]) -> set[int]:
    found: set[int] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys:
                values = value if isinstance(value, list) else [value]
                for item in values:
                    try:
                        found.add(int(item.get("id") if isinstance(item, dict) else item))
                    except (TypeError, ValueError):
                        pass
            found.update(related_ids(value, keys))
    elif isinstance(data, list):
        for item in data:
            found.update(related_ids(item, keys))
    return found


def link_vehicle(cur, user_id: str, source_vehicle_id: int, label: str | None = None):
    cur.execute(
        """INSERT INTO livre_driver_vehicles (driver_user_id, vehicle_magiis_id, vehicle_label, is_primary)
           VALUES (%s, %s, %s, TRUE)
           ON CONFLICT (driver_user_id, vehicle_magiis_id) DO UPDATE
           SET vehicle_label = COALESCE(EXCLUDED.vehicle_label, livre_driver_vehicles.vehicle_label),
               updated_at = NOW()""",
        (user_id, source_vehicle_id, label),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="crear las cuentas; sin esto solo simula")
    parser.add_argument("--credentials-file", default="driver-credentials.json")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Falta DATABASE_URL")

    credentials = []
    users_by_source_id = {}
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT magiis_id, raw_payload FROM magiis_crm_drivers ORDER BY magiis_id")
            drivers = cur.fetchall()
            if not drivers:
                raise SystemExit("No hay conductores en magiis_crm_drivers; no se creó nada")

            for source_id, payload in drivers:
                name = driver_name(payload, source_id)
                username = f"chofer_{source_id}"
                cur.execute("SELECT u.id FROM livre_users u WHERE u.username = %s", (username,))
                existing = cur.fetchone()
                if existing:
                    user_id = existing[0]
                    action = "existing"
                elif args.apply:
                    user_id = str(uuid.uuid4())
                    password = secrets.token_urlsafe(9)
                    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                    cur.execute(
                        "INSERT INTO livre_users (id, username, name, password_hash) VALUES (%s, %s, %s, %s)",
                        (user_id, username, name, password_hash),
                    )
                    cur.execute(
                        "INSERT INTO livre_driver_accounts (user_id, magiis_driver_id, active) VALUES (%s, %s, TRUE)",
                        (user_id, source_id),
                    )
                    action = "created"
                    credentials.append({"name": name, "username": username, "password": password, "source_driver_id": source_id})
                else:
                    user_id = None
                    action = "would_create"
                if user_id:
                    users_by_source_id[source_id] = user_id

                linked_vehicle = vehicle_id(payload)
                if user_id and linked_vehicle:
                    link_vehicle(cur, user_id, linked_vehicle, vehicle_label(payload))
                print(json.dumps({"source_driver_id": source_id, "name": name, "username": username, "action": action, "vehicle_id": linked_vehicle}, ensure_ascii=False))

            cur.execute("SELECT raw_payload FROM magiis_crm_vehicle_owners")
            for (owner_payload,) in cur.fetchall():
                owner_drivers = related_ids(owner_payload, ("driverUserId", "driverId", "driver_id", "driverIds"))
                owner_vehicles = related_ids(owner_payload, ("vehicleId", "vehicle_id", "vehicleIds"))
                for source_driver_id in owner_drivers:
                    user_id = users_by_source_id.get(source_driver_id)
                    if not user_id:
                        continue
                    for source_vehicle_id in owner_vehicles:
                        link_vehicle(cur, user_id, source_vehicle_id, first(owner_payload, "plate", "domain"))

    if credentials:
        path = Path(args.credentials_file)
        path.write_text(json.dumps(credentials, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        print(f"Credenciales nuevas guardadas en {path}", file=sys.stderr)
    elif args.apply:
        print("No hubo cuentas nuevas; no se generó archivo de credenciales", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())