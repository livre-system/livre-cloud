import asyncio
import logging
from app.memory.repository import (
    user_exists,
    create_user,
    get_or_create_document,
    add_row,
    add_row_without_embedding,
    update_embedding,
    get_index,
    get_documents_with_rows,
    delete_document,
    rename_document,
)
from app.memory.embeddings import get_embedding
from app.core.credentials_repository import save_refresh_token

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# API pública — misma forma que antes (Sheets/Drive),
# así el router no necesita cambios.
# ─────────────────────────────────────────────

def check_memory_status(user_id: str) -> dict:
    if not user_exists(user_id):
        return {"status": "not_found"}
    return {"status": "active"}


def create_user_memory(
    user_id: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
    email: str | None = None,
) -> dict:
    """
    Ya no depende de Drive: crea el usuario directo en Postgres.
    """
    create_user(user_id, email=email)
    if refresh_token:
        save_refresh_token(user_id, refresh_token)
    return {"user_id": user_id}


def read_user_memory(user_id: str) -> dict | None:
    if not user_exists(user_id):
        return None

    return {
        "id": user_id,
        "source": "postgres",
        "index": get_index(user_id),
        "documents": get_documents_with_rows(user_id),
    }


def write_user_memory(
    user_id: str, document: str, name: str, description: str, row: dict
) -> dict:
    if not user_exists(user_id):
        raise ValueError("not_found")

    document_id, created = get_or_create_document(user_id, document, name, description)
    row_with_ts = add_row(document_id, row)

    return {"document": document, "created": created, "row": row_with_ts}


def delete_user_document(user_id: str, key: str) -> bool:
    if not user_exists(user_id):
        raise ValueError("not_found")
    return delete_document(user_id, key)


def rename_user_document(user_id: str, key: str, new_name: str) -> bool:
    if not user_exists(user_id):
        raise ValueError("not_found")
    return rename_document(user_id, key, new_name)


# ─────────────────────────────────────────────────────────────────────────────
# SAVE MESSAGE — diseño no bloqueante
#
# El flujo es:
#   1. Guardar el mensaje inmediatamente en memory_rows (SIN embedding).
#   2. Devolver el control al chatbot (la respuesta NO queda bloqueada).
#   3. En background, generar el embedding y hacer UPDATE de esa fila.
#
# Esto garantiza:
#   - Persistencia inmediata de la información original.
#   - Embedding disponible para futuras búsquedas semánticas sin latencia
#     adicional en la respuesta al usuario.
# ─────────────────────────────────────────────────────────────────────────────

def save_message_to_memory(user_id: str, role: str, content: str) -> None:
    """
    Guarda el mensaje inmediatamente y dispara la generación del embedding
    en background sin bloquear la respuesta del chatbot.

    Llamar desde código síncrono (ej: endpoint FastAPI sync).
    """
    if not user_exists(user_id):
        create_user(user_id)

    document_id, _ = get_or_create_document(
        user_id,
        "chat_log",
        "Chat log",
        "Historial automático de conversaciones",
    )

    # Paso 1: persistir el mensaje de inmediato (sin embedding).
    row_id, _ = add_row_without_embedding(document_id, {"role": role, "message": content})

    # Paso 2: lanzar la generación del embedding en un thread separado.
    # No await — el chatbot puede responder mientras esto ocurre.
    _schedule_embedding_background(row_id, content)


async def save_message_to_memory_async(user_id: str, role: str, content: str) -> None:
    """
    Versión async de save_message_to_memory.
    Usar desde endpoints o funciones async de FastAPI.
    """
    if not user_exists(user_id):
        create_user(user_id)

    document_id, _ = get_or_create_document(
        user_id,
        "chat_log",
        "Chat log",
        "Historial automático de conversaciones",
    )

    # Paso 1: persistir el mensaje de inmediato (sin embedding).
    row_id, _ = add_row_without_embedding(document_id, {"role": role, "message": content})

    # Paso 2: lanzar en background sin bloquear.
    asyncio.create_task(_generate_and_store_embedding(row_id, content))


