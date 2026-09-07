import uuid
import bcrypt
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from app.core.database import get_cursor
from app.core.jwt import create_token, require_auth

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def register(body: RegisterBody):
    """Crea un usuario nuevo de Livre Cloud y devuelve el JWT."""
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")

    user_id = str(uuid.uuid4())
    password_hash = _hash_password(body.password)

    with get_cursor() as cur:
        # Verificar email duplicado
        cur.execute("SELECT id FROM livre_users WHERE email = %s", [body.email])
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="El email ya está registrado")

        cur.execute(
            """
            INSERT INTO livre_users (id, email, name, password_hash)
            VALUES (%s, %s, %s, %s)
            """,
            [user_id, body.email, body.name, password_hash],
        )

    token = create_token(id=user_id, name=body.name, email=body.email)
    return {"token": token, "user": {"id": user_id, "email": body.email, "name": body.name}}


@router.post("/login")
def login(body: LoginBody):
    """Autentica con email + password y devuelve el JWT."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, name, email, password_hash FROM livre_users WHERE email = %s",
            [body.email],
        )
        user = cur.fetchone()

    if not user or not _verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = create_token(id=user["id"], name=user["name"], email=user["email"])
    return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@router.get("/me")
def me(user: dict = Depends(require_auth)):
    return user


@router.post("/payment/pending")
def save_payment_pending(user: dict = Depends(require_auth)):
    """Guarda el email del usuario como pendiente de pago."""
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_pending (
                email TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            INSERT INTO payment_pending (email) VALUES (%s)
            ON CONFLICT (email) DO UPDATE SET created_at = NOW()
        """, [user["email"]])
    return {"ok": True}
