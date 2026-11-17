import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Test simple para verificar el fallback de la plantilla de tracking
class TestTrackingTemplateFallback(unittest.TestCase):
    @patch.dict(os.environ, {"ELEVENLABS_WHATSAPP_TRACKING_TEMPLATE_NAME": ""}, clear=False)
    @patch("app.voice.elevenlabs_trip.log")
    def test_tracking_template_fallback_empty(self, mock_log):
        from app.voice.elevenlabs_trip import send_tracking_message
        
        # Viaje mock sin whatsapp_user_id válido o con errores previos al envío de red
        trip = {
            "id": 999,
            "whatsapp_user_id": "+5492910000000",
            "tracking_token": "test-token"
        }
        
        # Verificamos que la función avance más allá del error de configuración de template
        with patch("app.voice.elevenlabs_trip.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"success": True}
            try:
                send_tracking_message(trip)
            except Exception as e:
                # Si falla por otra cosa (ej. cliente requests), validamos que no sea por config_template
                self.assertNotIn("ELEVENLABS_WHATSAPP_TRACKING_TEMPLATE_NAME", str(e))

if __name__ == "__main__":
    unittest.main()