def _schedule_embedding_background(row_id: str, content: str) -> None:
    """
    Lanza la generación del embedding en un thread del pool de asyncio.
    Compatible con entornos donde el event loop ya está corriendo (FastAPI).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Estamos dentro de FastAPI/uvicorn — crear task directamente.
            loop.create_task(_generate_and_store_embedding(row_id, content))
        else:
            # Contexto síncrono puro — correr en nuevo loop.
            import threading
            def run():
                asyncio.run(_generate_and_store_embedding(row_id, content))
            threading.Thread(target=run, daemon=True).start()
    except RuntimeError:
        # No hay event loop disponible — fallback a thread directo.
        import threading
        def run():
            _generate_embedding_sync(row_id, content)
        threading.Thread(target=run, daemon=True).start()


async def _generate_and_store_embedding(row_id: str, content: str) -> None:
    """Coroutine que genera el embedding y actualiza la fila. No bloquea."""
    try:
        loop = asyncio.get_event_loop()
        # Ejecutar la llamada HTTP a OpenAI en un thread para no bloquear el loop.
        embedding = await loop.run_in_executor(None, get_embedding, content)
        if embedding:
            await loop.run_in_executor(None, update_embedding, row_id, embedding)
            log.info(f"Embedding background OK — row_id={row_id}, dims={len(embedding)}")
        else:
            log.warning(f"Embedding background: get_embedding devolvió None para row_id={row_id}")
    except Exception as e:
        log.error(f"Error generando embedding en background para row_id={row_id}: {e}")


def _generate_embedding_sync(row_id: str, content: str) -> None:
    """Fallback síncrono para contextos sin event loop."""
    try:
        embedding = get_embedding(content)
        if embedding:
            update_embedding(row_id, embedding)
            log.info(f"Embedding sync OK — row_id={row_id}, dims={len(embedding)}")
        else:
            log.warning(f"Embedding sync: get_embedding devolvió None para row_id={row_id}")
    except Exception as e:
        log.error(f"Error generando embedding sync para row_id={row_id}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT MEMORY — texto limpio para el LLM
# ─────────────────────────────────────────────────────────────────────────────

def export_memory(user_id: str) -> str:
    """
    Devuelve toda la memoria del usuario como texto plano limpio.
    Sin headers de documento ni prefijos [user]/[assistant].
    Conserva todo el contenido y los saltos entre bloques.
    """
    if not user_exists(user_id):
        return ""
    documents = get_documents_with_rows(user_id)
    blocks = []
    for doc in documents:
        for row in doc["rows"]:
            msg = row.get("message", "").strip()
            if msg:
                blocks.append(msg)
    return "\n\n".join(blocks)


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD TXT — chunking semántico por párrafos (igual que Hash Cloud)
# ─────────────────────────────────────────────────────────────────────────────

def upload_txt_as_memory(
    user_id: str,
    filename: str,
    content: str,
    chat_id: str | None = None,
) -> dict:
    """
    Sube un archivo .txt como memoria.
    Chunking: divide por párrafos (\\n\\n), máx 6000 chars por chunk.
    Si un párrafo supera 6000 chars, se subdivide.
    Cada chunk se guarda con embedding sincrónico (no es tiempo real,
    es un proceso de carga, la latencia es aceptable).
    """
    if not user_exists(user_id):
        create_user(user_id)

    import time

    # Clave única por timestamp para no pisar archivos anteriores del mismo nombre.
    key = (
        filename.replace(".txt", "").replace(" ", "_").lower()
        + "_"
        + str(int(time.time()))
    )
    name = filename.replace(".txt", "")
    description = f"Cargado desde archivo: {filename}"

    document_id, created = get_or_create_document(
        user_id, key, name, description, chat_id=chat_id
    )

    # Un párrafo = un chunk semántico.
    # Solo subdividir si un párrafo individual supera MAX_CHARS.
    MAX_CHARS = 6000
    paragraphs = [p.strip() for p in content.strip().split("\n\n") if p.strip()]

    chunks = []
    for para in paragraphs:
        if len(para) <= MAX_CHARS:
            chunks.append(para)
        else:
            for i in range(0, len(para), MAX_CHARS):
                part = para[i : i + MAX_CHARS].strip()
                if part:
                    chunks.append(part)

    total_chunks = len(chunks)
    log.info(f"[UPLOAD] archivo={filename} | document_id={document_id} | total_chunks={total_chunks}")

    saved = 0
    failed = 0
    for idx, chunk_text in enumerate(chunks, 1):
        if not chunk_text.strip():
            continue
        try:
            log.info(
                f"[UPLOAD] chunk {idx}/{total_chunks} | "
                f"chars={len(chunk_text)} | "
                f"preview={chunk_text[:80]!r}"
            )
            add_row(document_id, {"message": chunk_text}, with_embedding=True)
            log.info(f"[UPLOAD] chunk {idx}/{total_chunks} — guardado + embedding OK")
            saved += 1
        except Exception as e:
            log.error(f"[UPLOAD] chunk {idx}/{total_chunks} — ERROR: {e}", exc_info=True)
            failed += 1

    log.info(
        f"[UPLOAD] COMPLETO — archivo={filename} | "
        f"guardados={saved} | fallidos={failed} | total={total_chunks}"
    )
    return {"document": key, "created": created, "rows_added": saved}


def extract_uploaded_file(filename: str, content: bytes, content_type: str | None = None) -> str:
    """Extrae texto de formatos que pueden cargarse desde el front."""
    import io
    from pathlib import Path

    suffix = Path(filename or "").suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".log"}:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("El archivo de texto debe estar en UTF-8") from exc

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(page.strip() for page in pages if page.strip())
        if not text:
            raise ValueError("El PDF no contiene texto extraíble; requiere OCR")
        return text

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
        try:
            from PIL import Image
            import pytesseract

            return pytesseract.image_to_string(
                Image.open(io.BytesIO(content)), lang="spa+eng"
            ).strip()
        except ImportError as exc:
            raise ValueError("OCR no disponible en el servidor") from exc
        except Exception as exc:
            raise ValueError("No se pudo leer el texto de la imagen") from exc

    raise ValueError("Formato no soportado. Usá TXT, PDF, CSV, JSON o una imagen")


def upload_file_as_memory(
    user_id: str,
    filename: str,
    content: bytes,
    content_type: str | None = None,
    chat_id: str | None = None,
) -> dict:
    """Entrada única para cargas manuales desde el front."""
    text = extract_uploaded_file(filename, content, content_type)
    if not text.strip():
        raise ValueError("No se encontró texto para cargar en la memoria")
    return upload_txt_as_memory(user_id, filename, text, chat_id=chat_id)
