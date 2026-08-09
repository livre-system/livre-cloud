import uuid
from datetime import datetime, timezone
from typing import Optional
import psycopg2
import psycopg2.extras
from app.core.config import DATABASE_URL
from app.core.encryption import encrypt, decrypt


def _conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def ensure_tables():
    """Crea las tablas si no existen. Llamar al startup."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id   TEXT NOT NULL,
                    title     TEXT NOT NULL DEFAULT 'Nueva conversación',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_chats_user_id ON chats(user_id);

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id         BIGSERIAL PRIMARY KEY,
                    chat_id    UUID NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON chat_messages(chat_id);
            """)
        conn.commit()


# ── Chats ────────────────────────────────────────────────────────────────────

def create_chat(user_id: str, title: str = "Nueva conversación") -> dict:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chats (user_id, title)
                VALUES (%s, %s)
                RETURNING chat_id, title, created_at, updated_at
                """,
                (user_id, title),
            )
            row = dict(cur.fetchone())
        conn.commit()
    row["chat_id"] = str(row["chat_id"])
    return row


def list_chats(user_id: str) -> list[dict]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chat_id, title, created_at, updated_at
                FROM chats
                WHERE user_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
    return [dict(r) | {"chat_id": str(r["chat_id"])} for r in rows]


def get_chat(chat_id: str, user_id: str) -> Optional[dict]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chat_id, title, created_at, updated_at FROM chats WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id),
            )
            row = cur.fetchone()
    if not row:
        return None
    return dict(row) | {"chat_id": str(row["chat_id"])}


def update_chat_title(chat_id: str, user_id: str, title: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chats SET title = %s, updated_at = NOW() WHERE chat_id = %s AND user_id = %s",
                (title, chat_id, user_id),
            )
        conn.commit()


def delete_chat(chat_id: str, user_id: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chats WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id),
            )
        conn.commit()


# ── Messages ─────────────────────────────────────────────────────────────────

def save_message(chat_id: str, role: str, content: str):
    encrypted = encrypt(content)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chat_messages (chat_id, role, content) VALUES (%s, %s, %s)",
                (chat_id, role, encrypted),
            )
            # Actualizar updated_at del chat
            cur.execute(
                "UPDATE chats SET updated_at = NOW() WHERE chat_id = %s",
                (chat_id,),
            )
        conn.commit()


def get_messages(chat_id: str, user_id: str) -> list[dict]:
    # Verificar que el chat pertenece al usuario
    if not get_chat(chat_id, user_id):
        return []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content FROM chat_messages WHERE chat_id = %s ORDER BY id ASC",
                (chat_id,),
            )
            rows = cur.fetchall()

    messages = []
    for r in rows:
        try:
            content = decrypt(r["content"])
        except Exception:
            # Mensaje viejo sin cifrar o clave distinta — lo saltea
            continue
        messages.append({"role": r["role"], "content": content})
    return messages


def count_user_messages(user_id: str) -> int:
    """Cuenta los mensajes enviados por el usuario (role=user) en las últimas 24hs."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as total
                FROM chat_messages cm
                JOIN chats c ON c.chat_id = cm.chat_id
                WHERE c.user_id = %s AND cm.role = 'user'
                AND cm.created_at >= NOW() - INTERVAL '24 hours'
            """, (user_id,))
            row = cur.fetchone()
    return row["total"] if row else 0
