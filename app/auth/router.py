import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from app.core.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from app.core.jwt import create_token, require_auth

router = APIRouter(prefix="/auth", tags=["auth"])

# Códigos de un solo uso: {code: {token, expires_at}}
_pending_codes: dict[str, dict] = {}

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Destinos válidos por app
_DESTINOS = {
    "ai":       "https://hash-ia.site",
    "job":      "https://hash-ia.site",
    "commerce": "https://hashcommerce.site",
}


@router.get("/login")
async def login(request: Request, next: str = "ai"):
    # Guardar destino en sesión para recuperarlo en el callback
    request.session["next"] = next if next in _DESTINOS else "ai"
    return await oauth.google.authorize_redirect(request, GOOGLE_REDIRECT_URI)


@router.get("/callback")
async def callback(request: Request):
    from app.memory.service import create_user_memory, check_memory_status
    from app.core.database import get_cursor
    token = await oauth.google.authorize_access_token(request)
    user = token.get("userinfo")
    user_id = user.get("sub")
    email = user.get("email")
    status_result = check_memory_status(user_id)
    if status_result["status"] == "not_found":
        create_user_memory(user_id, email=email)
    else:
        with get_cursor() as cur:
            cur.execute("UPDATE memory_users SET email = %s WHERE user_id = %s", [email, user_id])
    jwt_token = create_token(
        id=user_id,
        name=user.get("name"),
        email=email,
    )

    # Código de un solo uso (el JWT nunca viaja en la URL)
    code = secrets.token_urlsafe(32)
    _pending_codes[code] = {
        "token": jwt_token,
        "expires_at": datetime.utcnow() + timedelta(seconds=60),
    }

    # Leer destino guardado en sesión
    next_app = request.session.pop("next", "ai")
    destino = _DESTINOS.get(next_app, _DESTINOS["ai"])

    return RedirectResponse(
        url=f"{destino}?code={code}",
        status_code=302,
    )


@router.post("/token")
async def exchange_code(body: dict):
    """Canjea un código de un solo uso por el JWT. Expira en 60 segundos."""
    code = body.get("code", "")

    # Limpiar códigos expirados
    now = datetime.utcnow()
    expired = [k for k, v in _pending_codes.items() if now > v["expires_at"]]
    for k in expired:
        del _pending_codes[k]

    entry = _pending_codes.pop(code, None)
    if not entry or now > entry["expires_at"]:
        raise HTTPException(status_code=401, detail="Código inválido o expirado")

    return {"token": entry["token"]}


@router.get("/me")
def me(user: dict = Depends(require_auth)):
    return user


@router.post("/payment/pending")
def save_payment_pending(user: dict = Depends(require_auth)):
    """Guarda el email del usuario como pendiente de pago."""
    from app.core.database import get_cursor
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
