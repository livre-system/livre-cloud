from app.crm.router import _operational_snapshot, operational, update_operational_driver_status
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
        "name": f"Driver {user_id}",
        "username": f"driver-{user_id}",
        "latitude": -38.7,
        "longitude": -62.2,
        "location_updated_at": None,
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
    assert all(item["source"] == "livre_driver_accounts+livi_trips" for item in snapshot)


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


def test_schema_rejects_active_and_other_noncanonical_statuses():
    for status in ("active", "offline", "out-of-service"):
        try:
            DriverOperationalStatusUpdate(status=status)  # type: ignore[arg-type]
        except ValidationError:
            continue
        raise AssertionError(f"unexpected operational status accepted: {status}")
