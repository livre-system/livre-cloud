from fastapi import APIRouter, Depends
from app.core.jwt import require_auth
from app.context.provider import get_hash_context

router = APIRouter(prefix="/context", tags=["context"])


@router.get("")
def get_context(user: dict = Depends(require_auth)):
    return {
        "user": user,
        "hash": get_hash_context(),
    }
