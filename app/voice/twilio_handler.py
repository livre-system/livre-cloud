"""
app/voice/twilio_handler.py

Maneja el flujo de llamadas entrantes de Twilio:
  1. Twilio llama a /voice/incoming → responde con TwiML para grabar
  2. Twilio manda el audio grabado a /voice/transcribe
  3. Se transcribe con Groq, se genera respuesta con Gemini, se sintetiza con Fish
  4. Se guarda la conversación en el chat como mensajes normales
"""

from twilio.twiml.voice_response import VoiceResponse, Gather
import os

TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
BASE_URL = os.getenv("BASE_URL", "")  # ej: https://api.livre.app


def build_gather_response(action_url: str, say_text: str = None) -> str:
    """
    Construye TwiML que saluda y graba la voz del usuario.
    Usa <Gather> con input=speech para captura en tiempo real.
    """
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="es-AR",
        speech_timeout=3,
        timeout=8,
        speech_model="phone_call",
        enhanced=True,
    )

    if say_text:
        gather.say(say_text, language="es-AR", voice="Google.es-US-Neural2-A")
    else:
        gather.say(
            "Hola, soy LIVRE IA. ¿En qué te puedo ayudar?",
            language="es-AR",
            voice="Google.es-US-Neural2-A",
        )

    response.append(gather)

    # Si no habla, redirigir al mismo endpoint
    response.redirect(action_url, method="POST")

    return str(response)


def build_reply_response(reply_text: str, action_url: str) -> str:
    """
    Construye TwiML que lee la respuesta de la IA y vuelve a escuchar.
    """
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="es-AR",
        speech_timeout=3,
        timeout=8,
        speech_model="phone_call",
        enhanced=True,
    )

    gather.say(reply_text, language="es-AR", voice="Google.es-US-Neural2-A")
    response.append(gather)

    # Si no habla después de la respuesta, cerrar la llamada
    response.say("Gracias por contactar a Livre. ¡Hasta luego!", language="es-AR", voice="Google.es-US-Neural2-A")
    response.hangup()

    return str(response)


def build_error_response() -> str:
    """TwiML de error genérico."""
    response = VoiceResponse()
    response.say(
        "Hubo un problema técnico. Por favor intentá de nuevo en unos minutos.",
        language="es-AR",
        voice="Google.es-US-Neural2-A",
    )
    response.hangup()
    return str(response)
