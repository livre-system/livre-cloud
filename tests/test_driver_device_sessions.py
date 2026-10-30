import pytest
from fastapi import HTTPException

from app.core.database import _assert_driver_device_sessions_schema
from app.mobility.router import (
    _require_current_driver_device,
    driver_clear_live_location,
    driver_update_live_location,
)
from app.mobility.schemas import LocationUpdate


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def user(user_id="driver-a", device_id="device-a"):
    return {"id": user_id, "device_id": device_id}


def session_row(user_id="driver-a", device_id="device-a"):
    return {"user_id": user_id, "device_id": device_id}


def test_active_session_allows_location_for_device_a():
    cur = FakeCursor([session_row()])
    _require_current_driver_device(cur, user(), "device-a")
    assert "driver_device_sessions" in cur.calls[0][0]
    assert cur.calls[0][1] == ("driver-a", "device-a")


def test_second_device_session_for_same_driver_is_allowed():
    cur = FakeCursor([session_row(device_id="device-b")])
    _require_current_driver_device(cur, user(device_id="device-b"), "device-b")


def test_old_device_session_is_rejected_only_when_revoked():
    cur = FakeCursor([None])
    with pytest.raises(HTTPException) as exc:
        _require_current_driver_device(cur, user(), "device-a")
    assert exc.value.status_code == 401


def test_session_cannot_be_used_by_another_driver():
    cur = FakeCursor([None])
    with pytest.raises(HTTPException) as exc:
        _require_current_driver_device(cur, user("driver-b", "device-a"), "device-a")
    assert exc.value.status_code == 401


def test_body_device_must_match_jwt_device():
    cur = FakeCursor()
    with pytest.raises(HTTPException) as exc:
        _require_current_driver_device(cur, user(device_id="device-a"), "device-b")
    assert exc.value.status_code == 401
    assert cur.calls == []


def test_missing_device_is_rejected_without_database_lookup():
    cur = FakeCursor()
    with pytest.raises(HTTPException) as exc:
        _require_current_driver_device(cur, user(device_id=None), None)
    assert exc.value.status_code == 401
    assert cur.calls == []


def test_live_location_updates_after_valid_device_session():
    cur = FakeCursor([session_row(), {"user_id": "driver-a", "location_updated_at": "now"}])
    result = driver_update_live_location(
        LocationUpdate(latitude=-38.7, longitude=-62.2, device_id="device-a"),
        cur=cur,
        user=user(),
    )
    assert result["status"] == "updated"
    assert len(cur.calls) == 3
    assert "UPDATE livre_driver_accounts" in cur.calls[1][0]
    assert "livre_driver_heartbeat_diagnostics" in cur.calls[2][0]


def test_live_location_does_not_write_after_revoked_session():
    cur = FakeCursor([None])
    with pytest.raises(HTTPException) as exc:
        driver_update_live_location(
            LocationUpdate(latitude=-38.7, longitude=-62.2, device_id="device-a"),
            cur=cur,
            user=user(),
        )
    assert exc.value.status_code == 401
    assert len(cur.calls) == 1


def test_logout_revokes_only_authenticated_device_session():
    cur = FakeCursor([session_row(), {"user_id": "driver-a"}])
    result = driver_clear_live_location(cur=cur, user=user(), x_device_id="device-a")
    assert result == {"status": "cleared"}
    assert "livre_driver_device_sessions" in cur.calls[2][0]
    assert "device_id = %s" in cur.calls[2][0]
    assert cur.calls[2][1] == ("driver-a", "device-a")


def test_migration_creates_sessions_and_backfills_existing_active_device():
    migration = open("app/migrations/20261014_driver_device_sessions.sql").read()
    assert "CREATE TABLE IF NOT EXISTS livre_driver_device_sessions" in migration
    assert "INSERT INTO livre_driver_device_sessions" in migration
    assert "ON CONFLICT (user_id, device_id)" in migration


def test_startup_schema_check_rejects_missing_session_table():
    cur = FakeCursor([])
    with pytest.raises(RuntimeError, match="livre_driver_device_sessions"):
        _assert_driver_device_sessions_schema(cur)


def test_startup_schema_check_accepts_session_table():
    cur = FakeCursor([{"table_name": "livre_driver_device_sessions"}])
    _assert_driver_device_sessions_schema(cur)


def test_startup_schema_check_accepts_tuple_cursor_row():
    cur = FakeCursor([("livre_driver_device_sessions",)])
    _assert_driver_device_sessions_schema(cur)
