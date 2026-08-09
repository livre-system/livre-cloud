from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
SESSION_SECRET = os.getenv("SESSION_SECRET")

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

CREDENTIALS_SECRET = os.getenv("CREDENTIALS_SECRET")

# Postgres — reemplaza credentials.json y memory_index.json
# En Railway: agregá un plugin de Postgres y copiá la variable DATABASE_URL que te genera.
DATABASE_URL = os.getenv("DATABASE_URL")
