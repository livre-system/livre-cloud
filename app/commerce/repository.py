from typing import Optional
from psycopg2.extras import RealDictCursor


# ── Companies ──────────────────────────────────────────────

def get_company_by_user(cursor, user_id: str):
    cursor.execute(
        "SELECT * FROM commerce_companies WHERE owner_id = %s",
        (user_id,)
    )
    return cursor.fetchone()


def create_company(cursor, user_id: str, data: dict):
    cursor.execute(
        """
        INSERT INTO commerce_companies (owner_id, name, slug)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (user_id, data["name"], data["slug"])
    )
    return cursor.fetchone()


# ── Store config ───────────────────────────────────────────

def get_store(cursor, company_id: str):
    cursor.execute(
        "SELECT * FROM commerce_stores WHERE company_id = %s",
        (company_id,)
    )
    return cursor.fetchone()


def upsert_store(cursor, company_id: str, data: dict):
    cursor.execute(
        """
        INSERT INTO commerce_stores (company_id, display_name, description, currency, logo_url, banner_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description  = EXCLUDED.description,
            currency     = EXCLUDED.currency,
            logo_url     = EXCLUDED.logo_url,
            banner_url   = EXCLUDED.banner_url,
            updated_at   = NOW()
        RETURNING *
        """,
        (company_id, data.get("display_name"), data.get("description"),
         data.get("currency", "ARS"), data.get("logo_url"), data.get("banner_url"))
    )
    return cursor.fetchone()


# ── Categories ─────────────────────────────────────────────

def list_categories(cursor, company_id: str):
    cursor.execute(
        "SELECT * FROM commerce_categories WHERE company_id = %s ORDER BY name",
        (company_id,)
    )
    return cursor.fetchall()


def create_category(cursor, company_id: str, data: dict):
    cursor.execute(
        """
        INSERT INTO commerce_categories (company_id, name, slug)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (company_id, data["name"], data["slug"])
    )
    return cursor.fetchone()


def update_category(cursor, company_id: str, category_id: str, data: dict):
    cursor.execute(
        """
        UPDATE commerce_categories
        SET name = %s, slug = %s, updated_at = NOW()
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        (data["name"], data["slug"], category_id, company_id)
    )
    return cursor.fetchone()


def delete_category(cursor, company_id: str, category_id: str):
    cursor.execute(
        "DELETE FROM commerce_categories WHERE id = %s AND company_id = %s",
        (category_id, company_id)
    )


# ── Products ───────────────────────────────────────────────

def list_products(cursor, company_id: str, category_id: Optional[str] = None):
    if category_id:
        cursor.execute(
            "SELECT * FROM commerce_products WHERE company_id = %s AND category_id = %s ORDER BY name",
            (company_id, category_id)
        )
    else:
        cursor.execute(
            "SELECT * FROM commerce_products WHERE company_id = %s ORDER BY name",
            (company_id,)
        )
    return cursor.fetchall()


def get_product(cursor, company_id: str, product_id: str):
    cursor.execute(
        "SELECT * FROM commerce_products WHERE id = %s AND company_id = %s",
        (product_id, company_id)
    )
    return cursor.fetchone()


def create_product(cursor, company_id: str, data: dict):
    imgs = data.get("images") or []
    url1 = imgs[0] if len(imgs) > 0 else data.get("image_url")
    url2 = imgs[1] if len(imgs) > 1 else None
    url3 = imgs[2] if len(imgs) > 2 else None
    cursor.execute(
        """
        INSERT INTO commerce_products
            (company_id, category_id, name, slug, description, price, stock,
             image_url, image_url_2, image_url_3, active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            company_id, data.get("category_id"), data["name"], data["slug"],
            data.get("description"), data["price"], data.get("stock", 0),
            url1, url2, url3, data.get("active", True)
        )
    )
    return cursor.fetchone()


def update_product(cursor, company_id: str, product_id: str, data: dict):
    imgs = data.get("images") or []
    url1 = imgs[0] if len(imgs) > 0 else data.get("image_url")
    url2 = imgs[1] if len(imgs) > 1 else None
    url3 = imgs[2] if len(imgs) > 2 else None
    cursor.execute(
        """
        UPDATE commerce_products SET
            category_id = %s, name = %s, slug = %s, description = %s,
            price = %s, stock = %s, image_url = %s, image_url_2 = %s, image_url_3 = %s,
            active = %s, updated_at = NOW()
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        (
            data.get("category_id"), data["name"], data["slug"],
            data.get("description"), data["price"], data.get("stock", 0),
            url1, url2, url3, data.get("active", True),
            product_id, company_id
        )
    )
    return cursor.fetchone()


def delete_product(cursor, company_id: str, product_id: str):
    cursor.execute(
        "DELETE FROM commerce_products WHERE id = %s AND company_id = %s",
        (product_id, company_id)
    )


# ── Customers ──────────────────────────────────────────────

def list_customers(cursor, company_id: str):
    cursor.execute(
        "SELECT * FROM commerce_customers WHERE company_id = %s ORDER BY created_at DESC",
        (company_id,)
    )
    return cursor.fetchall()


def get_customer(cursor, company_id: str, customer_id: str):
    cursor.execute(
        "SELECT * FROM commerce_customers WHERE id = %s AND company_id = %s",
        (customer_id, company_id)
    )
    return cursor.fetchone()


def upsert_customer(cursor, company_id: str, data: dict):
    cursor.execute(
        """
        INSERT INTO commerce_customers (company_id, email, name, phone, address)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (company_id, email) DO UPDATE SET
            name    = EXCLUDED.name,
            phone   = EXCLUDED.phone,
            address = EXCLUDED.address,
            updated_at = NOW()
        RETURNING *
        """,
        (company_id, data["email"], data.get("name"), data.get("phone"), data.get("address"))
    )
    return cursor.fetchone()


# ── Orders ─────────────────────────────────────────────────

def list_orders(cursor, company_id: str, status: Optional[str] = None):
    if status:
        cursor.execute(
            "SELECT * FROM commerce_orders WHERE company_id = %s AND status = %s ORDER BY created_at DESC",
            (company_id, status)
        )
    else:
        cursor.execute(
            "SELECT * FROM commerce_orders WHERE company_id = %s ORDER BY created_at DESC",
            (company_id,)
        )
    return cursor.fetchall()


def get_order(cursor, company_id: str, order_id: str):
    cursor.execute(
        "SELECT * FROM commerce_orders WHERE id = %s AND company_id = %s",
        (order_id, company_id)
    )
    return cursor.fetchone()


def get_order_items(cursor, order_id: str):
    cursor.execute(
        "SELECT * FROM commerce_order_items WHERE order_id = %s",
        (order_id,)
    )
    return cursor.fetchall()


def create_order(cursor, company_id: str, data: dict):
    cursor.execute(
        """
        INSERT INTO commerce_orders (company_id, customer_id, status, total, notes)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        (company_id, data["customer_id"], data.get("status", "pending"), data["total"], data.get("notes"))
    )
    order = cursor.fetchone()
    for item in data.get("items", []):
        cursor.execute(
            """
            INSERT INTO commerce_order_items (order_id, product_id, quantity, unit_price)
            VALUES (%s, %s, %s, %s)
            """,
            (order["id"], item["product_id"], item["quantity"], item["unit_price"])
        )
    return order


def update_order_status(cursor, company_id: str, order_id: str, status: str):
    cursor.execute(
        """
        UPDATE commerce_orders SET status = %s, updated_at = NOW()
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        (status, order_id, company_id)
    )
    return cursor.fetchone()


# ── Connectors ─────────────────────────────────────────────

def get_connector(cursor, company_id: str, provider: str):
    cursor.execute(
        "SELECT * FROM commerce_connectors WHERE company_id = %s AND provider = %s",
        (company_id, provider)
    )
    return cursor.fetchone()


def list_connectors(cursor, company_id: str):
    cursor.execute(
        "SELECT id, company_id, provider, active, created_at FROM commerce_connectors WHERE company_id = %s",
        (company_id,)
    )
    return cursor.fetchall()


def upsert_connector(cursor, company_id: str, provider: str, credentials: dict):
    cursor.execute(
        """
        INSERT INTO commerce_connectors (company_id, provider, credentials, active)
        VALUES (%s, %s, %s, true)
        ON CONFLICT (company_id, provider) DO UPDATE SET
            credentials = EXCLUDED.credentials,
            active      = true,
            updated_at  = NOW()
        RETURNING id, company_id, provider, active, created_at
        """,
        (company_id, provider, credentials)
    )
    return cursor.fetchone()


def delete_connector(cursor, company_id: str, provider: str):
    cursor.execute(
        "DELETE FROM commerce_connectors WHERE company_id = %s AND provider = %s",
        (company_id, provider)
    )
