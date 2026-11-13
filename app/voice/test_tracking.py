"""
tests/voice/test_tracking.py

Pruebas unitarias para el envío de tracking de WhatsApp.
Cubren los dos casos de producción confirmados:
  - Trip 330: whatsapp_phone=NULL, whatsapp_user contiene número real.
  - Trip 326: ElevenLabs devuelve HTTP 400.

Ejecutar con:  pytest tests/voice/test_tracking.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
import httpx

# Importar las funciones bajo test
from app.voice.elevenlabs_trip import (
    _normalize_e164,
    _resolve_recipient,
    send_tracking_message,
)


# ---------------------------------------------------------------------------
# _normalize_e164
# ---------------------------------------------------------------------------

class TestNormalizeE164:
    def test_numero_valido_con_plus(self):
        assert _normalize_e164("+5491178240367") == "+5491178240367"

    def test_numero_sin_plus_lo_agrega(self):
        assert _normalize_e164("5491178240367") == "+5491178240367"

    def test_prefijo_whatsapp_se_limpia(self):
        assert _normalize_e164("whatsapp:+5491178240367") == "+5491178240367"

    def test_prefijo_whatsapp_sin_plus(self):
        assert _normalize_e164("whatsapp:5491178240367") == "+5491178240367"

    def test_uuid_interno_rechazado(self):
        assert _normalize_e164("user:8334a30a-a6f8-497d-bb5f-622059567001") == ""

    def test_uuid_puro_rechazado(self):
        assert _normalize_e164("8334a30a-a6f8-497d-bb5f-622059567001") == ""

    def test_vacio_devuelve_vacio(self):
        assert _normalize_e164("") == ""

    def test_none_devuelve_vacio(self):
        assert _normalize_e164(None) == ""

    def test_numero_demasiado_corto_rechazado(self):
        # menos de 7 dígitos
        assert _normalize_e164("+12345") == ""

    def test_numero_demasiado_largo_rechazado(self):
        # más de 15 dígitos
        assert _normalize_e164("+12345678901234567") == ""

    def test_espacios_se_limpian(self):
        assert _normalize_e164("  +5491178240367  ") == "+5491178240367"


# ---------------------------------------------------------------------------
# _resolve_recipient
# ---------------------------------------------------------------------------

class TestResolveRecipient:
    """Caso Trip 330: whatsapp_phone=NULL, whatsapp_user tiene número real."""

    def test_whatsapp_phone_tiene_prioridad(self):
        trip = {"id": 1, "whatsapp_phone": "+5491178240367", "whatsapp_user": "+5491100000000"}
        assert _resolve_recipient(trip) == "+5491178240367"

    def test_fallback_a_whatsapp_user_si_phone_vacio(self):
        """Trip 330: whatsapp_phone vacío, whatsapp_user tiene número válido."""
        trip = {"id": 330, "whatsapp_phone": "", "whatsapp_user": "5491178240367"}
        assert _resolve_recipient(trip) == "+5491178240367"

    def test_fallback_a_whatsapp_user_si_phone_null(self):
        """Trip 330: whatsapp_phone=NULL en DB (llega como None)."""
        trip = {"id": 330, "whatsapp_phone": None, "whatsapp_user": "5491178240367"}
        assert _resolve_recipient(trip) == "+5491178240367"

    def test_uuid_en_whatsapp_user_rechazado(self):
        """user:<UUID> no debe usarse como teléfono."""
        trip = {
            "id": 1,
            "whatsapp_phone": "",
            "whatsapp_user": "user:8334a30a-a6f8-497d-bb5f-622059567001",
        }
        assert _resolve_recipient(trip) == ""

    def test_ambos_vacios_devuelve_vacio(self):
        trip = {"id": 1, "whatsapp_phone": None, "whatsapp_user": None}
        assert _resolve_recipient(trip) == ""

    def test_whatsapp_user_con_prefijo_whatsapp(self):
        trip = {"id": 1, "whatsapp_phone": "", "whatsapp_user": "whatsapp:+5491178240367"}
        assert _resolve_recipient(trip) == "+5491178240367"

    def test_whatsapp_phone_invalido_cae_a_user(self):
        """whatsapp_phone existe pero es UUID, debe caer al fallback."""
        trip = {
            "id": 1,
            "whatsapp_phone": "user:8334a30a-a6f8-497d-bb5f-622059567001",
            "whatsapp_user": "5491178240367",
        }
        assert _resolve_recipient(trip) == "+5491178240367"


# ---------------------------------------------------------------------------
# send_tracking_message — casos de error
# ---------------------------------------------------------------------------

TRIP_BASE = {
    "id": 330,
    "tracking_token": "tok_abc123",
    "whatsapp_phone": None,
    "whatsapp_user": "5491178240367",
}


class TestSendTrackingMessage:
    def _mock_response(self, status_code: int, json_body: dict) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.is_error = status_code >= 400
        resp.json.return_value = json_body
        resp.text = str(json_body)
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=resp)
            if status_code >= 400 else None
        )
        return resp

    @patch("app.voice.elevenlabs_trip.httpx.post")
    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test_key",
        "ELEVENLABS_WHATSAPP_TRACKING_TEMPLATE_NAME": "tracking_link",
    })
    def test_trip_330_fallback_whatsapp_user(self, mock_post):
        """Trip 330: whatsapp_phone=NULL, usa whatsapp_user como fallback y envía OK."""
        mock_post.return_value = self._mock_response(200, {"conversation_id": "conv_ok"})

        result = send_tracking_message(TRIP_BASE)

        assert result == "conv_ok"
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["whatsapp_user_id"] not in {"", "user:8334a30a-a6f8-497d-bb5f-622059567001"}

    @patch("app.voice.elevenlabs_trip.httpx.post")
    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test_key",
        "ELEVENLABS_WHATSAPP_TRACKING_TEMPLATE_NAME": "tracking_link",
    })
    def test_trip_326_http_400_lanza_excepcion_con_log(self, mock_post):
        """Trip 326: ElevenLabs devuelve 400, debe lanzar excepción y loguear body."""
        mock_post.return_value = self._mock_response(
            400, {"error": "invalid_phone_number", "code": 131009}
        )
        trip = {**TRIP_BASE, "whatsapp_phone": "5491178240367", "whatsapp_user": ""}

        with pytest.raises(httpx.HTTPStatusError):
            send_tracking_message(trip)

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": ""})
    def test_sin_api_key_lanza_error(self):
        with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
            send_tracking_message(TRIP_BASE)

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test_key"})
    def test_sin_tracking_token_lanza_error(self):
        trip = {**TRIP_BASE, "tracking_token": None}
        with pytest.raises(RuntimeError, match="tracking_token"):
            send_tracking_message(trip)

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test_key"})
    def test_uuid_en_ambos_campos_lanza_error(self):
        """Ningún campo contiene un teléfono válido."""
        trip = {
            **TRIP_BASE,
            "whatsapp_phone": "user:8334a30a-a6f8-497d-bb5f-622059567001",
            "whatsapp_user": "user:8334a30a-a6f8-497d-bb5f-622059567001",
        }
        with pytest.raises(RuntimeError, match="WhatsApp User ID reutilizable"):
            send_tracking_message(trip)

    @patch("app.voice.elevenlabs_trip.httpx.post")
    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test_key",
        "ELEVENLABS_WHATSAPP_TRACKING_TEMPLATE_NAME": "tracking_link",
    })
    def test_payload_contiene_campos_correctos(self, mock_post):
        """Verifica que el payload que se manda a ElevenLabs tiene la estructura correcta."""
        mock_post.return_value = self._mock_response(200, {"conversation_id": "conv_xyz"})
        trip = {**TRIP_BASE, "whatsapp_phone": "+5491178240367"}

        send_tracking_message(trip)

        payload = mock_post.call_args[1]["json"]
        assert "whatsapp_phone_number_id" in payload
        assert "whatsapp_user_id" in payload
        assert "template_name" in payload
        assert "template_language_code" in payload
        assert "template_params" in payload
        assert "agent_id" in payload
        assert payload["template_params"][0]["type"] == "body"
        assert "text" in payload["template_params"][0]["parameters"][0]

    @patch("app.voice.elevenlabs_trip.httpx.post", side_effect=httpx.TimeoutException("timeout"))
    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test_key",
        "ELEVENLABS_WHATSAPP_TRACKING_TEMPLATE_NAME": "tracking_link",
    })
    def test_timeout_lanza_error(self, mock_post):
        trip = {**TRIP_BASE, "whatsapp_phone": "+5491178240367"}
        with pytest.raises(RuntimeError, match="Timeout"):
            send_tracking_message(trip)
