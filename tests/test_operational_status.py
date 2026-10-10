from app.crm.router import (
    _HIDDEN_GPS_DRIVER_USER_IDS,
    _operational_snapshot,
    operational,
    update_operational_driver_status,
)
from pydantic import ValidationError

from app.mobility.schemas import DriverOperationalStatusUpdate


class FakeCursor:
    def __init__(self, rows_by_query):
        self.rows_by_query = list(rows_by_query)
        self.calls = []
        self.rowcount = 1

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchall(self):
        return self.rows_by_query.pop(0)

    def fetchone(self):
        return self.rows_by_query.pop(0)


def driver_row(user_id, status, trip_status=None):
    return {
        "user_id": user_id,
        "active": True,
        "operational_status": status,
        "session_active": True,
        "name": f"Driver {user_id}",
        "username": f"driver-{user_id}",
        "latitude": -38.7,
        "longitude": -62.2,
        "location_updated_at": None,
        "associated_vehicle_plate": None,
        "associated_vehicle_model": None,
        "associated_vehicle_color": None,
        "trip_id": None,
        "trip_status": trip_status,
        "tracking_driver_name": None,
        "tracking_plate": None,
        "tracking_vehicle_model": None,
        "tracking_vehicle_color": None,
    }


def test_snapshot_preserves_the_three_canonical_statuses():
    cur = FakeCursor([[driver_row(1, "available"), driver_row(2, "unavailable"), driver_row(3, "out_of_service")]])

    snapshot = _operational_snapshot(cur)

    assert [item["operationalStatus"] for item in snapshot] == ["available", "unavailable", "out_of_service"]
    assert [item["operationalLabel"] for item in snapshot] == ["Disponible", "No disponible", "Fuera de servicio"]
    assert all(item["source"] == "livre_driver_accounts+device_session+driver_vehicle_association+livi_trips" for item in snapshot)


def test_snapshot_uses_the_persistent_driver_vehicle_association():
    row = driver_row("barroso", "available")
    row.update({
        "associated_vehicle_plate": "406 AG8008HE",
        "associated_vehicle_model": "FIAT CRONOS",
        "associated_vehicle_color": "ROJO",
    })

    snapshot = _operational_snapshot(FakeCursor([[row]]))

    assert snapshot[0]["vehicle"] == {
        "plate": "406 AG8008HE",
        "model": "FIAT CRONOS",
        "color": "ROJO",
    }


def test_snapshot_query_excludes_verified_non_map_ids_but_keeps_real_driver():
    real_driver_id = "2aa35285-3da4-4238-985e-303493b2bf67"
    cur = FakeCursor([[driver_row(real_driver_id, "available")]])

    snapshot = _operational_snapshot(cur)

    query, params = cur.calls[0]
    assert "a.user_id <> ALL(%s)" in query
    assert params == (list(_HIDDEN_GPS_DRIVER_USER_IDS),)
    assert real_driver_id not in _HIDDEN_GPS_DRIVER_USER_IDS
    assert snapshot[0]["driverUserId"] == real_driver_id


def test_crm_operational_counts_each_canonical_status_without_active_as_status():
    rows = [driver_row(1, "available"), driver_row(2, "unavailable"), driver_row(3, "out_of_service")]
    cur = FakeCursor([rows, {"total": 0}])

    result = operational(user={"id": "crm"}, cur=cur)

    assert result["driver_totals"] == {
        "totalDrivers": 3,
        "totalDriversOnline": 1,
        "totalDriversOffline": 2,
        "totalDriversInTravel": 0,
        "available": 1,
        "unavailable": 1,
        "out_of_service": 1,
    }
    assert all(item["operationalStatus"] != "active" for item in result["drivers"])


def test_crm_status_update_writes_the_canonical_value_returned_by_backend():
    cur = FakeCursor([{ "operational_status": "out_of_service" }])

    result = update_operational_driver_status(
        driver_user_id=42,
        payload=DriverOperationalStatusUpdate(status="out_of_service"),
        user={"id": "crm"},
        cur=cur,
    )

    assert result == {"status": "out_of_service"}
    assert cur.calls[0][1] == ("out_of_service", 42)


def test_expired_session_has_no_presence_or_coordinates():
    row = driver_row("expired", "available")
    row.update({"session_active": False, "latitude": None, "longitude": None})

    snapshot = _operational_snapshot(FakeCursor([[row]]))

    assert snapshot[0]["sessionActive"] is False
    assert snapshot[0]["lastPlace"] is None
    assert snapshot[0]["lastSee"] is None
    assert snapshot[0]["operationalStatus"] == "available"


def test_schema_rejects_active_and_other_noncanonical_statuses():
    for status in ("active", "offline", "out-of-service"):
        try:
            DriverOperationalStatusUpdate(status=status)  # type: ignore[arg-type]
        except ValidationError:
            continue
        raise AssertionError(f"unexpected operational status accepted: {status}")
