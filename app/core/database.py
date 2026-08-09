"""
Conexión a Postgres para HASH Cloud.
Reemplaza los JSON locales (credentials.json, memory_index.json) por tablas persistentes.
Un solo pool de conexiones, reutilizado en toda la app.
"""

from contextlib import contextmanager
import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from app.core.config import DATABASE_URL

# Pool chico: FastAPI + Railway no necesitan más de esto para arrancar.
# Ajustá maxconn si ves errores de "connection pool exhausted" bajo carga.
_pool = pg_pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DATABASE_URL,
)


@contextmanager
def get_conn():
    """Contexto que entrega una conexión del pool y hace commit/rollback automático."""
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


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
    conn = _pool.getconn()
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


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
