"""
Schemas de Hash Commerce.
Pydantic v2. Se usan para validar entrada y serializar salida en el router.
"""

from pydantic import BaseModel, EmailStr
from typing import Optional
from uuid import UUID
from datetime import datetime
from decimal import Decimal


# ── Empresa ───────────────────────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    name: str

class CompanyOut(BaseModel):
    id: UUID
    owner_id: str
    name: str
    created_at: datetime


# ── Configuración de tienda ───────────────────────────────────────────────────

class StoreConfigUpdate(BaseModel):
    name:             Optional[str] = None
    logo_url:         Optional[str] = None
    favicon_url:      Optional[str] = None
    banner_url:       Optional[str] = None
    primary_color:    Optional[str] = None
    secondary_color:  Optional[str] = None
    business_name:    Optional[str] = None
    business_email:   Optional[str] = None
    business_phone:   Optional[str] = None
    business_address: Optional[str] = None
    custom_domain:    Optional[str] = None

class StoreConfigOut(StoreConfigUpdate):
    company_id: UUID
    updated_at: datetime


# ── Categorías ────────────────────────────────────────────────────────────────

class CategoryCreate(BaseModel):
    name:        str
    slug:        str
    description: Optional[str] = None
    image_url:   Optional[str] = None

class CategoryOut(CategoryCreate):
    id:         UUID
    company_id: UUID
    active:     bool
    created_at: datetime


# ── Productos ─────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name:        str
    slug:        str
    description: Optional[str] = None
    price:       Decimal
    stock:       int = 0
    image_url:   Optional[str] = None
    category_id: Optional[UUID] = None

class ProductOut(ProductCreate):
    id:         UUID
    company_id: UUID
    active:     bool
    created_at: datetime
    updated_at: datetime


# ── Clientes ──────────────────────────────────────────────────────────────────

class CustomerCreate(BaseModel):
    name:    str
    email:   EmailStr
    phone:   Optional[str] = None
    address: Optional[str] = None

class CustomerOut(CustomerCreate):
    id:         UUID
    company_id: UUID
    created_at: datetime


# ── Pedidos ───────────────────────────────────────────────────────────────────

class OrderItemCreate(BaseModel):
    product_id: Optional[UUID] = None
    name:       str
    price:      Decimal
    quantity:   int

class OrderCreate(BaseModel):
    customer_id: Optional[UUID] = None
    notes:       Optional[str] = None
    items:       list[OrderItemCreate]

class OrderItemOut(OrderItemCreate):
    id: UUID

class OrderOut(BaseModel):
    id:          UUID
    company_id:  UUID
    customer_id: Optional[UUID]
    status:      str
    total:       Decimal
    notes:       Optional[str]
    items:       list[OrderItemOut]
    created_at:  datetime
    updated_at:  datetime


# ── Conectores ────────────────────────────────────────────────────────────────

class ConnectorOut(BaseModel):
    id:         UUID
    company_id: UUID
    provider:   str
    active:     bool
    created_at: datetime
    updated_at: datetime
