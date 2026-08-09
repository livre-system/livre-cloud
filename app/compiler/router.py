from fastapi import APIRouter, Depends, HTTPException, status
from app.core.jwt import require_auth
from app.context.provider import get_hash_sources
from app.compiler.base_compiler import compile_base_context
from app.compiler.user_compiler import compile_user_context
from app.compiler.style_compiler import compile_style_context
from app.compiler.hash_compiler import compile_hash_context
from app.memory.service import read_user_memory

router = APIRouter(prefix="/compiler", tags=["compiler"])


@router.get("/base")
def compiler_base(user: dict = Depends(require_auth)):
    sources = get_hash_sources()
    return compile_base_context(sources)


@router.get("/user")
def compiler_user(user: dict = Depends(require_auth)):
    memory = read_user_memory(user["id"])
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memoria no encontrada")
    if "error" in memory:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=memory["error"])
    return compile_user_context(memory)


@router.get("/style")
def compiler_style(user: dict = Depends(require_auth)):
    sources = get_hash_sources()
    return compile_style_context(sources)


@router.get("/hash")
def compiler_hash(user: dict = Depends(require_auth)):
    sources = get_hash_sources()
    base = compile_base_context(sources)
    style = compile_style_context(sources)

    memory = read_user_memory(user["id"])
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memoria no encontrada")
    if "error" in memory:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=memory["error"])
    user_ctx = compile_user_context(memory)

    return compile_hash_context(base, user_ctx, style)
