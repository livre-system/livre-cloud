"""
Conexión a Postgres para HASH Cloud.
Reemplaza los JSON locales (credentials.json, memory_index.json) por tablas persistentes.
Un solo pool de conexiones, reutilizado en toda la app.
"""

from contextlib import contextmanager
import hashlib
import logging
import os
import threading
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from app.core.config import DATABASE_URL

# Se inicializa al primer acceso para no exigir PostgreSQL durante imports/tests.
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # Pool chico: FastAPI + Railway no necesitan más de esto.
                _pool = pg_pool.SimpleConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=DATABASE_URL,
                )
    return _pool


@contextmanager
def get_conn():
    """Entrega una conexión y descarta las que PostgreSQL cerró."""
    pool = _get_pool()
    conn = pool.getconn()
    discard = False
    try:
        # SimpleConnectionPool puede conservar una conexión muerta después de
        # un reinicio/corte de red del servidor. No la entregamos al caller.
        if conn.closed:
            discard = True
            pool.putconn(conn, close=True)
            conn = pool.getconn()
            discard = False
        yield conn
        conn.commit()
    except Exception:
        discard = True
        try:
            if not conn.closed:
                conn.rollback()
        except (psycopg2.Error, psycopg2.InterfaceError):
            pass
        raise
    finally:
        pool.putconn(conn, close=discard or bool(conn.closed))


@contextmanager
def get_cursor():
    """Atajo: entrega directamente un cursor (dict-like rows) ya con su conexión."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur


def get_cursor_dep():
    """
    Versión para FastAPI Depends().
    Usa yield sin @contextmanager para que FastAPI pueda manejarlo como dependencia.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        try:
            if not conn.closed:
                conn.rollback()
        except (psycopg2.Error, psycopg2.InterfaceError):
            pass
        raise
    finally:
        pool.putconn(conn, close=bool(conn.closed))




def _assert_driver_device_sessions_schema(cur) -> None:
    """Falla el arranque si la migración de sesiones no dejó el esquema listo."""
    cur.execute(
        "SELECT to_regclass('public.livre_driver_device_sessions') AS table_name"
    )
    row = cur.fetchone()
    if row is None:
        table_name = None
    else:
        try:
            table_name = row["table_name"]
        except (TypeError, KeyError, IndexError):
            table_name = row[0]
    if table_name != "livre_driver_device_sessions":
        raise RuntimeError(
            "[MIGRATIONS] Esquema incompleto: falta la tabla "
            "public.livre_driver_device_sessions. "
            "El backend no puede iniciar hasta aplicar las migraciones."
        )


def run_migrations():
    """Aplica migraciones ordenadas y verifica checksum de las ya aplicadas."""
    log = logging.getLogger(__name__)
    migrations_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(123456789);")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT NOW(),
                    checksum TEXT
                );
            """)
            # Compatibilidad con instalaciones creadas antes de los checksums.
            cur.execute("ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT")
            files = sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))
            for filename in files:
                path = os.path.join(migrations_dir, filename)
                content = open(path, "rb").read()
                checksum = hashlib.sha256(content).hexdigest()
                cur.execute("SELECT checksum FROM schema_migrations WHERE version = %s", (filename,))
                applied = cur.fetchone()
                if applied:
                    stored = applied["checksum"] if hasattr(applied, "keys") else applied[0]
                    if stored and stored != checksum:
                        raise RuntimeError(f"[MIGRATIONS] Checksum cambiado: {filename}")
                    if not stored:
                        log.warning("[MIGRATIONS] Backfill de checksum legado: %s", filename)
                        cur.execute("UPDATE schema_migrations SET checksum = %s WHERE version = %s", (checksum, filename))
                    continue
                log.info("[MIGRATIONS] Aplicando %s", filename)
                cur.execute(content.decode("utf-8"))
                cur.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (filename, checksum))
                log.info("[MIGRATIONS] Aplicado %s exitosamente", filename)
            _assert_driver_device_sessions_schema(cur)


def init_db() -> None:
    """
    Crea las tablas si no existen. Se llama una vez al arrancar la app (main.py).
    Idempotente: correr esto en cada deploy no rompe nada.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

            # Tokens de Google OAuth (reemplaza credentials.json)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    user_id TEXT PRIMARY KEY,
                    refresh_token TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            # Marca que un usuario ya tiene memoria inicializada (reemplaza memory_index.json)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_users (
                    user_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            # Documentos de memoria (antes: "documents" dentro del JSON de Drive)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_documents (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id TEXT NOT NULL REFERENCES memory_users(user_id) ON DELETE CASCADE,
                    key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (user_id, key)
                );
            """)

            # Filas dentro de cada documento (antes: "rows" dentro del JSON de Drive)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_rows (
                    id BIGSERIAL PRIMARY KEY,
                    document_id UUID NOT NULL REFERENCES memory_documents(id) ON DELETE CASCADE,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_documents_user ON memory_documents(user_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_rows_document ON memory_rows(document_id);")

            # ── Commerce ───────────────────────────────────────────────────────────

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_companies (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    owner_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_stores (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id UUID NOT NULL UNIQUE REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    display_name TEXT,
                    description TEXT,
                    currency TEXT NOT NULL DEFAULT 'ARS',
                    logo_url TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_categories (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, slug)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_products (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    category_id UUID REFERENCES commerce_categories(id) ON DELETE SET NULL,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    description TEXT,
                    price NUMERIC(12, 2) NOT NULL,
                    stock INTEGER NOT NULL DEFAULT 0,
                    image_url TEXT,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, slug)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_customers (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    name TEXT,
                    phone TEXT,
                    address TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, email)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_orders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    customer_id UUID REFERENCES commerce_customers(id) ON DELETE SET NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    total NUMERIC(12, 2) NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_order_items (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    order_id UUID NOT NULL REFERENCES commerce_orders(id) ON DELETE CASCADE,
                    product_id UUID REFERENCES commerce_products(id) ON DELETE SET NULL,
                    quantity INTEGER NOT NULL,
                    unit_price NUMERIC(12, 2) NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_connectors (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    credentials JSONB NOT NULL DEFAULT '{}',
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, provider)
                );
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_products_company ON commerce_products(company_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_orders_company ON commerce_orders(company_id);")

            # ── Usuarios propios de Livre Cloud ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS livre_users (
                    id            TEXT        PRIMARY KEY,
                    username      TEXT        UNIQUE,
                    email         TEXT        UNIQUE,
                    name          TEXT        NOT NULL,
                    password_hash TEXT        NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_livre_users_email ON livre_users(email);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_livre_users_username ON livre_users(username);")

            # Migración: agregar columna username si no existe (idempotente)
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'livre_users' AND column_name = 'username'
                    ) THEN
                        ALTER TABLE livre_users ADD COLUMN username TEXT UNIQUE;
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'livre_users'
                          AND column_name = 'email'
                          AND is_nullable = 'NO'
                    ) THEN
                        ALTER TABLE livre_users ALTER COLUMN email DROP NOT NULL;
                    END IF;
                END
                $$;
            """)
