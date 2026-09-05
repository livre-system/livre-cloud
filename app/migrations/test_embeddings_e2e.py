# test_embeddings_e2e.py — Livre Cloud
#
# Prueba end-to-end del sistema de embeddings/memoria:
#   mensaje → embedding → almacenamiento → búsqueda semántica
#
# Ejecutar desde la raíz del proyecto:
#   python test_embeddings_e2e.py
#
# Requiere: OPENAI_API_KEY y DATABASE_URL en el entorno.

import os
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Verificaciones previas ────────────────────────────────────────────────────

def check_env():
    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not os.getenv("DATABASE_URL"):
        missing.append("DATABASE_URL")
    if missing:
        print(f"\n❌ Variables de entorno faltantes: {', '.join(missing)}")
        sys.exit(1)
    print("✅ Variables de entorno OK")

# ── Imports del proyecto ──────────────────────────────────────────────────────

check_env()

from app.memory.embeddings import get_embedding, get_query_embedding
from app.memory.repository import (
    user_exists,
    create_user,
    get_or_create_document,
    add_row_without_embedding,
    update_embedding,
    search_memory_by_embedding,
)
from app.core.database import get_cursor

TEST_USER_ID = "test_embeddings_e2e_livre"

# ── Utilidades ────────────────────────────────────────────────────────────────

def cleanup():
    """Elimina datos del usuario de prueba."""
    with get_cursor() as cur:
        cur.execute("""
            DELETE FROM memory_rows mr
            USING memory_documents md
            WHERE mr.document_id = md.id
              AND md.user_id = %s;
        """, (TEST_USER_ID,))
        cur.execute("DELETE FROM memory_documents WHERE user_id = %s;", (TEST_USER_ID,))
        cur.execute("DELETE FROM memory_users WHERE user_id = %s;", (TEST_USER_ID,))
    print("🧹 Datos de prueba eliminados")


def section(title: str):
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_1_embedding_generation():
    section("TEST 1: Generación de embedding")

    text = "Me llamo Claude y vivo en Buenos Aires."
    emb = get_embedding(text)

    assert emb is not None, "get_embedding() devolvió None"
    assert len(emb) == 1536, f"Dimensión incorrecta: {len(emb)} (esperado 1536)"
    assert all(isinstance(v, float) for v in emb), "Los valores no son floats"

    print(f"  ✅ get_embedding() → {len(emb)} dims, tipo={type(emb[0]).__name__}")

    query_emb = get_query_embedding(text)
    assert query_emb is not None, "get_query_embedding() devolvió None"
    assert len(query_emb) == 1536, f"Dimensión de query embedding incorrecta: {len(query_emb)}"

    print(f"  ✅ get_query_embedding() → {len(query_emb)} dims")
    return emb


def test_2_save_message_immediate(document_id: str):
    section("TEST 2: Persistencia inmediata del mensaje (sin esperar embedding)")

    content = "Hoy tuve una reunión importante con el equipo de diseño sobre la interfaz de Livre."
    t0 = time.time()
    row_id, row = add_row_without_embedding(document_id, {"role": "user", "message": content})
    elapsed = time.time() - t0

    assert row_id is not None, "row_id es None"
    assert "created_at" in row, "created_at no está en el resultado"

    print(f"  ✅ Mensaje guardado en {elapsed:.3f}s (sin embedding, no bloqueante)")
    print(f"     row_id = {row_id}")
    return row_id, content


def test_3_embedding_stored(row_id: str, content: str):
    section("TEST 3: Generación y almacenamiento de embedding en background")

    embedding = get_embedding(content)
    assert embedding is not None, "Embedding es None"
    assert len(embedding) == 1536

    update_embedding(row_id, embedding)

    # Verificar en BD
    with get_cursor() as cur:
        cur.execute(
            "SELECT embedding IS NOT NULL AS has_emb FROM memory_rows WHERE id = %s;",
            (row_id,)
        )
        result = cur.fetchone()

    assert result is not None, f"No se encontró la fila {row_id}"
    assert result["has_emb"], "El embedding no se guardó en la BD"

    print(f"  ✅ Embedding almacenado OK en memory_rows (row_id={row_id})")
    print(f"     dims={len(embedding)}")


