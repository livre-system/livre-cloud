from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from app.core.jwt import require_auth
from app.voice.factory import get_voice_provider
from app.llm.factory import get_llm_provider
from app.context.provider import get_hash_sources
from app.compiler.base_compiler import compile_base_context
from app.compiler.style_compiler import compile_style_context
from app.compiler.user_compiler import compile_user_context
from app.memory.service import read_user_memory, save_message_to_memory
from app.memory.repository import search_memory_by_embedding
from app.core.database import get_cursor
from app.core.encryption import decrypt
from app.chat.models import ChatRequest, SynthesizeRequest
import app.chat.repository as repo

router = APIRouter(prefix="/chat", tags=["chat"])

FREE_MESSAGE_LIMIT = 20


def _get_all_memory(user_id: str) -> str:
    """Trae toda la memoria del usuario desencriptada."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT md.name, mr.data
                FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s AND md.key NOT LIKE 'chat_log%'
                ORDER BY mr.created_at ASC
            """, [user_id])
            rows = cur.fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            msg = r["data"].get("message", "")
            if not msg:
                continue
            try:
                msg = decrypt(msg)
            except Exception:
                pass
            lines.append(f"[{r['name']}]\n{msg.strip()}")
        return "\n\n".join(lines)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"_get_all_memory error: {e}")
        return ""


def _search_memory(user_id: str, query: str) -> str:
    """Busca por embeddings. Si no encuentra nada, devuelve vacío."""
    rows = search_memory_by_embedding(user_id, query)
    if not rows:
        return ""
    lines = []
    for r in rows:
        msg = r["data"].get("message", "")
        if not msg:
            continue
        try:
            msg = decrypt(msg)
        except Exception:
            pass
        lines.append(f"[{r['name']}]\n{msg.strip()[:500]}")
    return "\n\n".join(lines)


def _build_system_prompt(user_id: str, query: str = "", mode: str = "conspiranoico") -> str:
    sources = get_hash_sources(mode)
    base_context = compile_base_context(sources)
    style_context = compile_style_context(sources)

    memory_text = _search_memory(user_id, query) if query else ""

    print(f">>> SYSTEM PROMPT MEMORY: {len(memory_text)} chars", flush=True)
    return (
        f"Fecha y hora actual: {base_context['fecha_actual']}\n\n"
        + (f"Memoria relevante:\n{memory_text}\n\n" if memory_text else "")
        + f"Identidad de HASH:\n{base_context['sources']['cognitive_base']}\n\n"
        f"Log personal:\n{base_context['sources']['personal_log']}\n\n"
        f"Destilador:\n{base_context['sources']['destilador']}\n\n"
        f"Estilo:\n{style_context['sources']['style']}"
    )


def _is_quota_error(e: Exception) -> bool:
    msg = str(e)
    return "429" in msg or "503" in msg or "fallaron" in msg


def _get_fallback_provider(provider_name: str):
    if provider_name != "groq":
        from app.llm.groq import GroqProvider
        return GroqProvider()
    return None


# ── Chats CRUD ────────────────────────────────────────────────────────────────

@router.get("/list")
def list_chats(user: dict = Depends(require_auth)):
    """Devuelve todos los chats del usuario ordenados por actividad."""
    return repo.list_chats(user["id"])


@router.post("/new")
def new_chat(user: dict = Depends(require_auth)):
    """Crea un chat vacío y devuelve su ID."""
    # Límite plan free: 6 chats
    from app.core.database import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT plan FROM memory_users WHERE user_id = %s", [user["id"]])
        u = cur.fetchone()
    if u and u["plan"] == "free":
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) as total FROM chats WHERE user_id = %s", [user["id"]])
            row = cur.fetchone()
            if row and row["total"] >= 6:
                raise HTTPException(status_code=403, detail="Límite de chats alcanzado (plan free: 6)")
    chat = repo.create_chat(user["id"])
    return chat


@router.get("/{chat_id}/messages")
def get_messages(chat_id: str, user: dict = Depends(require_auth)):
    """Devuelve el historial de mensajes de un chat."""
    chat = repo.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")
    messages = repo.get_messages(chat_id, user["id"])
    return {"chat_id": chat_id, "title": chat["title"], "messages": messages}


