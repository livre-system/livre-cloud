# app/auth/dependencies.py
# Re-exporta require_auth para que otros módulos puedan importarlo
# desde app.auth.dependencies sin romper la estructura existente.

from app.core.jwt import require_auth  # noqa: F401
