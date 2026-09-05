# backfill_embeddings.py — Livre Cloud
#
# Genera embeddings para todas las filas de memory_rows que no los tienen.
# Ejecutar desde la raíz del proyecto:
#
#   python backfill_embeddings.py <user_id>
#   python backfill_embeddings.py --all          ← todos los usuarios
#
# El script es idempotente: si se interrumpe y se vuelve a correr,
# solo procesa las filas que siguen sin embedding.

import logging
import time
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

from app.core.database import get_cursor
from app.core.encryption import decrypt
from app.memory.embeddings import get_embedding

BATCH_SIZE = 10
PAUSE_BETWEEN_BATCHES = 0.5  # segundos — respetar rate limits de OpenAI


# ─────────────────────────────────────────────────────────────────────────────
# Consultas
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_user_ids() -> list[str]:
    """Devuelve todos los user_id que tienen al menos una fila sin embedding."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT md.user_id
            FROM memory_rows mr
            JOIN memory_documents md ON md.id = mr.document_id
            WHERE mr.embedding IS NULL
              AND mr.data ? 'message'
              AND mr.data->>'message' IS NOT NULL
              AND mr.data->>'message' != ''
            ORDER BY md.user_id;
        """)
        return [r["user_id"] for r in cur.fetchall()]


def fetch_rows_without_embedding(user_id: str) -> list[dict]:
    """Trae todas las filas sin embedding del usuario que tienen message en data."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT mr.id, mr.data
            FROM memory_rows mr
            JOIN memory_documents md ON md.id = mr.document_id
            WHERE md.user_id = %s
              AND mr.embedding IS NULL
              AND mr.data ? 'message'
              AND mr.data->>'message' IS NOT NULL
              AND mr.data->>'message' != ''
            ORDER BY mr.created_at ASC;
        """, (user_id,))
        return cur.fetchall()


def count_pending(user_id: str) -> int:
    with get_cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS pending
            FROM memory_rows mr
            JOIN memory_documents md ON md.id = mr.document_id
            WHERE md.user_id = %s
              AND mr.embedding IS NULL;
        """, (user_id,))
        return cur.fetchone()["pending"]


def update_embedding(row_id, embedding: list[float]) -> None:
    """Actualiza SOLO el campo embedding de una fila existente."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE memory_rows SET embedding = %s::vector WHERE id = %s;",
            (str(embedding), row_id),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Procesamiento por usuario
# ─────────────────────────────────────────────────────────────────────────────

def backfill_user(user_id: str, auto_confirm: bool = False) -> dict:
    rows = fetch_rows_without_embedding(user_id)
    total = len(rows)

    print()
    print("=" * 55)
    print(f"  Usuario         : {user_id}")
    print(f"  Filas a procesar: {total}")
    print("=" * 55)

    if total == 0:
        log.info("Nada que procesar para este usuario.")
        return {"total": 0, "updated": 0, "failed": 0}

    if not auto_confirm:
        print()
        confirm = input(f"¿Confirmar backfill de {total} filas? [s/N]: ").strip().lower()
        if confirm != "s":
            print("Operación cancelada.")
            return {"total": total, "updated": 0, "failed": 0, "cancelled": True}

    print()
    updated = 0
    failed = 0
    failed_ids = []

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        log.info(f"Procesando lote {i // BATCH_SIZE + 1} ({len(batch)} filas)...")

        for row in batch:
            row_id = row["id"]
            try:
                raw_message = row["data"].get("message", "")
                if not raw_message:
                    log.warning(f"  Fila {row_id}: message vacío, salteando.")
                    continue

                # El mensaje puede estar encriptado (rows existentes) o en plano.
                try:
                    message = decrypt(raw_message)
                except Exception:
                    message = raw_message

                if not message or not message.strip():
                    log.warning(f"  Fila {row_id}: mensaje vacío tras decrypt, salteando.")
                    continue

                embedding = get_embedding(message)
                if not embedding:
                    log.warning(
                        f"  Fila {row_id}: get_embedding() devolvió None, salteando."
                    )
                    failed += 1
                    failed_ids.append(row_id)
                    continue

                update_embedding(row_id, embedding)
                log.info(f"  Fila {row_id}: OK ({len(embedding)} dims).")
                updated += 1

            except Exception as e:
                log.error(f"  Fila {row_id}: ERROR — {e}")
                failed += 1
                failed_ids.append(row_id)

        # Pausa entre lotes para respetar rate limits.
        if i + BATCH_SIZE < total:
            time.sleep(PAUSE_BETWEEN_BATCHES)

    # Verificación final contra BD.
    pending = count_pending(user_id)

    print()
    print("=" * 55)
    print("  RESUMEN")
    print(f"  Total procesadas : {total}")
    print(f"  Actualizadas OK  : {updated}")
    print(f"  Fallidas         : {failed}")
    print(f"  Pendientes en BD : {pending}")
    if failed_ids:
        print(f"  IDs fallidos     : {failed_ids}")
    print("=" * 55)

    return {
        "total": total,
        "updated": updated,
        "failed": failed,
        "pending": pending,
        "failed_ids": failed_ids,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print()
        print("Uso:")
        print("  python backfill_embeddings.py <user_id>   ← un usuario")
        print("  python backfill_embeddings.py --all       ← todos los usuarios")
        print()
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--all":
        user_ids = fetch_all_user_ids()
        if not user_ids:
            print("No hay filas sin embedding en ningún usuario.")
            sys.exit(0)

        print(f"\nUsuarios con filas pendientes: {len(user_ids)}")
        for uid in user_ids:
            print(f"  - {uid}")

        print()
        confirm = input(
            f"¿Confirmar backfill de TODOS los usuarios ({len(user_ids)})? [s/N]: "
        ).strip().lower()
        if confirm != "s":
            print("Operación cancelada.")
            sys.exit(0)

        totals = {"total": 0, "updated": 0, "failed": 0}
        for uid in user_ids:
            result = backfill_user(uid, auto_confirm=True)
            for k in ("total", "updated", "failed"):
                totals[k] += result.get(k, 0)

        print()
        print("=" * 55)
        print("  RESUMEN GLOBAL")
        print(f"  Usuarios procesados : {len(user_ids)}")
        print(f"  Total filas         : {totals['total']}")
        print(f"  Actualizadas OK     : {totals['updated']}")
        print(f"  Fallidas            : {totals['failed']}")
        print("=" * 55)

    else:
        backfill_user(arg)
