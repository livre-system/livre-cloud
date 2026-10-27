from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

SESSION_SECRET = os.getenv("SESSION_SECRET")

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

CREDENTIALS_SECRET = os.getenv("CREDENTIALS_SECRET")

# Firebase Authentication de la app web de pasajeros.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "livre-pasajeros-668b4")

# Postgres — reemplaza credentials.json y memory_index.json
# En Railway: agregá un plugin de Postgres y copiá la variable DATABASE_URL que te genera.
DATABASE_URL = os.getenv("DATABASE_URL")

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
BASE_URL = os.getenv("BASE_URL")

# Outbox: opcional hasta configurar un consumidor autorizado.
OUTBOX_WEBHOOK_URL = os.getenv("OUTBOX_WEBHOOK_URL")

# Las migraciones se ejecutan como job controlado; el arranque automático es opt-in.
AUTO_MIGRATE = os.getenv("AUTO_MIGRATE", "false").lower() in {"1", "true", "yes"}


def validate_runtime_config() -> None:
    """Falla temprano cuando el proceso se intenta arrancar sin secretos críticos."""
    environment = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).lower()
    required = {"JWT_SECRET": JWT_SECRET, "SESSION_SECRET": SESSION_SECRET, "DATABASE_URL": DATABASE_URL}
    missing = [name for name, value in required.items() if not value]
    if missing and environment in {"production", "prod", "staging"}:
        raise RuntimeError("Configuración incompleta: faltan " + ", ".join(missing))

# ─────────────────────────────────────────────────────────────────────────────
# Memoria global de Livre Cloud
# Toda la memoria operativa de LIVI se almacena bajo este user_id único.
# El chatbot busca siempre en este espacio, sin aislar por usuario autenticado.
# ─────────────────────────────────────────────────────────────────────────────
MEMORY_USER_ID = "elevenlabs_voice"
