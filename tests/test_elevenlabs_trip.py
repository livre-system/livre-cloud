from decimal import Decimal

import app.voice.elevenlabs_trip as trip
from app.mobility.router import should_send_tracking_message


def test_ahorro_requires_advance_payment_only_for_passenger_channels():
    assert trip.requires_advance_payment("2", "whatsapp") is True
    assert trip.requires_advance_payment("2", "passenger") is True
    assert trip.requires_advance_payment("2", "panel") is False
    assert trip.requires_advance_payment("2", "crm") is False
    assert trip.requires_advance_payment("1", "whatsapp") is False


def test_arriving_message_contains_private_passenger_tracking_url(monkeypatch):
    monkeypatch.setattr(trip, "TRACKING_BASE_URL", "https://pasajeros.livre.com.ar/seguimiento")
    message = trip.arriving_message({"id": 7, "tracking_token": "tok-abc"})
    assert "https://pasajeros.livre.com.ar/seguimiento/?token=tok-abc" in message


def test_tracking_message_is_sent_when_premium_moves_from_assigned_to_arriving():
    assert should_send_tracking_message("assigned", "arriving") is True
    assert should_send_tracking_message("searching", "arriving") is True
    assert should_send_tracking_message("assigned", "in_progress") is False


def test_missing_service_id_resolves_from_unique_confirmed_fare():
    options = [
        {"transportTypeId": "1", "pricePointToPoint": 9200},
        {"transportTypeId": "2", "pricePointToPoint": 7360},
    ]
    assert trip.resolve_service_type_id(None, "", "9200", options) == "1"
    assert trip.resolve_service_type_id(None, "", "7360", options) == "2"
    assert trip.resolve_service_type_id(None, "", None, options) is None


def test_explicit_service_id_wins_over_label_and_fare():
    assert trip.resolve_service_type_id("1", "Ahorro", "7360", []) == "1"


class Cursor:
    def __init__(self):
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return {"id": 123}


class CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *args):
        return False


def test_save_trip_binds_all_insert_columns(monkeypatch):
    cursor = Cursor()
    monkeypatch.setattr(trip, "get_cursor", lambda: CursorContext(cursor))

    trip_id, _ = trip._save_trip(
        conversation_id="conv-1",
        origen="Origen",
        destino="Destino",
        whatsapp_user="5492915650870",
        service_type_id="2",
        fare=Decimal("4426"),
        service_type_name="Ahorro",
        quote_id="quote-1",
        activity_type="real",
        source="panel",
    )

    assert trip_id == 123
    assert cursor.query is not None
    assert cursor.params is not None
    assert cursor.query.count("%s") == len(cursor.params)
    assert cursor.params[:5] == ("conv-1", "Origen", "Destino", "5492915650870", "")
    assert isinstance(cursor.params[5], str)
    assert cursor.params[-11] == "real"
    assert cursor.params[-10] == "panel"
    assert cursor.params[-9] == "searching"
    assert cursor.params[-8] is None
    assert "scheduled_at" in cursor.query


def test_save_trip_binds_scheduled_state_and_time(monkeypatch):
    from datetime import datetime, timezone

    cursor = Cursor()
    monkeypatch.setattr(trip, "get_cursor", lambda: CursorContext(cursor))
    scheduled_at = datetime(2030, 1, 1, tzinfo=timezone.utc)

    trip._save_trip(
        conversation_id="conv-scheduled",
        origen="Origen",
        destino="Destino",
        status="scheduled",
        scheduled_at=scheduled_at,
    )

    assert cursor.params[-10] == "whatsapp"
    assert cursor.params[-9] == "scheduled"
    assert cursor.params[-8] == scheduled_at


def test_format_quote_message_numbers_options_and_uses_lines():
    message = trip._format_quote_message([
        {"serviceName": "Livre Premium", "pricePointToPoint": 4410},
        {"serviceName": "Livre Ahorro", "pricePointToPoint": 3308},
    ])

    assert message == (
        "Estas son las opciones para tu viaje:\n"
        "1. Livre Premium: $4410 (efectivo, en el momento)\n"
        "2. Livre Ahorro: $3308 (pago anticipado con tarjeta débito/crédito o Mercado Pago)\n"
        "Elegí una opción. Premium se paga en efectivo; Ahorro requiere pagar antes de confirmar."
    )
