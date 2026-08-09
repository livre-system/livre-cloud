from fastapi import HTTPException
from . import repository as repo


def _get_company_or_404(cursor, user_id: str):
    company = repo.get_company_by_user(cursor, user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada. Creá una primero.")
    return company


# ── Companies ──────────────────────────────────────────────

def get_my_company(cursor, user_id: str):
    return _get_company_or_404(cursor, user_id)


def create_company(cursor, user_id: str, data: dict):
    existing = repo.get_company_by_user(cursor, user_id)
    if existing:
        raise HTTPException(status_code=400, detail="Ya tenés una empresa registrada.")
    return repo.create_company(cursor, user_id, data)


# ── Store ──────────────────────────────────────────────────

def get_store(cursor, user_id: str):
    company = _get_company_or_404(cursor, user_id)
    store = repo.get_store(cursor, company["id"])
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no configurada.")
    store = dict(store)
    store.setdefault("store_name", store.get("display_name"))
    return store


def upsert_store(cursor, user_id: str, data: dict):
    company = _get_company_or_404(cursor, user_id)
    # Normalizar: el frontend manda store_name, el repo espera display_name
    if "store_name" in data and "display_name" not in data:
        data["display_name"] = data["store_name"]
    return repo.upsert_store(cursor, company["id"], data)


# ── Categories ─────────────────────────────────────────────

def list_categories(cursor, user_id: str):
    company = _get_company_or_404(cursor, user_id)
    return repo.list_categories(cursor, company["id"])


def create_category(cursor, user_id: str, data: dict):
    import re
    if not data.get("slug"):
        data["slug"] = re.sub(r"[^a-z0-9]+", "-", data.get("name", "categoria").lower()).strip("-") or "categoria"
    company = _get_company_or_404(cursor, user_id)
    return repo.create_category(cursor, company["id"], data)


def update_category(cursor, user_id: str, category_id: str, data: dict):
    company = _get_company_or_404(cursor, user_id)
    result = repo.update_category(cursor, company["id"], category_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="Categoría no encontrada.")
    return result


def delete_category(cursor, user_id: str, category_id: str):
    company = _get_company_or_404(cursor, user_id)
    repo.delete_category(cursor, company["id"], category_id)


# ── Products ───────────────────────────────────────────────

def list_products(cursor, user_id: str, category_id: str = None):
    company = _get_company_or_404(cursor, user_id)
    return repo.list_products(cursor, company["id"], category_id)


def get_product(cursor, user_id: str, product_id: str):
    company = _get_company_or_404(cursor, user_id)
    product = repo.get_product(cursor, company["id"], product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return product


def create_product(cursor, user_id: str, data: dict):
    import re
    if not data.get("slug"):
        data["slug"] = re.sub(r"[^a-z0-9]+", "-", data.get("name", "producto").lower()).strip("-") or "producto"
    company = _get_company_or_404(cursor, user_id)
    return repo.create_product(cursor, company["id"], data)


def update_product(cursor, user_id: str, product_id: str, data: dict):
    import re
    if not data.get("slug"):
        data["slug"] = re.sub(r"[^a-z0-9]+", "-", data.get("name", "producto").lower()).strip("-") or "producto"
    company = _get_company_or_404(cursor, user_id)
    result = repo.update_product(cursor, company["id"], product_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return result


def delete_product(cursor, user_id: str, product_id: str):
    company = _get_company_or_404(cursor, user_id)
    repo.delete_product(cursor, company["id"], product_id)


# ── Customers ──────────────────────────────────────────────

def list_customers(cursor, user_id: str):
    company = _get_company_or_404(cursor, user_id)
    return repo.list_customers(cursor, company["id"])


def get_customer(cursor, user_id: str, customer_id: str):
    company = _get_company_or_404(cursor, user_id)
    customer = repo.get_customer(cursor, company["id"], customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    return customer


def upsert_customer(cursor, user_id: str, data: dict):
    company = _get_company_or_404(cursor, user_id)
    return repo.upsert_customer(cursor, company["id"], data)


# ── Orders ─────────────────────────────────────────────────

def list_orders(cursor, user_id: str, status: str = None):
    company = _get_company_or_404(cursor, user_id)
    return repo.list_orders(cursor, company["id"], status)


def get_order(cursor, user_id: str, order_id: str):
    company = _get_company_or_404(cursor, user_id)
    order = repo.get_order(cursor, company["id"], order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    items = repo.get_order_items(cursor, order_id)
    return {**order, "items": items}


def create_order(cursor, user_id: str, data: dict):
    company = _get_company_or_404(cursor, user_id)
    return repo.create_order(cursor, company["id"], data)


def update_order_status(cursor, user_id: str, order_id: str, status: str):
    company = _get_company_or_404(cursor, user_id)
    result = repo.update_order_status(cursor, company["id"], order_id, status)
    if not result:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    return result


# ── Connectors ─────────────────────────────────────────────

def list_connectors(cursor, user_id: str):
    company = _get_company_or_404(cursor, user_id)
    return repo.list_connectors(cursor, company["id"])


def upsert_connector(cursor, user_id: str, provider: str, credentials: dict):
    company = _get_company_or_404(cursor, user_id)
    return repo.upsert_connector(cursor, company["id"], provider, credentials)


def delete_connector(cursor, user_id: str, provider: str):
    company = _get_company_or_404(cursor, user_id)
    repo.delete_connector(cursor, company["id"], provider)
