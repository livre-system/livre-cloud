from app.crm.router import (
    _HIDDEN_GPS_DRIVER_USER_IDS,
    _operational_snapshot,
    operational,
    update_operational_driver_status,
)
from pydantic import ValidationError

from app.mobility.schemas import DriverOperationalStatusUpdate
from app.mobility.router import _driver_trip, driver_trips


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


def test_driver_trips_returns_only_trips_assigned_to_authenticated_driver():
    cur = FakeCursor([[]])

    result = driver_trips(cur=cur, user={"id": "driver-a"})

    assert result == []
    query, params = cur.calls[0]
    assert "WHERE t.assigned_driver_user_id = %s" in query
    assert "t.assigned_driver_user_id IS NULL" not in query
    assert params == ("driver-a",)


def test_driver_trip_serializes_commercial_fare_fields():
    trip = _driver_trip({
        "id": 321,
        "origen": "Bahía Blanca",
        "destino": "Ingeniero White",
        "status": "assigned",
        "tracking_driver_name": "Conductor prueba",
        "tracking_plate": "AA123BB",
        "tracking_vehicle_model": "Fiat Cronos",
        "tracking_vehicle_color": "Blanco",
        "tracking_latitude": None,
        "tracking_longitude": None,
        "tracking_location_updated_at": None,
        "service_type_id": 2,
        "service_type_name": "Ahorro",
        "fare": "5054.00",
        "original_fare": "5054.00",
        "discount_percentage": "15.00",
        "discount_amount": "758.10",
        "final_fare": "4295.90",
        "fare_source": "commercial",
    })

    assert trip.model_dump() | {"id": trip.id} == {
        "id": 321,
        "origin": "Bahía Blanca",
        "destination": "Ingeniero White",
        "status": "assigned",
        "driver_name": "Conductor prueba",
        "plate": "AA123BB",
        "vehicle_model": "Fiat Cronos",
        "vehicle_color": "Blanco",
        "latitude": None,
        "longitude": None,
        "location_updated_at": None,
        "service_type_id": 2,
        "service_type_name": "Ahorro",
        "fare": 5054.00,
        "driver_fare": 4295.90,
        "original_fare": 5054.00,
        "discount_percentage": 15.00,
        "discount_amount": 758.10,
        "final_fare": 4295.90,
        "fare_source": "commercial",
    }


def test_driver_trip_keeps_legacy_fare_without_commercial_fields():
    trip = _driver_trip({
        "id": 322,
        "origen": "Bahía Blanca",
        "destino": "Punta Alta",
        "status": "completed",
        "tracking_driver_name": None,
        "tracking_plate": None,
        "tracking_vehicle_model": None,
        "tracking_vehicle_color": None,
        "tracking_latitude": None,
        "tracking_longitude": None,
        "tracking_location_updated_at": None,
        "service_type_id": None,
        "service_type_name": None,
        "fare": "3000.00",
        "original_fare": None,
        "discount_percentage": None,
        "discount_amount": None,
        "final_fare": None,
        "fare_source": None,
    })

    assert trip.fare == 3000.0
    assert trip.original_fare is None
    assert trip.final_fare is None
    assert trip.fare_source is None


def test_snapshot_preserves_the_three_canonical_statuses():
    cur = FakeCursor([[driver_row(1, "available"), driver_row(2, "unavailable"), driver_row(3, "out_of_service")]])

    snapshot = _operational_snapshot(cur)

    assert [item["operationalStatus"] for item in snapshot] == ["available", "unavailable", "out_of_service"]
    assert [item["operationalLabel"] for item in snapshot] == ["Disponible", "No disponible", "Fuera de servicio"]
    assert all(item["source"] == "livre_driver_accounts+driver_vehicle_association+livi_trips" for item in snapshot)


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
    assert params == (list(_HIDDEN_GPS_DRIVER_USER_IDS), None)
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


def test_schema_rejects_active_and_other_noncanonical_statuses():
    for status in ("active", "offline", "out-of-service"):
        try:
            DriverOperationalStatusUpdate(status=status)  # type: ignore[arg-type]
        except ValidationError:
            continue
        raise AssertionError(f"unexpected operational status accepted: {status}")
