from app.crm.router import (
    _HIDDEN_GPS_DRIVER_USER_IDS,
    _local_trip_page,
    _operational_snapshot,
    _trip_status_semantics,
    assign_trip_to_driver,
    operational,
    update_operational_driver_status,
)
from fastapi import HTTPException
from pydantic import ValidationError
from datetime import datetime, timedelta, timezone

from app.mobility.schemas import DriverOperationalStatusUpdate
from app.mobility.router import _driver_trip, driver_trips


def test_trip_status_semantics_does_not_promote_errors_to_open():
    semantics = _trip_status_semantics({
        ("pending", "unclassified"): 2,
        ("completed", "unclassified"): 68,
        ("magiis_error", "unclassified"): 17,
        ("cancel_error", "unclassified"): 1,
        ("future_state", "unclassified"): 3,
    })
    assert semantics["open"] == 2
    assert semantics["terminal"] == 86
    assert semantics["unknown"] == 3
    assert semantics["by_status"]["magiis_error"] == 17
    assert "magiis_error" in semantics["terminal_statuses"]


def test_live_travels_uses_argentina_calendar_for_today_filter():
    cur = FakeCursor([
        [{"status": "searching", "total": 1}],
        {"total": 1},
        [{"id": 42, "status": "searching"}],
        [],
        [],
    ])

    result = _local_trip_page(cur, 1, 20, state="SEARCHING_DRIVER", date_from="2026-10-22", date_to="2026-10-22")

    assert result["total"] == 1
    assert "created_at AT TIME ZONE 'America/Argentina/Buenos_Aires'" in cur.calls[0][0]
    assert "created_at AT TIME ZONE 'America/Argentina/Buenos_Aires'" in cur.calls[1][0]
    assert "created_at AT TIME ZONE 'America/Argentina/Buenos_Aires'" in cur.calls[2][0]


class FakeCursor:
    def __init__(self, rows_by_query, schema_rows=None):
        self.rows_by_query = list(rows_by_query)
        self.schema_rows = schema_rows or [
            {"table_name": "livi_trips", "column_name": column}
            for column in ("status", "activity_type", "source", "whatsapp_user")
        ] + [
            {"table_name": table, "column_name": "id"}
            for table in ("magiis_quote_requests", "livi_whatsapp_quotes", "memory_documents", "memory_rows")
        ] + [
            {"table_name": "memory_rows", "column_name": "document_id"},
            {"table_name": "memory_documents", "column_name": "key"},
        ]
        self._schema_pending = False
        self.calls = []
        self.rowcount = 1

    def execute(self, query, params=()):
        self.calls.append((query, params))
        self._schema_pending = "information_schema.columns" in query

    def fetchall(self):
        if self._schema_pending:
            self._schema_pending = False
            return self.schema_rows
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


def test_live_travels_includes_driver_accepted_states_in_progress_tab():
    cur = FakeCursor([
        [{"status": "in_progress", "unassigned": False, "total": 1}],
        {"total": 1},
        [{"id": 99, "status": "in_progress", "assigned_driver_user_id": "driver-a"}],
        [],
        [],
    ])

    result = _local_trip_page(cur, 1, 20, state="CLOSING_TRAVEL-GOING_TO_CLIENT-GOING_TO_DESTINATION")

    assert result["total"] == 1
    query, params = cur.calls[1]
    assert "status IN ('confirmed', 'assigned', 'arriving', 'in_progress')" in query
    assert "assigned_driver_user_id IS NULL" not in query
    assert params == []


def test_driver_trips_returns_available_open_trips_and_own_assignments():
    cur = FakeCursor([[]])

    result = driver_trips(cur=cur, user={"id": "driver-a"})

    assert result == []
    query, params = next(
        call for call in cur.calls if "t.status IN ('searching')" in call[0]
    )
    assert "active_driver.operational_status = 'available'" in query
    assert "t.assigned_driver_user_id IS NULL" in query
    assert "t.assigned_driver_user_id = %s" in query
    assert "t.status IN ('assigned', 'arriving', 'in_progress')" in query
    assert params == ("driver-a", "driver-a")


