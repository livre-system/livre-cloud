from decimal import Decimal

import app.voice.elevenlabs_trip as trip


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
    assert cursor.params[-6] == "real"
    assert cursor.params[-5] == "panel"
    assert cursor.params[-4] == "searching"
    assert cursor.params[-3] is None
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

    assert cursor.params[-5] == "whatsapp"
    assert cursor.params[-4] == "scheduled"
    assert cursor.params[-3] == scheduled_at


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
