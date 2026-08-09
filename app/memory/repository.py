from datetime import datetime, timezone
from psycopg2.extras import Json
from app.core.database import get_cursor
from app.core.encryption import encrypt, decrypt
from app.memory.embeddings import get_embedding


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


def get_or_create_document(user_id: str, key: str, name: str, description: str, chat_id: str | None = None) -> tuple[str, bool]:
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


def search_memory_by_embedding(user_id: str, query: str, limit: int = 10) -> list[dict]:
    """Busca en memoria usando similitud de embeddings."""
    query_embedding = get_embedding(query)
    if not query_embedding:
        return []
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT md.name, mr.data, 1 - (mr.embedding <=> %s::vector) AS similarity
                FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s AND mr.embedding IS NOT NULL
                ORDER BY mr.embedding <=> %s::vector
                LIMIT %s
            """, (str(query_embedding), user_id, str(query_embedding), limit))
            return cur.fetchall()
    except Exception:
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
            result.append({
                "id": str(doc["id"]),
                "key": doc["key"],
                "name": doc["name"],
                "description": doc["description"],
                "created_at": doc["created_at"].isoformat(),
                "rows": [
                    {**{k: (decrypt(v) if k == "message" else v) for k, v in r["data"].items()}, "created_at": r["created_at"].isoformat()}
                    for r in rows
                ],
            })
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
