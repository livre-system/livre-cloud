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
from app.memory.structured_ingestion import search_structured_knowledge
from app.core.database import get_cursor
from app.core.encryption import decrypt
from app.chat.models import ChatRequest, SynthesizeRequest
import app.chat.repository as repo

router = APIRouter(prefix="/chat", tags=["chat"])




def _get_all_memory(user_id: str, chat_id: str | None = None) -> str:
    """Trae toda la memoria del usuario desencriptada."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT md.name, mr.data
                FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s AND md.key NOT LIKE 'chat_log%'
                  AND (md.chat_id IS NULL OR md.chat_id = %s)
                ORDER BY mr.created_at ASC
            """, [user_id, chat_id])
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


def _search_memory(user_id: str, query: str, chat_id: str | None = None) -> str:
    """Combina corpus empresarial estructurado y memoria privada del chat."""
    lines = []
    max_chars = 24000
    try:
        for row in search_structured_knowledge(query, limit=8):
            lines.append(
                f"[Datos estructurados — {row['source_table']} — similitud {row['similarity']:.2f}]\n"
                f"{row['content'][:2500]}"
            )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("No se pudo buscar corpus estructurado: %s", exc)

    rows = search_memory_by_embedding(user_id, query, chat_id=chat_id, limit=12)
    for r in rows:
        msg = r["data"].get("message", "")
        if not msg:
            continue
        try:
            msg = decrypt(msg)
        except Exception:
            pass
        lines.append(f"[{r['name']}]\n{msg.strip()[:1200]}")
    result = "\n\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars].rsplit("\n\n", 1)[0]
    return result


def _get_operational_context() -> str:
    """Resumen acotado de la operación viva para consultas de LIVI."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT status, count(*) AS total
                FROM livi_trips
                GROUP BY status
                ORDER BY status
            """)
            totals = ", ".join(f"{r['status'] or 'sin_estado'}={r['total']}" for r in cur.fetchall())
            cur.execute("""
                SELECT id, origen, destino, status, tracking_driver_name,
                       tracking_plate, created_at, updated_at
                FROM livi_trips
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 12
            """)
            recent = cur.fetchall()
        lines = [f"Estados de viajes: {totals or 'sin datos'}"]
        for row in recent:
            lines.append(
                f"Viaje {row['id']}: {row['status'] or 'sin estado'}; "
                f"origen={row['origen'] or '—'}; destino={row['destino'] or '—'}; "
                f"chofer={row['tracking_driver_name'] or '—'}; patente={row['tracking_plate'] or '—'}; "
                f"actualizado={row['updated_at'] or row['created_at']}"
            )
        return "\n".join(lines)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("No se pudo cargar contexto operacional: %s", exc)
        return ""


def _build_system_prompt(user_id: str, query: str = "", chat_id: str | None = None) -> str:
    sources = get_livre_sources()
    base_context = compile_base_context(sources)
    style_context = compile_style_context(sources)

    semantic_text = _search_memory(user_id, query, chat_id) if query else ""
    # Una consulta concreta debe usar sólo los resultados relevantes. Mandar
    # toda la memoria histórica puede generar prompts de decenas de MB.
    memory_text = semantic_text or _get_all_memory(user_id, chat_id)

    operational_text = _get_operational_context()
    print(f">>> SYSTEM PROMPT MEMORY: {len(memory_text)} chars", flush=True)
    return (
        f"Fecha y hora actual: {base_context['fecha_actual']}\n\n"
        + (f"Datos operativos actuales:\n{operational_text}\n\n" if operational_text else "")
        + (f"Contexto operativo relevante:\n{memory_text}\n\n" if memory_text else "")
        + f"Estilo:\n{style_context['sources']['style']}\n\n"
        + "Regla de preservación de transcripciones de LIVI:\n"
        + "Conservá cada transcripción del usuario literalmente, sin corregir, resumir ni reformular sus palabras. "
        + "Si agregás interpretación, análisis o una decisión, escribila debajo en un bloque separado titulado 'Conclusión'. "
        + "Nunca mezcles la conclusión con la transcripción original ni la presentes como si fuera texto del usuario."
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

        # Guardar el mensaje del usuario
        last_user_msg = body.messages[-1] if body.messages else None
        if last_user_msg and last_user_msg.role == "user":
            repo.save_message(chat_id, "user", last_user_msg.content)
            save_message_to_memory(user["id"], "user", last_user_msg.content, chat_id)

            # Auto-título con las primeras palabras del primer mensaje
            chat = repo.get_chat(chat_id, user["id"])
            if chat and chat["title"] == "Nueva conversación":
                auto_title = last_user_msg.content[:50].strip()
                repo.update_chat_title(chat_id, user["id"], auto_title)

        query = last_user_msg.content if last_user_msg else ""
        system_prompt = _build_system_prompt(user["id"], query, chat_id)
        import logging
        logging.getLogger(__name__).info("[CHAT DEBUG] prompt_chars=%d messages=%d provider=%s", len(system_prompt), len(body.messages), body.provider)
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
        save_message_to_memory(user["id"], "assistant", reply, chat_id)

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
        chat_id = body.chat_id

        if not chat_id:
            new = repo.create_chat(user["id"])
            chat_id = new["chat_id"]

        # Guardar mensaje del usuario
        last_user_msg = body.messages[-1] if body.messages else None
        if last_user_msg and last_user_msg.role == "user":
            repo.save_message(chat_id, "user", last_user_msg.content)
            save_message_to_memory(user["id"], "user", last_user_msg.content, chat_id)

            chat = repo.get_chat(chat_id, user["id"])
            if chat and chat["title"] == "Nueva conversación":
                auto_title = last_user_msg.content[:50].strip()
                repo.update_chat_title(chat_id, user["id"], auto_title)

        query = last_user_msg.content if last_user_msg else ""
        system_prompt = _build_system_prompt(user["id"], query, chat_id)
        import logging
        logging.getLogger(__name__).info("[CHAT DEBUG] prompt_chars=%d messages=%d provider=%s", len(system_prompt), len(body.messages), body.provider)
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
                    save_message_to_memory(user["id"], "assistant", complete, chat_id)
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
    memory_text = _get_all_memory(user["id"])
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
        system_prompt = _build_system_prompt(user["id"], transcript, chat_id)
        llm = get_llm_provider()
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        reply = llm.generate(messages)

        # 6. Guardar respuesta
        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(user["id"], "assistant", reply, chat_id)

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