def test_crm_assignment_is_atomic_and_requires_available_driver():
    cur = FakeCursor([{
        "id": 77,
        "origen": "Bahía Blanca",
        "destino": "Ingeniero White",
        "status": "assigned",
        "assigned_driver_user_id": 12,
        "tracking_driver_name": "Driver 12",
        "created_at": None,
        "updated_at": None,
    }])

    result = assign_trip_to_driver(
        77,
        {"driver_user_id": 12},
        user={"id": "livreuser"},
        cur=cur,
    )

    assert result["status"] == "assigned"
    query, params = cur.calls[0]
    assert "t.assigned_driver_user_id IS NULL" in query
    assert "a.operational_status = 'available'" in query
    assert params == (77, 12, 12)


def test_driver_trip_serializes_commercial_fare_fields():
    trip = _driver_trip({
        "id": 321,
        "origen": "Bahía Blanca",
        "destino": "Ingeniero White",
        "status": "assigned",
        "created_at": None,
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
        "created_at": None,
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
        "driver_fare": 5054.00,
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
        "created_at": None,
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


def test_snapshot_keeps_old_position_and_marks_it_stale():
    old = driver_row("old", "available")
    old["location_updated_at"] = datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)
    recent = driver_row("recent", "available")
    recent["location_updated_at"] = datetime.now(timezone.utc) - timedelta(hours=23)
    cur = FakeCursor([[old, recent]])

    snapshot = _operational_snapshot(cur)

    assert snapshot[0]["lastPlace"] == {"latitude": -38.7, "longitude": -62.2}
    assert snapshot[0]["locationFresh"] is False
    assert snapshot[1]["lastPlace"] == {"latitude": -38.7, "longitude": -62.2}
    assert snapshot[1]["locationFresh"] is True


def test_snapshot_uses_the_persistent_driver_vehicle_association():
    row = driver_row("barroso", "available")
    row.update({
        "associated_vehicle_plate": "406 AG8008HE",
        "associated_vehicle_model": "FIAT CRONOS",
        "associated_vehicle_color": "ROJO",
    })
    cur = FakeCursor([[row]])

    snapshot = _operational_snapshot(cur)

    assert snapshot[0]["vehicle"] == {
        "plate": "406 AG8008HE",
        "model": "FIAT CRONOS",
        "color": "ROJO",
    }
    query, _ = cur.calls[0]
    assert "livre_crm_vehicles" in query
    assert "vehicle.source_magiis_id = dv.vehicle_magiis_id" in query
    assert "vehicle.model AS vehicle_model" in query


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
    cur = FakeCursor([rows, [], {
        "trips_total": 0,
        "whatsapp_trip_requests": 0,
        "crm_trip_requests": 0,
        "test_trips_total": 0,
        "whatsapp_quote_requests": 0,
        "whatsapp_quotes_total": 0,
        "whatsapp_conversations_with_transcript": 0,
    }])
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


def test_crm_operational_separates_trip_activity_and_deduplicates_vehicles():
    first = driver_row(1, "available")
    first["associated_vehicle_plate"] = "AA111AA"
    second = driver_row(2, "available", "assigned")
    second["associated_vehicle_plate"] = "AA111AA"
    third = driver_row(3, "out_of_service")
    third["associated_vehicle_plate"] = "BB222BB"
    cur = FakeCursor([
        [first, second, third],
        [
            {"status": "completed", "activity_type": "real", "total": 4},
            {"status": "completed", "activity_type": "test", "total": 2},
            {"status": "completed", "activity_type": "unclassified", "total": 7},
            {"status": "assigned", "activity_type": "real", "total": 1},
        ],
        {
            "trips_total": 14,
            "whatsapp_trip_requests": 5,
            "crm_trip_requests": 9,
            "test_trips_total": 2,
            "whatsapp_quote_requests": 3,
            "whatsapp_quotes_total": 3,
            "whatsapp_conversations_with_transcript": 4,
        },
    ])

    result = operational(user={"id": "crm"}, cur=cur)

    assert result["status"] == {
        "travelsQuantity": 4,
        "testTravelsQuantity": 2,
        "unclassifiedTravelsQuantity": 7,
        "workingVehiclesQuantity": 2,
        "kilometersTotal": None,
        "clientsIndividualsAmount": None,
    }
    assert result["driver_totals"]["totalDriversInTravel"] == 1
    assert result["driver_totals"]["available"] == 1
    assert result["vehicle_totals"] == {"identified": 2, "unidentified": 1}
    assert result["activity"]["source"] == "livre-cloud"
    assert result["activity"]["trips"]["total"] == 14
    assert result["activity"]["whatsapp"] == {
        "trip_requests": 5,
        "quote_requests": 3,
        "quotes": 3,
        "conversations_with_transcript": 4,
    }
    assert result["activity"]["crm"] == {"trip_requests": 9}
    assert "CURRENT_TIMESTAMP AT TIME ZONE 'America/Argentina/Buenos_Aires'" in cur.calls[2][0]
    assert "CURRENT_TIMESTAMP AT TIME ZONE 'America/Argentina/Buenos_Aires'" in cur.calls[3][0]


def test_crm_operational_counts_legacy_null_activity_as_unclassified():
    cur = FakeCursor([
        [],
        [{"status": "completed", "activity_type": "unclassified", "total": 3}],
        {
            "trips_total": 3,
            "whatsapp_trip_requests": 0,
            "crm_trip_requests": 0,
            "test_trips_total": 0,
            "whatsapp_quote_requests": 0,
            "whatsapp_quotes_total": 0,
            "whatsapp_conversations_with_transcript": 0,
        },
    ])

    result = operational(user={"id": "crm"}, cur=cur)

    assert result["status"]["unclassifiedTravelsQuantity"] == 3
    assert "COALESCE(activity_type, 'unclassified')" in cur.calls[2][0]


def test_crm_operational_uses_explicit_legacy_capabilities_without_querying_new_objects():
    legacy_schema = [
        {"table_name": "livi_trips", "column_name": column}
        for column in ("status", "whatsapp_user")
    ] + [
        {"table_name": "magiis_quote_requests", "column_name": "id"},
        {"table_name": "memory_documents", "column_name": "id"},
        {"table_name": "memory_documents", "column_name": "key"},
        {"table_name": "memory_rows", "column_name": "id"},
        {"table_name": "memory_rows", "column_name": "document_id"},
    ]
    cur = FakeCursor(
        [
            [],
            [{"status": "completed", "activity_type": "unclassified", "total": 2}],
            {
                "trips_total": 2,
                "whatsapp_trip_requests": 1,
                "crm_trip_requests": 0,
                "test_trips_total": None,
                "whatsapp_quote_requests": 4,
                "whatsapp_quotes_total": None,
                "whatsapp_conversations_with_transcript": 2,
            },
        ],
        schema_rows=legacy_schema,
    )

    result = operational(user={"id": "crm"}, cur=cur)

    assert result["activity"]["schema"]["status"] == "legacy"
    assert result["activity"]["trips"]["test_total"] is None
    assert result["activity"]["whatsapp"]["quotes"] is None
    assert "livi_whatsapp_quotes" not in cur.calls[3][0]
    assert "COALESCE(activity_type" not in cur.calls[2][0]


def test_operational_schema_rejects_missing_required_trips_status():
    cur = FakeCursor([[]], schema_rows=[{"table_name": "livi_trips", "column_name": "id"}])

    try:
        operational(user={"id": "crm"}, cur=cur)
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail["code"] == "CRM_SCHEMA_INCOMPATIBLE"
    else:
        raise AssertionError("missing required schema was accepted")


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


def test_operational_cleanup_migration_drops_defaults_and_preserves_explicit_labels():
    migration = open("app/migrations/20261019_operational_activity_audit_cleanup.sql").read()
    assert "ALTER COLUMN activity_type DROP NOT NULL" in migration
    assert "ALTER COLUMN source DROP NOT NULL" in migration
    assert "ALTER COLUMN activity_type DROP DEFAULT" in migration
    assert "ALTER COLUMN source DROP DEFAULT" in migration
    assert "SET activity_type = NULL" in migration
    assert "source = NULL" in migration
    assert "activity_type = 'unclassified'" in migration
    assert "source = 'unknown'" in migration
    assert "activity_type = 'real'" not in migration
    assert "activity_type = 'test'" not in migration
