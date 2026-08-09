"""
Tablas de Hash Commerce.
Se llama desde init_db() en app/core/database.py al arrancar la app.
Todas las tablas están asociadas a company_id (multi-tenant).
"""

from app.core.database import get_conn


def init_commerce_db() -> None:
    """
    Crea las tablas de commerce si no existen.
    Idempotente: se puede correr en cada deploy sin romper nada.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:

            # ── Empresas ──────────────────────────────────────────────────────
            # Una empresa es el tenant. Cada comercio es una company.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_companies (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    owner_id    TEXT NOT NULL UNIQUE,
                    name        TEXT NOT NULL,
                    slug        TEXT NOT NULL UNIQUE,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            # Usuarios que pertenecen a una empresa
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_company_members (
                    company_id  UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    user_id     TEXT NOT NULL,
                    role        TEXT NOT NULL DEFAULT 'owner',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (company_id, user_id)
                );
            """)

            # ── Configuración de la tienda ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_stores (
                    company_id       UUID PRIMARY KEY REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    display_name     TEXT,
                    description      TEXT,
                    currency         TEXT NOT NULL DEFAULT 'ARS',
                    logo_url         TEXT,
                    favicon_url      TEXT,
                    banner_url       TEXT,
                    primary_color    TEXT,
                    secondary_color  TEXT,
                    business_name    TEXT,
                    business_email   TEXT,
                    business_phone   TEXT,
                    business_address TEXT,
                    custom_domain    TEXT,
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            # ── Categorías ────────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_categories (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id  UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    name        TEXT NOT NULL,
                    slug        TEXT NOT NULL,
                    description TEXT,
                    image_url   TEXT,
                    active      BOOLEAN NOT NULL DEFAULT true,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, slug)
                );
            """)

            # ── Productos ─────────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_products (
                    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id   UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    category_id  UUID REFERENCES commerce_categories(id) ON DELETE SET NULL,
                    name         TEXT NOT NULL,
                    slug         TEXT NOT NULL,
                    description  TEXT,
                    price        NUMERIC(12, 2) NOT NULL,
                    stock        INTEGER NOT NULL DEFAULT 0,
                    image_url    TEXT,
                    image_url_2  TEXT,
                    image_url_3  TEXT,
                    active       BOOLEAN NOT NULL DEFAULT true,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, slug)
                );
            """)

            # ── Clientes ──────────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_customers (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id  UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    name        TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    phone       TEXT,
                    address     TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, email)
                );
            """)

            # ── Pedidos ───────────────────────────────────────────────────────
            # status: pending | paid | shipped | delivered | cancelled
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_orders (
                    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id   UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    customer_id  UUID REFERENCES commerce_customers(id) ON DELETE SET NULL,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    total        NUMERIC(12, 2) NOT NULL,
                    notes        TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            # Líneas de cada pedido
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_order_items (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    order_id    UUID NOT NULL REFERENCES commerce_orders(id) ON DELETE CASCADE,
                    product_id  UUID REFERENCES commerce_products(id) ON DELETE SET NULL,
                    name        TEXT NOT NULL,
                    price       NUMERIC(12, 2) NOT NULL,
                    quantity    INTEGER NOT NULL
                );
            """)

            # ── Conectores ────────────────────────────────────────────────────
            # provider: 'mercadopago' | 'paypal' | 'stripe' | etc.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS commerce_connectors (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id    UUID NOT NULL REFERENCES commerce_companies(id) ON DELETE CASCADE,
                    provider      TEXT NOT NULL,
                    access_token  TEXT,
                    refresh_token TEXT,
                    extra         JSONB,
                    active        BOOLEAN NOT NULL DEFAULT true,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (company_id, provider)
                );
            """)

            # ── Índices ───────────────────────────────────────────────────────
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_products_company   ON commerce_products(company_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_categories_company ON commerce_categories(company_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_orders_company     ON commerce_orders(company_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_customers_company  ON commerce_customers(company_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_connectors_company ON commerce_connectors(company_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_commerce_members_user       ON commerce_company_members(user_id);")