@router.patch("/{chat_id}/title")
def update_title(chat_id: str, body: dict, user: dict = Depends(require_auth)):
    """Actualiza el título del chat."""
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="El título no puede estar vacío")
    repo.update_chat_title(chat_id, user["id"], title)
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, user: dict = Depends(require_auth)):
    """Elimina un chat, todos sus mensajes y sus documentos de memoria."""
    with get_cursor() as cur:
        cur.execute("""
            DELETE FROM memory_rows WHERE document_id IN (
                SELECT id FROM memory_documents WHERE user_id = %s AND chat_id = %s
            )
        """, [user["id"], chat_id])
        cur.execute(
            "DELETE FROM memory_documents WHERE user_id = %s AND chat_id = %s",
            [user["id"], chat_id]
        )
    repo.delete_chat(chat_id, user["id"])
    return {"ok": True}


# ── Chat (enviar mensaje) ─────────────────────────────────────────────────────

@router.post("")
def chat(body: ChatRequest, user: dict = Depends(require_auth)):
    try:
        # Crear chat si no viene chat_id
        chat_id = body.chat_id
        if not chat_id:
            new = repo.create_chat(user["id"])
            chat_id = new["chat_id"]

        # Límite de mensajes plan free
        with get_cursor() as cur:
            cur.execute("SELECT plan FROM memory_users WHERE user_id = %s", [user["id"]])
            u = cur.fetchone()
        if u and u["plan"] == "free":
            if repo.count_user_messages(user["id"]) >= FREE_MESSAGE_LIMIT:
                raise HTTPException(status_code=403, detail="Límite de mensajes alcanzado (plan free: 20)")

        # Guardar el mensaje del usuario
        last_user_msg = body.messages[-1] if body.messages else None
        if last_user_msg and last_user_msg.role == "user":
            repo.save_message(chat_id, "user", last_user_msg.content)
            save_message_to_memory(user["id"], "user", last_user_msg.content)

            # Auto-título con las primeras palabras del primer mensaje
            chat = repo.get_chat(chat_id, user["id"])
            if chat and chat["title"] == "Nueva conversación":
                auto_title = last_user_msg.content[:50].strip()
                repo.update_chat_title(chat_id, user["id"], auto_title)

        query = last_user_msg.content if last_user_msg else ""
        system_prompt = _build_system_prompt(user["id"], query)
        messages = [{"role": "system", "content": system_prompt}] + [m.model_dump() for m in body.messages]
        llm = get_llm_provider(body.provider)

        try:
            reply = llm.generate(messages)
        except RuntimeError as e:
            if _is_quota_error(e):
                fallback = _get_fallback_provider(llm.__class__.__name__.lower().replace("provider", ""))
                if fallback:
                    reply = fallback.generate(messages)
                else:
                    raise
            else:
                raise

        # Guardar respuesta del asistente
        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(user["id"], "assistant", reply)

        return {"reply": reply, "chat_id": chat_id}

    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Proveedor no disponible")
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Error en /chat")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno del servidor")


@router.post("/stream")
def chat_stream(body: ChatRequest, user: dict = Depends(require_auth)):
    try:
        # Límite plan free: 6 chats (antes de entrar al stream)
        chat_id = body.chat_id
        with get_cursor() as cur:
            cur.execute("SELECT plan FROM memory_users WHERE user_id = %s", [user["id"]])
            u = cur.fetchone()
        is_free = u and u["plan"] == "free"

        if not chat_id:
            if is_free:
                with get_cursor() as cur:
                    cur.execute("SELECT COUNT(*) as total FROM chats WHERE user_id = %s", [user["id"]])
                    row = cur.fetchone()
                    if row and row["total"] >= 6:
                        raise HTTPException(status_code=403, detail="Límite de chats alcanzado (plan free: 6)")
            new = repo.create_chat(user["id"])
            chat_id = new["chat_id"]

        # Límite de mensajes plan free
        if is_free:
            if repo.count_user_messages(user["id"]) >= FREE_MESSAGE_LIMIT:
                raise HTTPException(status_code=403, detail="Límite de mensajes alcanzado (plan free: 20)")

        # Guardar mensaje del usuario
        last_user_msg = body.messages[-1] if body.messages else None
        if last_user_msg and last_user_msg.role == "user":
            repo.save_message(chat_id, "user", last_user_msg.content)
            save_message_to_memory(user["id"], "user", last_user_msg.content)

            chat = repo.get_chat(chat_id, user["id"])
            if chat and chat["title"] == "Nueva conversación":
                auto_title = last_user_msg.content[:50].strip()
                repo.update_chat_title(chat_id, user["id"], auto_title)

        query = last_user_msg.content if last_user_msg else ""
        mode = getattr(body, "mode", "conspiranoico") or "conspiranoico"
        system_prompt = _build_system_prompt(user["id"], query, mode)
        messages = [{"role": "system", "content": system_prompt}] + [m.model_dump() for m in body.messages]
        llm = get_llm_provider()

        full_reply = []

        def event_stream():
            try:
                for chunk in llm.generate_stream(messages):
                    full_reply.append(chunk)
                    escaped = chunk.replace("\n", "\\n")
                    yield f"data: {escaped}\n\n"
            except RuntimeError as e:
                if _is_quota_error(e):
                    try:
                        from app.llm.groq import GroqProvider
                        fallback = GroqProvider()
                        for chunk in fallback.generate_stream(messages):
                            full_reply.append(chunk)
                            escaped = chunk.replace("\n", "\\n")
                            yield f"data: {escaped}\n\n"
                    except Exception as fe:
                        yield f"data: [ERROR] {str(fe)}\n\n"
                else:
                    yield f"data: [ERROR] {str(e)}\n\n"
            except Exception as e:
                yield f"data: [ERROR] {str(e)}\n\n"
            finally:
                # Guardar respuesta completa
                if full_reply:
                    complete = "".join(full_reply)
                    repo.save_message(chat_id, "assistant", complete)
                    save_message_to_memory(user["id"], "assistant", complete)
                yield f"data: [CHAT_ID] {chat_id}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Error en /chat/stream")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno del servidor")


