from datetime import datetime, timezone
from app.core.encryption import encrypt, decrypt
from app.core.database import get_cursor


def get_refresh_token(user_id: str) -> str | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT refresh_token FROM credentials WHERE user_id = %s;",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return decrypt(row["refresh_token"])


def save_refresh_token(user_id: str, refresh_token: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO credentials (user_id, refresh_token, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET refresh_token = EXCLUDED.refresh_token;
            """,
            (user_id, encrypt(refresh_token), datetime.now(timezone.utc)),
        )
