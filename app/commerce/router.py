from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File
from typing import Optional
import re

from app.auth.dependencies import require_auth
from app.core.database import get_cursor_dep
from . import service
from . import repository as repo
from .connectors.cloudinary import upload_image

router = APIRouter(prefix="/commerce", tags=["commerce"])


# ── Setup (crea company + store en un paso) ────────────────

@router.post("/setup")
def setup(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    store_name = data.get("store_name", "").strip()
    if not store_name:
        raise HTTPException(status_code=400, detail="El nombre de la tienda es obligatorio.")

    company = repo.get_company_by_user(cursor, user["id"])
    if not company:
        slug = re.sub(r"[^a-z0-9]+", "-", store_name.lower()).strip("-") or "tienda"
        base_slug = slug
        suffix = 0
        while True:
            cursor.execute("SELECT 1 FROM commerce_companies WHERE slug = %s", [slug])
            if not cursor.fetchone():
                break
            suffix += 1
            slug = f"{base_slug}-{suffix}"
        company = repo.create_company(cursor, user["id"], {"name": store_name, "slug": slug})

    store = repo.upsert_store(cursor, company["id"], {"display_name": store_name})
    return {**dict(store), "company_id": str(company["id"])}


# ── Companies ──────────────────────────────────────────────

@router.get("/company")
def get_company(user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.get_my_company(cursor, user["id"])


@router.post("/company")
def create_company(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.create_company(cursor, user["id"], data)


# ── Store ──────────────────────────────────────────────────

@router.get("/store")
def get_store(user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.get_store(cursor, user["id"])


@router.put("/store")
def upsert_store(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.upsert_store(cursor, user["id"], data)


# ── Categories ─────────────────────────────────────────────

@router.get("/categories")
def list_categories(user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.list_categories(cursor, user["id"])


@router.post("/categories")
def create_category(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.create_category(cursor, user["id"], data)


@router.put("/categories/{category_id}")
def update_category(category_id: str, data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.update_category(cursor, user["id"], category_id, data)


@router.delete("/categories/{category_id}")
def delete_category(category_id: str, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    service.delete_category(cursor, user["id"], category_id)
    return {"ok": True}


# ── Products ───────────────────────────────────────────────

@router.get("/products")
def list_products(
    category_id: Optional[str] = Query(None),
    user=Depends(require_auth),
    cursor=Depends(get_cursor_dep)
):
    return service.list_products(cursor, user["id"], category_id)


# ── Upload imagen (ANTES de /{product_id} para evitar conflicto) ──

@router.post("/products/upload-image")
async def upload_product_image(image: UploadFile = File(...), user=Depends(require_auth)):
    try:
        url = await upload_image(image)
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products/{product_id}")
def get_product(product_id: str, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.get_product(cursor, user["id"], product_id)


@router.post("/products")
def create_product(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.create_product(cursor, user["id"], data)


@router.put("/products/{product_id}")
def update_product(product_id: str, data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.update_product(cursor, user["id"], product_id, data)


@router.delete("/products/{product_id}")
def delete_product(product_id: str, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    service.delete_product(cursor, user["id"], product_id)
    return {"ok": True}


# ── Customers ──────────────────────────────────────────────

@router.get("/customers")
def list_customers(user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.list_customers(cursor, user["id"])


@router.get("/customers/{customer_id}")
def get_customer(customer_id: str, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.get_customer(cursor, user["id"], customer_id)


@router.post("/customers")
def upsert_customer(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.upsert_customer(cursor, user["id"], data)


# ── Orders ─────────────────────────────────────────────────

@router.get("/orders")
def list_orders(
    status: Optional[str] = Query(None),
    user=Depends(require_auth),
    cursor=Depends(get_cursor_dep)
):
    return service.list_orders(cursor, user["id"], status)


@router.get("/orders/{order_id}")
def get_order(order_id: str, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.get_order(cursor, user["id"], order_id)


@router.post("/orders")
def create_order(data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.create_order(cursor, user["id"], data)


@router.patch("/orders/{order_id}/status")
def update_order_status(order_id: str, data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.update_order_status(cursor, user["id"], order_id, data["status"])


# ── Connectors ─────────────────────────────────────────────

@router.get("/connectors")
def list_connectors(user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.list_connectors(cursor, user["id"])


@router.put("/connectors/{provider}")
def upsert_connector(provider: str, data: dict, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    return service.upsert_connector(cursor, user["id"], provider, data["credentials"])


@router.delete("/connectors/{provider}")
def delete_connector(provider: str, user=Depends(require_auth), cursor=Depends(get_cursor_dep)):
    service.delete_connector(cursor, user["id"], provider)
    return {"ok": True}


# ── Tienda pública (sin auth) ──────────────────────────────

@router.get("/public/{slug}")
def get_public_store(slug: str, cursor=Depends(get_cursor_dep)):
    cursor.execute(
        "SELECT id, name, slug FROM commerce_companies WHERE slug = %s",
        [slug]
    )
    company = cursor.fetchone()
    if not company:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")

    company_id = company["id"]

    cursor.execute(
        "SELECT display_name, logo_url, banner_url FROM commerce_stores WHERE company_id = %s",
        [company_id]
    )
    store = cursor.fetchone() or {}

    cursor.execute(
        """SELECT p.id, p.name, p.price, p.stock, p.image_url, p.image_url_2, p.image_url_3,
                  p.description, c.name as category_name
           FROM commerce_products p
           LEFT JOIN commerce_categories c ON c.id = p.category_id
           WHERE p.company_id = %s AND p.active = true
           ORDER BY c.name NULLS LAST, p.created_at DESC""",
        [company_id]
    )
    products = cursor.fetchall()

    return {
        "slug": company["slug"],
        "store_name": (store.get("display_name") if store else None) or company["name"],
        "logo_url": store.get("logo_url") if store else None,
        "banner_url": store.get("banner_url") if store else None,
        "products": [dict(p) for p in products],
    }
