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
    assert cursor.params[-4] == "real"
    assert cursor.params[-3] == "panel"