# ── Debug ───────────────────────────────────────────────────────────────────

@router.get("/debug/memory")
def debug_memory(user: dict = Depends(require_auth)):
    """Muestra cuánto texto de memoria se está mandando al LLM."""
    test_query = "test"
    memory_text = _search_memory(user["id"], test_query)
    system_prompt = _build_system_prompt(user["id"], test_query)
    return {
        "memory_chars": len(memory_text),
        "memory_preview": memory_text[:300] if memory_text else "(vacío)",
        "total_prompt_chars": len(system_prompt),
    }


# ── Voz ──────────────────────────────────────────────────────────────────────────

@router.post("/synthesize")
def synthesize(body: SynthesizeRequest, user: dict = Depends(require_auth)):
    try:
        voice = get_voice_provider()
        audio = voice.synthesize(body.text, voice_id=body.voice_id)
        return Response(content=audio, media_type="audio/mpeg")
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Servicio de voz no disponible temporalmente")
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Error en synthesize")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno del servidor")


@router.post("/voice")
async def voice_chat(
    audio: UploadFile = File(...),
    chat_id: str = Form(None),
    user: dict = Depends(require_auth),
):
    """Recibe audio → transcribe → responde → devuelve audio."""
    try:
        from app.llm.groq import GroqProvider
        groq = GroqProvider()

        # 1. Transcribir voz a texto
        audio_bytes = await audio.read()
        transcript = groq.transcribe(audio_bytes, filename=audio.filename or "audio.webm")
        if not transcript:
            raise HTTPException(status_code=400, detail="No se pudo transcribir el audio")

        # 2. Crear chat si no viene chat_id
        if not chat_id:
            new = repo.create_chat(user["id"])
            chat_id = new["chat_id"]

        # 3. Guardar mensaje del usuario
        repo.save_message(chat_id, "user", transcript)
        save_message_to_memory(user["id"], "user", transcript)

        # 4. Traer historial del chat
        history = repo.get_messages(chat_id, user["id"])

        # 5. Generar respuesta con el proveedor normal (Gemini/Grok)
        system_prompt = _build_system_prompt(user["id"], transcript)
        llm = get_llm_provider()
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        reply = llm.generate(messages)

        # 6. Guardar respuesta
        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(user["id"], "assistant", reply)

        # 6. Devolver solo texto (el navegador reproduce el audio)
        from fastapi.responses import JSONResponse
        return JSONResponse(content={
            "transcript": transcript,
            "reply": reply,
            "chat_id": chat_id,
        })
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Error en /chat/voice")
        raise HTTPException(status_code=500, detail="Error en chat de voz")
def synthesize_stream(body: SynthesizeRequest, user: dict = Depends(require_auth)):
    try:
        voice = get_voice_provider()

        def audio_chunks():
            try:
                for chunk in voice.synthesize_stream(body.text, voice_id=body.voice_id):
                    yield chunk
            except Exception as e:
                print(f"Error en stream de audio: {e}")

        return StreamingResponse(
            audio_chunks(),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache"},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Servicio de voz no disponible temporalmente")
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Error en synthesize")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno del servidor")

