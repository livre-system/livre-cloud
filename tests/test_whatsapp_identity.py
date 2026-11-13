from app.voice.elevenlabs_trip import _whatsapp_user


def test_captures_system_caller_id_as_whatsapp_identity():
    payload = {"conversation_id": "conv-1", "data": {"system__caller_id": "5491178240367"}}
    assert _whatsapp_user(payload) == "5491178240367"


def test_rejects_business_phone_number_id_as_passenger_identity(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID", "1364913613361192")
    payload = {"data": {"system__caller_id": "1364913613361192"}}
    assert _whatsapp_user(payload) == ""


def test_rejects_internal_user_uuid_as_whatsapp_identity():
    payload = {"data": {"system__caller_id": "user:8334a30a-a6f8-497d-bb5f-622059567001"}}
    assert _whatsapp_user(payload) == ""