def test_4_semantic_search(document_id: str):
    section("TEST 4: Búsqueda semántica")

    # Insertar varios mensajes con embeddings.
    messages = [
        "Hoy tuve una reunión importante con el equipo de diseño sobre la interfaz de Livre.",
        "El clima en Buenos Aires estuvo muy agradable esta semana.",
        "Necesito revisar la arquitectura del sistema de embeddings antes del lanzamiento.",
        "Mi gato se llama Mochi y le gusta dormir en el teclado.",
        "El sistema de memoria vectorial permite búsquedas semánticas muy precisas.",
    ]

    print("  Insertando mensajes con embeddings...")
    for msg in messages:
        row_id, _ = add_row_without_embedding(document_id, {"role": "user", "message": msg})
        emb = get_embedding(msg)
        if emb:
            update_embedding(row_id, emb)
            print(f"  → OK: '{msg[:50]}...' " if len(msg) > 50 else f"  → OK: '{msg}'")

    # Búsqueda semántica.
    print()
    query = "sistema vectorial de memoria y búsqueda semántica"
    print(f"  Query: '{query}'")
    print()

    results = search_memory_by_embedding(TEST_USER_ID, query, limit=3, min_similarity=0.30)

    assert len(results) > 0, (
        "La búsqueda semántica no devolvió resultados. "
        "Verificar que pgvector esté habilitado y la columna embedding exista."
    )

    print(f"  ✅ {len(results)} resultado(s) encontrado(s):")
    for r in results:
        similarity = r.get("similarity", 0)
        data = r.get("data", {})
        msg = data.get("message", "")
        try:
            from app.core.encryption import decrypt
            msg = decrypt(msg)
        except Exception:
            pass
        print(f"     [{similarity:.3f}] {msg[:80]}")

    # El resultado más relevante debe ser sobre embeddings/memoria.
    top_msg = results[0].get("data", {}).get("message", "")
    try:
        from app.core.encryption import decrypt
        top_msg = decrypt(top_msg)
    except Exception:
        pass

    assert any(
        kw in top_msg.lower()
        for kw in ["embedding", "memoria", "vectorial", "semántic", "arquitectura"]
    ), f"El resultado más relevante no parece correcto: '{top_msg}'"

    print(f"\n  ✅ Resultado más relevante es semánticamente correcto")


def test_5_min_similarity_filter():
    section("TEST 5: Filtro de similitud mínima")

    # Buscar algo completamente irrelevante — no debe devolver resultados
    # con similitud >= 0.50 en una colección de mensajes sobre Livre/tech.
    results_strict = search_memory_by_embedding(
        TEST_USER_ID, "receta de empanadas tucumanas", limit=10, min_similarity=0.70
    )
    print(f"  Búsqueda muy específica (min_similarity=0.70): {len(results_strict)} resultado(s)")

    results_loose = search_memory_by_embedding(
        TEST_USER_ID, "sistema de memoria y búsqueda", limit=10, min_similarity=0.30
    )
    print(f"  Búsqueda relevante (min_similarity=0.30): {len(results_loose)} resultado(s)")

    assert len(results_loose) >= len(results_strict), (
        "El filtro de similitud no está funcionando: "
        "búsqueda suelta debería devolver >= resultados que búsqueda estricta"
    )

    print("  ✅ Filtro de similitud mínima funciona correctamente")


# ── Runner ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 55)
    print("  LIVRE CLOUD — TEST E2E: Embeddings & Memoria")
    print("=" * 55)

    try:
        # Setup
        if not user_exists(TEST_USER_ID):
            create_user(TEST_USER_ID)
        document_id, _ = get_or_create_document(
            TEST_USER_ID,
            "chat_log",
            "Chat log",
            "Historial automático de conversaciones",
        )

        # Tests
        test_1_embedding_generation()
        row_id, content = test_2_save_message_immediate(document_id)
        test_3_embedding_stored(row_id, content)
        test_4_semantic_search(document_id)
        test_5_min_similarity_filter()

        print("\n" + "=" * 55)
        print("  ✅ TODOS LOS TESTS PASARON")
        print("  Flujo verificado: mensaje → embedding → BD → búsqueda")
        print("=" * 55 + "\n")

    except AssertionError as e:
        print(f"\n❌ TEST FALLIDO: {e}\n")
        sys.exit(1)
    except Exception as e:
        log.exception(f"Error inesperado: {e}")
        sys.exit(1)
    finally:
        print()
        resp = input("¿Eliminar datos de prueba? [S/n]: ").strip().lower()
        if resp != "n":
            cleanup()


if __name__ == "__main__":
    main()
