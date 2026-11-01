from pathlib import Path


ROOT = Path(__file__).parents[1]
ROUTER = (ROOT / "app/mobility/router.py").read_text()
SCHEMAS = (ROOT / "app/mobility/schemas.py").read_text()
MIGRATION = (ROOT / "app/migrations/20261101_message_idempotency.sql").read_text()


def test_message_contract_accepts_idempotency_keys():
    assert "client_message_id: str | None" in SCHEMAS
    assert "ON CONFLICT DO NOTHING" in ROUTER
    assert "uq_livre_trip_messages_client_id" in MIGRATION
    assert "uq_livre_trip_messages_handie_client_id" in MIGRATION


def test_duplicate_messages_do_not_schedule_again():
    assert "inserted = message is not None" in ROUTER
    assert ROUTER.count("if inserted:\n        schedule_conversation_message") >= 2
    assert "if inserted:\n        if payload.audio_data:" in ROUTER
