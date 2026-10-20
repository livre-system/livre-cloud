from datetime import datetime, timezone
from psycopg2.extras import Json
from app.core.database import get_cursor
from app.core.encryption import encrypt, decrypt
from app.memory.embeddings import get_embedding, get_query_embedding


def user_exists(user_id: str) -> bool:
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM memory_users WHERE user_id = %s;", (user_id,))
        return cur.fetchone() is not None


def create_user(user_id: str, email: str | None = None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_users (user_id, email, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET email = COALESCE(EXCLUDED.email, memory_users.email);
            """,
            (user_id, email, datetime.now(timezone.utc)),
        )


def get_or_create_document(
    user_id: str,
    key: str,
    name: str,
    description: str,
    chat_id: str | None = None,
) -> tuple[str, bool]:
    """Devuelve (document_id, created). created=True si se acaba de crear."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM memory_documents WHERE user_id = %s AND key = %s;",
            (user_id, key),
        )
        row = cur.fetchone()
        if row:
            return str(row["id"]), False

        cur.execute(
            """
            INSERT INTO memory_documents (user_id, key, name, description, created_at, chat_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (user_id, key, name, description, datetime.now(timezone.utc), chat_id),
        )
        new_id = cur.fetchone()["id"]
        return str(new_id), True


def add_row(document_id: str, data: dict, with_embedding: bool = False) -> dict:
    """
    Inserta una fila en memory_rows.

    Si with_embedding=True y hay mensaje, genera el embedding ANTES del INSERT
    para que quede disponible de inmediato en búsquedas semánticas.
    El embedding se genera de forma sincrónica aquí; el desacoplamiento
    async se maneja en la capa de servicio (service.py) cuando se quiere
    no bloquear la respuesta del chatbot.
    """
    created_at = datetime.now(timezone.utc)
    encrypted_data = {**data}
    if "message" in encrypted_data:
        encrypted_data["message"] = encrypt(encrypted_data["message"])

    embedding = None
    if with_embedding and "message" in data and data["message"]:
        embedding = get_embedding(data["message"])

    with get_cursor() as cur:
        if embedding:
            cur.execute(
                """
                INSERT INTO memory_rows (document_id, data, created_at, embedding)
                VALUES (%s, %s, %s, %s::vector)
                RETURNING id;
                """,
                (document_id, Json(encrypted_data), created_at, str(embedding)),
            )
        else:
            cur.execute(
                """
                INSERT INTO memory_rows (document_id, data, created_at)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (document_id, Json(encrypted_data), created_at),
            )
    return {**data, "created_at": created_at.isoformat()}


def add_row_without_embedding(document_id: str, data: dict) -> tuple[str, dict]:
    """
    Inserta la fila SIN embedding y devuelve (row_id, row_dict).
    Usar cuando el embedding se va a generar en background después.
    """
    created_at = datetime.now(timezone.utc)
    encrypted_data = {**data}
    if "message" in encrypted_data:
        encrypted_data["message"] = encrypt(encrypted_data["message"])

    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_rows (document_id, data, created_at)
            VALUES (%s, %s, %s)
            RETURNING id;
            """,
            (document_id, Json(encrypted_data), created_at),
        )
        row_id = str(cur.fetchone()["id"])

    return row_id, {**data, "created_at": created_at.isoformat()}


def update_embedding(row_id: str, embedding: list[float]) -> None:
    """Actualiza SOLO el campo embedding de una fila existente."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE memory_rows SET embedding = %s::vector WHERE id = %s;",
            (str(embedding), row_id),
        )


def search_memory_by_embedding(
    user_id: str,
    query: str,
    limit: int = 20,
    min_similarity: float = 0.50,
    chat_id: str | None = None,
) -> list[dict]:
    """
    Busca en memoria usando similitud coseno de embeddings.

    - Usa get_query_embedding (función semántica correcta para búsqueda).
    - Aplica threshold min_similarity para descartar resultados irrelevantes.
    - Busca en documentos cargados por la empresa y excluye transcripciones
      de chats: el historial privado se envía solo con el chat seleccionado.
    - Ordena por distancia coseno ascendente (más similar primero).
    """
    query_embedding = get_query_embedding(query)
    if not query_embedding:
        return []
    try:
        scope_sql = "AND md.chat_id IS NULL"
        scope_params: list[str] = []
        if chat_id:
            scope_sql = "AND (md.chat_id IS NULL OR md.chat_id = %s)"
            scope_params = [chat_id]
        with get_cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    md.name,
                    md.key,
                    mr.data,
                    1 - (mr.embedding <=> %s::vector) AS similarity
                FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s
                  AND md.key NOT LIKE 'chat_log%%'
                  AND mr.embedding IS NOT NULL
                  AND 1 - (mr.embedding <=> %s::vector) >= %s
                  {scope_sql}
                ORDER BY mr.embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    str(query_embedding),
                    user_id,
                    str(query_embedding),
                    min_similarity,
                    *scope_params,
                    str(query_embedding),
                    limit,
                ),
            )
            return cur.fetchall()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error en search_memory_by_embedding: {e}")
        return []


def get_index(user_id: str) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, description, created_at
            FROM memory_documents
            WHERE user_id = %s
            ORDER BY created_at ASC;
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


def get_documents_with_rows(user_id: str) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, key, name, description, created_at
            FROM memory_documents
            WHERE user_id = %s
            ORDER BY created_at ASC;
            """,
            (user_id,),
        )
        documents = cur.fetchall()

        result = []
        for doc in documents:
            cur.execute(
                """
                SELECT data, created_at
                FROM memory_rows
                WHERE document_id = %s
                ORDER BY created_at ASC;
                """,
                (doc["id"],),
            )
            rows = cur.fetchall()
            result.append(
                {
                    "id": str(doc["id"]),
                    "key": doc["key"],
                    "name": doc["name"],
                    "description": doc["description"],
                    "created_at": doc["created_at"].isoformat(),
                    "rows": [
                        {
                            **{
                                k: (decrypt(v) if k == "message" else v)
                                for k, v in r["data"].items()
                            },
                            "created_at": r["created_at"].isoformat(),
                        }
                        for r in rows
                    ],
                }
            )
        return result


def delete_document(user_id: str, key: str) -> bool:
    """Elimina el documento y todas sus filas. Devuelve True si existía."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM memory_documents WHERE user_id = %s AND key = %s;",
            (user_id, key),
        )
        row = cur.fetchone()
        if not row:
            return False
        doc_id = row["id"]
        cur.execute("DELETE FROM memory_rows WHERE document_id = %s;", (doc_id,))
        cur.execute("DELETE FROM memory_documents WHERE id = %s;", (doc_id,))
        return True


def rename_document(user_id: str, key: str, new_name: str) -> bool:
    """Renombra el documento (name y key). Devuelve True si existía."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id FROM memory_documents WHERE user_id = %s AND key = %s;",
            (user_id, key),
        )
        row = cur.fetchone()
        if not row:
            return False
        cur.execute(
            "UPDATE memory_documents SET name = %s, key = %s WHERE id = %s;",
            (new_name, new_name, row["id"]),
        )
        return True
