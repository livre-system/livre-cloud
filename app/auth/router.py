import uuid
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.core.database import get_cursor
from app.core.jwt import create_token, require_auth
from app.core.rate_limit import client_key, driver_login_limiter, login_limiter

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    username: str
    password: str
    name: str


class LoginBody(BaseModel):
    username: str
    password: str
    device_id: str | None = None


class DriverRegisterBody(RegisterBody):
    """Datos mínimos para crear una cuenta operativa de chofer."""


def _roles_for_user(cur, user_id: str) -> list[str]:
    cur.execute(
        """SELECT role FROM livre_user_roles WHERE user_id = %s
           UNION ALL
           SELECT 'driver' FROM livre_driver_accounts
            WHERE user_id = %s AND active = TRUE""",
        [user_id, user_id],
    )
    roles = [row["role"] for row in cur.fetchall()]
    if roles:
        return sorted(set(roles))
    return ["passenger"]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def register(body: RegisterBody):
    """Crea un usuario nuevo de Livre Cloud y devuelve el JWT."""
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")
    if len(body.username.strip()) < 3:
        raise HTTPException(status_code=400, detail="El usuario debe tener al menos 3 caracteres")

    user_id = str(uuid.uuid4())
    password_hash = _hash_password(body.password)

    with get_cursor() as cur:
        cur.execute("SELECT id FROM livre_users WHERE username = %s", [body.username])
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="El usuario ya existe")

        cur.execute(
            """
            INSERT INTO livre_users (id, username, name, password_hash)
            VALUES (%s, %s, %s, %s)
            """,
            [user_id, body.username, body.name, password_hash],
        )

    roles = ["passenger"]
    token = create_token(id=user_id, name=body.name, email=body.username, roles=roles)
    return {"token": token, "user": {"id": user_id, "username": body.username, "name": body.name, "roles": roles}}


@router.post("/driver/register", status_code=201)
def register_driver(body: DriverRegisterBody):
    """El alta de choferes es administrativa, nunca desde la app."""
    raise HTTPException(
        status_code=410,
        detail="El registro está deshabilitado. Livre debe habilitar tu cuenta.",
    )



@router.post("/driver/login")
def login_driver(body: LoginBody, request: Request):
    """Autentica una cuenta de conductor y vincula la sesión al dispositivo."""
    driver_login_limiter.check(client_key(request, "driver-login"))
    device_id = (body.device_id or "").strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="Falta la identidad del dispositivo")

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.name, u.username, u.password_hash
            FROM livre_users u
            JOIN livre_driver_accounts a ON a.user_id = u.id
            WHERE u.username = %s AND a.active = TRUE
            """,
            [body.username.strip()],
        )
        user = cur.fetchone()
        if not user or not _verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Credenciales de conductor inválidas")
        cur.execute(
            """INSERT INTO livre_driver_device_sessions
                   (user_id, device_id, active, created_at, last_seen_at, revoked_at)
               VALUES (%s, %s, TRUE, NOW(), NOW(), NULL)
               ON CONFLICT (user_id, device_id) DO UPDATE
               SET active = TRUE, last_seen_at = NOW(), revoked_at = NULL""",
            [user["id"], device_id],
        )
        roles = _roles_for_user(cur, user["id"])

    token = create_token(id=user["id"], name=user["name"], email=user["username"], device_id=device_id, roles=roles)
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "name": user["name"], "roles": roles}}


@router.post("/login")
def login(body: LoginBody, request: Request):
    """Autentica con username + password y devuelve el JWT."""
    login_limiter.check(client_key(request, "login"))
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, name, username, password_hash FROM livre_users WHERE username = %s",
            [body.username],
        )
        user = cur.fetchone()
        if not user or not _verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Credenciales inválidas")
        roles = _roles_for_user(cur, user["id"])
        if "driver" in roles:
            raise HTTPException(status_code=403, detail="Esta cuenta debe ingresar como conductor")

    token = create_token(id=user["id"], name=user["name"], email=user["username"], roles=roles)
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "name": user["name"], "roles": roles}}


@router.get("/me")
def me(user: dict = Depends(require_auth)):
    return user


@router.post("/payment/pending")
def save_payment_pending(user: dict = Depends(require_auth)):
    """Guarda el username del usuario como pendiente de pago."""
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_pending (
                username TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            INSERT INTO payment_pending (username) VALUES (%s)
            ON CONFLICT (username) DO UPDATE SET created_at = NOW()
        """, [user["email"]])
    return {"ok": True}
