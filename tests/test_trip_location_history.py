import pytest
from fastapi import HTTPException

from app.crm.router import _require_livreuser
from app.mobility.router import driver_update_location
from app.mobility.schemas import LocationUpdate


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows


def user(user_id="driver-a", device_id="device-a"):
    return {"id": user_id, "device_id": device_id}


def session_row():
    return {"user_id": "driver-a", "device_id": "device-a"}


def trip_row(status="arriving"):
    return {
        "id": 42,
        "origen": "Origen",
        "destino": "Destino",
        "status": status,
        "assigned_driver_user_id": "driver-a",
    }


def test_route_history_migration_creates_own_table_and_indexes():
    migration = open("app/migrations/20261021_trip_location_history.sql").read()
    assert "CREATE TABLE IF NOT EXISTS livre_trip_location_history" in migration
    assert "REFERENCES livi_trips(id) ON DELETE CASCADE" in migration
    assert "idx_livre_trip_location_history_trip_time" in migration


def test_livreuser_is_allowed_to_view_routes():
    cur = FakeCursor([{"username": "livreuser"}])
    _require_livreuser(cur, {"id": "admin-id"})


def test_other_users_cannot_view_routes():
    cur = FakeCursor([{"username": "another-user"}])
    with pytest.raises(HTTPException) as exc:
        _require_livreuser(cur, {"id": "other-id"})
    assert exc.value.status_code == 403


def test_location_history_insert_is_gated_to_accepted_trip_states(monkeypatch):
    monkeypatch.setattr("app.mobility.router._driver_trip", lambda trip: trip)
    cur = FakeCursor([
        session_row(),
        trip_row(),
        trip_row(),
    ])
    driver_update_location(
        42,
        LocationUpdate(latitude=-38.7, longitude=-62.2, device_id="device-a"),
        cur=cur,
        user=user(),
    )
    history_call = next(call for call in cur.calls if "livre_trip_location_history" in call[0])
    assert "status IN ('accepted', 'arriving', 'started', 'in_progress')" in history_call[0]
    assert history_call[1] == (-38.7, -62.2, 42, "driver-a")


def test_location_history_is_not_recorded_for_unaccepted_trip(monkeypatch):
    monkeypatch.setattr("app.mobility.router._driver_trip", lambda trip: trip)
    cur = FakeCursor([
        session_row(),
        trip_row(status="assigned"),
        trip_row(status="assigned"),
    ])
    driver_update_location(
        42,
        LocationUpdate(latitude=-38.7, longitude=-62.2, device_id="device-a"),
        cur=cur,
        user=user(),
    )
    history_call = next(call for call in cur.calls if "livre_trip_location_history" in call[0])
    assert "status IN ('accepted', 'arriving', 'started', 'in_progress')" in history_call[0]
