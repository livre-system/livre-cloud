from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from app.core.jwt import require_auth
from app.voice.factory import get_voice_provider
from app.llm.factory import get_llm_provider
from app.context.provider import get_livre_sources
from app.compiler.base_compiler import compile_base_context
from app.compiler.style_compiler import compile_style_context
from app.memory.service import read_user_memory, save_message_to_memory
from app.memory.repository import search_memory_by_embedding
from app.core.database import get_cursor
from app.core.encryption import decrypt
from app.chat.models import ChatRequest, SynthesizeRequest
import app.chat.repository as repo

router = APIRouter(prefix="/chat", tags=["chat"])

FREE_MESSAGE_LIMIT = 20


def _get_all_memory(user_id: str) -> str:
    """Trae toda la memoria estática del usuario (documentos subidos, excluye chat_log)."""
    import logging
    log = logging.getLogger(__name__)
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT md.name, mr.data
                FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s AND md.key NOT LIKE 'chat_log%%'
                ORDER BY mr.created_at ASC
            """, [user_id])
            rows = cur.fetchall()

        if not rows:
            log.info("[MEMORY DEBUG] _get_all_memory: sin filas (0 docs estáticos)")
            return ""

        lines = []
        for r in rows:
            # Soportar tanto dict-cursor como tuple-cursor
            try:
                if hasattr(r, "keys") or isinstance(r, dict):
                    name = r["name"]
                    data = r["data"]
                else:
                    name = r[0]
                    data = r[1]
            except Exception as parse_err:
                log.error(f"[MEMORY DEBUG] _get_all_memory: error parseando fila {r!r}: {parse_err}")
                continue

            if isinstance(data, str):
                import json
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}

            msg = data.get("message", "") if isinstance(data, dict) else ""
            if not msg:
                continue
            try:
                msg = decrypt(msg)
            except Exception:
                pass
            lines.append(f"[{name}]\n{msg.strip()}")

        log.info(f"[MEMORY DEBUG] _get_all_memory: {len(lines)} líneas de memoria estática")
        return "\n\n".join(lines)

    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error(f"[MEMORY DEBUG] _get_all_memory error: {e}", exc_info=True)
        return ""


def _search_memory(user_id: str, query: str) -> str:
    """
    Búsqueda semántica sobre memoria del usuario.
    Logs explícitos para depuración de retrieval.
    Independiente de _get_all_memory — si esa falla, esta igual corre.
    """
    import logging
    log = logging.getLogger(__name__)

    log.info(f"[MEMORY DEBUG] query={query!r}")

    # 1. Generar query embedding
    try:
        from app.memory.embeddings import get_query_embedding
        test_emb = get_query_embedding(query)
        log.info(f"[MEMORY DEBUG] query_embedding_generado={'SÍ' if test_emb else 'NO'} dims={len(test_emb) if test_emb else 0}")
    except Exception as e:
        log.error(f"[MEMORY DEBUG] ERROR generando query embedding: {e}")
        test_emb = None

    if not test_emb:
        log.error("[MEMORY DEBUG] Sin query embedding — abortando búsqueda semántica")
        return ""

    # 2. Ejecutar búsqueda semántica
    try:
        rows = search_memory_by_embedding(user_id, query, limit=20, min_similarity=0.30)
    except Exception as e:
        log.error(f"[MEMORY DEBUG] ERROR en search_memory_by_embedding: {e}", exc_info=True)
        return ""

    log.info(f"[MEMORY DEBUG] semantic_search_called=SÍ semantic_results_count={len(rows)}")

    if not rows:
        log.warning("[MEMORY DEBUG] semantic_results_count=0 — el LLM NO recibirá contexto semántico")
        return ""

    lines = []
    for i, r in enumerate(rows, 1):
        # Acceso robusto: soportar dict-cursor y tuple-cursor
        try:
            if hasattr(r, "keys") or isinstance(r, dict):
                name      = r.get("name", "?")
                data      = r.get("data", {})
                similarity = r.get("similarity", 0)
            else:
                # tuple: (name, data, similarity)
                name      = r[0]
                data      = r[1]
                similarity = r[2]
        except Exception as pe:
            log.error(f"[MEMORY DEBUG] Error parseando resultado {i}: {pe} — fila={r!r}")
            continue

        if isinstance(data, str):
            import json
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        msg = data.get("message", "") if isinstance(data, dict) else ""
        if not msg:
            continue

        try:
            msg = decrypt(msg)
        except Exception:
            pass

        log.info(
            f"[MEMORY DEBUG] result_{i} document_name={name!r} "
            f"similarity={similarity:.4f} "
            f"content={msg[:120]!r}"
        )
        lines.append(f"[{name}]\n{msg.strip()}")

    log.info(f"[MEMORY DEBUG] semantic_memory_chars={sum(len(l) for l in lines)}")
    return "\n\n".join(lines)


def _build_system_prompt(user_id: str, query: str = "") -> str:
    import logging
    log = logging.getLogger(__name__)

    log.info(f"[MEMORY DEBUG] === _build_system_prompt START user={user_id} query={query!r}")

    sources = get_livre_sources()
    base_context = compile_base_context(sources)
    style_context = compile_style_context(sources)

    # Memoria estática: falla de forma aislada — NO bloquea la búsqueda semántica.
    try:
        static_memory = _get_all_memory(user_id)
    except Exception as e:
        log.error(f"[MEMORY DEBUG] _get_all_memory excepción (ignorada): {e}")
        static_memory = ""

    log.info(f"[MEMORY DEBUG] static_memory_chars={len(static_memory)}")

    # Búsqueda semántica: SIEMPRE se ejecuta si hay query, independiente de static_memory.
    semantic_memory = ""
    if query and query.strip():
        log.info("[MEMORY DEBUG] semantic_search_called=SÍ")
        try:
            semantic_memory = _search_memory(user_id, query)
        except Exception as e:
            log.error(f"[MEMORY DEBUG] _search_memory excepción: {e}", exc_info=True)
            semantic_memory = ""
    else:
        log.info("[MEMORY DEBUG] semantic_search_called=NO (query vacío)")

    log.info(f"[MEMORY DEBUG] semantic_memory_chars={len(semantic_memory)}")

    final_context = (static_memory or "") + (semantic_memory or "")
    log.info(f"[MEMORY DEBUG] final_context_chars={len(final_context)}")

    print(
        f">>> [MEMORY DEBUG] static={len(static_memory)}c "
        f"semantic={len(semantic_memory)}c "
        f"final={len(final_context)}c",
        flush=True,
    )

    parts = [f"Fecha y hora actual: {base_context['fecha_actual']}"]

    if static_memory:
        parts.append(f"Memoria del usuario:\n{static_memory}")

    if semantic_memory:
        parts.append(
            "Información relevante recuperada de la memoria del usuario "
            f"(búsqueda semántica sobre la pregunta actual):\n{semantic_memory}"
        )

    if not static_memory and not semantic_memory:
        parts.append(
            "IMPORTANTE: No tienes información personal del usuario en memoria. "
            "No inventes datos ni nombres. Responde solo con lo que sabes."
        )

    parts.append(f"Estilo:\n{style_context['sources']['style']}")

    full_prompt = "\n\n".join(parts)
    log.info(f"[MEMORY DEBUG] === _build_system_prompt END prompt_chars={len(full_prompt)}")
    return full_prompt


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
        system_prompt = _build_system_prompt(user["id"], query)
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
def debug_memory(user: dict = Depends(require_auth), q: str = "¿Quién es LUCAS DARIO?"):
    """
    Diagnóstico completo de retrieval.
    Parámetro opcional: ?q=tu+query+de+prueba
    Por defecto prueba con '¿Quién es LUCAS DARIO?'
    """
    static_memory = _get_all_memory(user["id"])
    semantic_memory = _search_memory(user["id"], q)
    system_prompt = _build_system_prompt(user["id"], q)

    # Contar filas con y sin embedding
    with get_cursor() as cur:
        cur.execute("""
            SELECT
                md.key,
                md.name,
                COUNT(mr.id) AS total_rows,
                COUNT(mr.embedding) AS rows_con_embedding
            FROM memory_documents md
            LEFT JOIN memory_rows mr ON mr.document_id = md.id
            WHERE md.user_id = %s
            GROUP BY md.key, md.name
            ORDER BY md.created_at ASC
        """, [user["id"]])
        docs = cur.fetchall()

    return {
        "query_usada": q,
        "documentos": [
            {
                "key": d["key"],
                "name": d["name"],
                "total_rows": d["total_rows"],
                "rows_con_embedding": d["rows_con_embedding"],
            }
            for d in docs
        ],
        "static_memory_chars": len(static_memory),
        "static_memory_preview": static_memory[:500] if static_memory else "(vacío)",
        "semantic_memory_chars": len(semantic_memory),
        "semantic_memory_preview": semantic_memory[:500] if semantic_memory else "(vacío — revisar logs del servidor para diagnóstico)",
        "total_prompt_chars": len(system_prompt),
        "system_prompt_preview": system_prompt[:800],
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

