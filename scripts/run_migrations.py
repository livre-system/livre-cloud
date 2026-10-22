"""Job controlado para Railway: aplicar migraciones antes de levantar la API."""
from app.core.database import run_migrations


if __name__ == "__main__":
    run_migrations()
    print("migrations: ok")
