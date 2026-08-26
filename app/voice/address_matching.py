"""
app/voice/address_matching.py

Lógica compartida de detección y validación de direcciones (calle + altura)
contra la tabla street_embeddings de Bahía Blanca.

Usada por:
  - app/voice/router.py   (flujo Twilio + GPT)
  - app/voice/telnyx.py   (flujo Telnyx + ElevenLabs)

ESTRATEGIA DE MATCHING (v2):
  El transcript puede llegar muy distorsionado por el STT de ElevenLabs:
  "Saavedra" → "Cavera", "Casilda", "Vereda", etc.

  Solución: combinar dos scores independientes y tomar el mejor candidato.

    1. Embedding (semántico, via OpenAI + pgvector)
       → bueno cuando el nombre está medianamente bien transcripto
       → devuelve similarity ∈ [0, 1] directamente desde Postgres

    2. Fuzzy textual (difflib.SequenceMatcher, stdlib pura)
       → compara carácter a carácter contra TODOS los nombres de la DB
       → muy bueno para errores de STT con fonética parecida:
         "cavera" vs "saavedra" → ratio ~0.57
       → se normaliza: sin tildes, minúsculas

    Score final = max(embedding_sim, fuzzy_sim * FUZZY_WEIGHT)
    Threshold final = COMBINED_THRESHOLD

  No se baja el threshold del embedding en crudo: el peso del fuzzy
  compensa errores de STT sin abrir la puerta a falsos positivos.

  CALLE SIN ALTURA:
    Si el usuario dice solo la calle, se guarda en call_state["pending_street"]
    y se espera la altura en el siguiente turno.
"""

import re
import unicodedata
import logging
from difflib import SequenceMatcher

from app.memory.embeddings import get_embedding
from app.core.database import get_cursor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parámetros de scoring
# ---------------------------------------------------------------------------

# Peso que se le aplica al score fuzzy antes de comparar con el embedding.
# Ej: fuzzy_ratio=0.80 → fuzzy_score_final = 0.80 * 0.90 = 0.72
# Mantenlo < 1.0 para que el embedding siga siendo el camino preferido.
FUZZY_WEIGHT = 0.90

# Score mínimo combinado para considerar un candidato válido.
COMBINED_THRESHOLD = 0.52

# Cuántos candidatos devolver al llamador.
TOP_N = 3

# ---------------------------------------------------------------------------
# Cache en memoria
# ---------------------------------------------------------------------------

# street_names: todos los nombres de calle tal como están en la DB (cargado una vez)
_street_names_cache: list[str] | None = None

# embedding cache: texto de calle normalizado → lista de candidatos
_embedding_cache: dict[str, list[tuple[str, float]]] = {}  # → [(name, sim)]


# ---------------------------------------------------------------------------
# Normalización de texto
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "calle", "avenida", "av", "avda", "bulevar", "boulevard", "pasaje", "pje",
    "ruta", "camino", "vereda", "esquina", "esq", "entre", "y", "la", "el",
    "las", "los", "de", "del", "desde", "hasta", "por",
}


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_street_text(text: str) -> str:
    """
    Normalización para comparación fuzzy y como input al embedding:
      - minúsculas
      - sin tildes
      - sin puntuación
      - sin stop words de vía pública
      - sin números (se extraen aparte)
    """
    t = text.lower()
    t = _strip_accents(t)
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    t = re.sub(r'\b\d+\b', ' ', t)       # quitar números
    tokens = [w for w in t.split() if w not in _STOP_WORDS]
    return " ".join(tokens).strip()


# ---------------------------------------------------------------------------
# Carga de todos los nombres de calle (una vez)
# ---------------------------------------------------------------------------

def _load_all_street_names() -> list[str]:
    global _street_names_cache
    if _street_names_cache is not None:
        return _street_names_cache
    try:
        with get_cursor() as cur:
            cur.execute("SELECT name FROM street_embeddings ORDER BY name")
            rows = cur.fetchall()
            _street_names_cache = [r["name"] for r in rows]
            logger.info(f"[ADDRESS] street_names cargados: {len(_street_names_cache)}")
    except Exception as e:
        logger.warning(f"[ADDRESS] No se pudieron cargar street_names: {e}")
        _street_names_cache = []
    return _street_names_cache


# ---------------------------------------------------------------------------
# Score fuzzy textual
# ---------------------------------------------------------------------------

def _fuzzy_score(query_norm: str, candidate: str) -> float:
    """
    SequenceMatcher ratio entre el query normalizado y el nombre de calle
    normalizado. Retorna valor ∈ [0, 1].
    """
    cand_norm = normalize_street_text(candidate)
    if not query_norm or not cand_norm:
        return 0.0
    return SequenceMatcher(None, query_norm, cand_norm).ratio()


# ---------------------------------------------------------------------------
# Búsqueda combinada: embedding + fuzzy
# ---------------------------------------------------------------------------

def search_top_streets(query: str) -> list[tuple[str, float, float, float]]:
    """
    Devuelve los mejores candidatos para el texto `query`.

    Retorna lista de (nombre, emb_sim, fuzzy_sim, score_final) ordenada por
    score_final desc, limitada a TOP_N entradas con score >= COMBINED_THRESHOLD.

    Si el embedding falla, usa solo fuzzy (y viceversa).
    """
    query_norm = normalize_street_text(query)
    if not query_norm:
        return []

    logger.info(f"[ADDRESS] transcript original : '{query}'")
    logger.info(f"[ADDRESS] transcript norm     : '{query_norm}'")

    # ---- 1. Embedding (async-safe: se llama con asyncio.to_thread desde telnyx.py) ----
    emb_results: dict[str, float] = {}   # name → embedding similarity
    cache_key = query_norm

    if cache_key in _embedding_cache:
        logger.info(f"[ADDRESS] embedding cache hit para '{query_norm}'")
        for name, sim in _embedding_cache[cache_key]:
            emb_results[name] = sim
    else:
        embedding = get_embedding(query_norm)
        if embedding:
            try:
                with get_cursor() as cur:
                    cur.execute("""
                        SELECT name, 1 - (embedding <=> %s::vector) AS similarity
                        FROM street_embeddings
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT 20
                    """, (str(embedding), str(embedding)))
                    rows = cur.fetchall()
                    top = [(r["name"], float(r["similarity"])) for r in rows]
                    _embedding_cache[cache_key] = top
                    for name, sim in top:
                        emb_results[name] = sim
                    logger.info(
                        f"[ADDRESS] embedding top3: "
                        + ", ".join(f"{n}={s:.3f}" for n, s in top[:3])
                    )
            except Exception as e:
                logger.warning(f"[ADDRESS] Error embedding DB: {e}")
        else:
            logger.warning("[ADDRESS] get_embedding devolvió None")

    # ---- 2. Fuzzy contra TODOS los nombres ----
    all_streets = _load_all_street_names()
    fuzzy_results: dict[str, float] = {}

    for street in all_streets:
        score = _fuzzy_score(query_norm, street)
        if score >= 0.40:       # poda liviana, no filtramos candidatos buenos
            fuzzy_results[street] = score

    top_fuzzy = sorted(fuzzy_results.items(), key=lambda x: x[1], reverse=True)[:5]
    logger.info(
        f"[ADDRESS] fuzzy top5: "
        + ", ".join(f"{n}={s:.3f}" for n, s in top_fuzzy)
    )

    # ---- 3. Score combinado ----
    all_names = set(emb_results) | set(fuzzy_results)
    scored: list[tuple[str, float, float, float]] = []

    for name in all_names:
        emb_sim   = emb_results.get(name, 0.0)
        fuzzy_sim = fuzzy_results.get(name, 0.0)
        final     = max(emb_sim, fuzzy_sim * FUZZY_WEIGHT)
        if final >= COMBINED_THRESHOLD:
            scored.append((name, emb_sim, fuzzy_sim, final))

    scored.sort(key=lambda x: x[3], reverse=True)
    top = scored[:TOP_N]

    logger.info(
        f"[ADDRESS] candidatos finales: "
        + ", ".join(
            f"{n}(emb={e:.3f}, fuzz={f:.3f}, final={fin:.3f})"
            for n, e, f, fin in top
        )
    )
    return top


def search_top3_streets(query: str) -> list[str]:
    """
    Interfaz compatible con router.py (Twilio) y telnyx.py.
    Devuelve solo los nombres, sin scores.

    SWITCH EMBEDDING ↔ FUZZY:
      - Modo actual  (embeddings OFF): delega en search_streets_fuzzy()
      - Para reactivar embeddings:     cambiar por search_top_streets()
        → return [name for name, *_ in search_top_streets(query)]
    """
    return search_streets_fuzzy(query)


# ---------------------------------------------------------------------------
# Fuzzy-only matching  (embeddings desactivados temporalmente)
#
# Activado como fuente exclusiva para el flujo de voz Telnyx + ElevenLabs.
# NO usa get_embedding() ni la columna vector de street_embeddings.
# Lee únicamente la columna `name` de street_embeddings (ya cacheada).
#
# Para volver a activar embeddings:
#   1. En search_top3_streets(), reemplazar `return search_streets_fuzzy(query)`
#      por `return [name for name, *_ in search_top_streets(query)]`
#   2. Nada más. Esta función puede quedarse sin efectos secundarios.
# ---------------------------------------------------------------------------

# Threshold mínimo de SequenceMatcher.ratio() para considerar un candidato.
# SequenceMatcher devuelve valores en [0, 1].
#
# Elección: 0.55
#   - "saavedra" vs "saavedra"  → 1.00  ✓
#   - "cavera"   vs "saavedra"  → ~0.57 ✓  (error STT típico)
#   - "casilda"  vs "saavedra"  → ~0.50 ✗  (descartado)
#   - "vereda"   vs "saavedra"  → ~0.46 ✗  (descartado, era stop word igual)
#   - "hola"     vs cualquier   → < 0.40 ✗ (no genera falsos positivos)
#   - "pueyrredon" vs "pueyrredon" → 1.00 ✓
#   - "rodriguez"  vs "rodríguez"  → 0.94 ✓ (normalize_street_text quita tilde)
#
#   0.55 es suficientemente alto para evitar que palabras comunes del
#   transcript (artículos, verbos, etc.) activen una calle.
FUZZY_ONLY_THRESHOLD = 0.55

# Cuántos candidatos devolver en modo fuzzy-only.
FUZZY_ONLY_TOP_N = 3

# Tamaño máximo (en tokens) de la ventana deslizante usada para extraer
# el segmento de nombre de calle del transcript completo.
# 3 cubre el 100% de calles de Bahía Blanca después de normalizar
# (ninguna tiene más de 3 tokens significativos tras quitar stop words).
MAX_WINDOW_TOKENS = 3

# Score mínimo que debe alcanzar la mejor ventana para ser aceptada
# como segmento extraído. Por debajo de este valor, se usa el transcript
# normalizado completo y el threshold final (FUZZY_ONLY_THRESHOLD) decide.
#
# Elección: 0.70
#   La ventana deslizante puede producir falsos positivos si acepta tokens
#   comunes con score moderado:
#     "como"    vs "Colón"  → 0.667  → si se acepta, matchea una calle falsa
#     "correcto" vs "Moreno" → 0.571 → ídem
#   Con 0.70 solo se acepta una ventana con coincidencia claramente buena:
#     "saavedra" vs "Saavedra"      → 1.000 ✓  se extrae
#     "rodriguez" vs "Rodríguez"    → 1.000 ✓  se extrae
#     "pueyrredon" vs "Pueyrredón"  → 1.000 ✓  se extrae
#     "hipolito yrigoyen" vs "Hipólito Yrigoyen" → 1.000 ✓  se extrae
#     "como" vs "Colón"             → 0.667 ✗  no se extrae
#     "correcto" vs "Moreno"        → 0.571 ✗  no se extrae
#   Cuando ninguna ventana supera 0.70, el texto completo normalizado va al
#   fuzzy y FUZZY_ONLY_THRESHOLD = 0.55 descarta el caso correctamente.
WINDOW_MIN_SCORE = 0.70


def _collapse_spelling(text: str) -> str:
    """
    Detecta y colapsa deletreo letra a letra.

    Patrones reconocidos:
      "R-O-D-R-I-G-U-E-Z"      → "rodriguez"
      "R O D R I G U E Z"       → "rodriguez"  (letras sueltas separadas por espacio)
      "r o d r i g u e z"       → "rodriguez"

    La heurística es: si el texto (sin tildes, minúsculas) tiene 4 o más tokens
    de una sola letra cada uno, se trata como deletreo.

    Si no parece deletreo, devuelve el texto original sin cambios.
    """
    # Normalizar para análisis: minúsculas, sin tildes, reemplazar guiones por espacio
    cleaned = _strip_accents(text.lower())
    cleaned = re.sub(r'[-–—]', ' ', cleaned)
    tokens = cleaned.split()

    single_letter_count = sum(1 for t in tokens if re.fullmatch(r'[a-z]', t))
    if single_letter_count >= 4 and single_letter_count >= len(tokens) * 0.7:
        # Parece deletreo: colapsar solo las letras individuales en una palabra
        collapsed = "".join(t for t in tokens if re.fullmatch(r'[a-z]', t))
        logger.info(f"[ADDRESS] deletreo detectado: '{text}' → '{collapsed}'")
        return collapsed

    return text


def _extract_address_segment(transcript_norm: str) -> str:
    """
    Extrae el segmento del transcript normalizado que más probablemente
    corresponde a un nombre de calle, usando una ventana deslizante de
    1 a MAX_WINDOW_TOKENS tokens sobre los nombres de la DB.

    Estrategia:
      Para cada ventana de tokens del transcript (largo 1, 2 o 3),
      calculamos el mejor score fuzzy contra las 810 calles.
      Nos quedamos con la ventana que produce el score más alto.

    Si ninguna ventana supera WINDOW_MIN_SCORE, devuelve el transcript
    completo (el matcher aplicará el threshold final igualmente).

    Los tokens numéricos se excluyen de la ventana porque son altura,
    no nombre de calle.
    """
    all_streets = _load_all_street_names()
    if not all_streets:
        return transcript_norm

    tokens = [t for t in transcript_norm.split() if t]
    # Excluir tokens puramente numéricos de la ventana de búsqueda
    text_tokens = [t for t in tokens if not re.fullmatch(r'\d+', t)]

    if not text_tokens:
        return transcript_norm

    # Si hay un solo token no tiene sentido hacer ventana.
    if len(text_tokens) == 1:
        return text_tokens[0]

    best_window = ""
    best_score  = 0.0

    for window_size in range(1, MAX_WINDOW_TOKENS + 1):
        for start in range(len(text_tokens) - window_size + 1):
            window = " ".join(text_tokens[start : start + window_size])
            # Score de esta ventana = mejor fuzzy contra cualquier calle
            for street in all_streets:
                score = _fuzzy_score(window, street)
                if score > best_score:
                    best_score  = score
                    best_window = window

    if best_score >= WINDOW_MIN_SCORE:
        return best_window

    # Ninguna ventana alcanzó el mínimo: devolver todos los text_tokens
    # y dejar que el threshold final (FUZZY_ONLY_THRESHOLD) descarte.
    return " ".join(text_tokens)


def search_streets_fuzzy(query: str) -> list[str]:
    """
    Fuzzy-only street search con extracción de segmento.  NO usa embeddings.

    Pipeline:
      1. Detectar y colapsar deletreo ("R-O-D-R-I-G-U-E-Z" → "rodriguez")
      2. normalize_numbers_in_text  ("mil quinientos" → "1500")
      3. normalize_street_text      (minúsculas, sin tildes, sin stop words,
                                     sin números)
      4. _extract_address_segment   (ventana deslizante 1-3 tokens)
      5. Fuzzy contra 810 calles    (SequenceMatcher, threshold 0.55)
      6. Devolver top FUZZY_ONLY_TOP_N

    Retorna lista de nombres (strings), vacía si nada supera el threshold.

    Logs:
      [ADDRESS] transcript original : ...
      [ADDRESS] texto extraído      : ...
      [ADDRESS] transcript norm     : ...
      [ADDRESS] fuzzy top5          : ...
      [ADDRESS] candidatos finales  : ...
    """
    logger.info(f"[ADDRESS] transcript original : '{query}'")

    # Paso 1: colapsar deletreo antes de cualquier otra normalización
    text = _collapse_spelling(query)

    # Paso 2: convertir números en palabras a dígitos
    text = normalize_numbers_in_text(text)

    # Paso 3: normalización estándar (minúsculas, sin tildes, sin stop words,
    #         sin números — los números los extraemos desde el transcript
    #         original en telnyx.py, no acá)
    query_norm = normalize_street_text(text)

    if not query_norm:
        logger.info("[ADDRESS] texto extraído      : (vacío)")
        logger.info("[ADDRESS] transcript norm     : (vacío)")
        logger.info("[ADDRESS] candidatos finales  : ninguno (query vacío tras normalizar)")
        return []

    # Paso 4: extraer el segmento más probable de nombre de calle
    extracted = _extract_address_segment(query_norm)

    logger.info(f"[ADDRESS] texto extraído      : '{extracted}'")
    logger.info(f"[ADDRESS] transcript norm     : '{query_norm}'")

    all_streets = _load_all_street_names()
    if not all_streets:
        logger.warning("[ADDRESS] candidatos finales  : ninguno (street_names vacío)")
        return []

    # Paso 5: fuzzy contra todas las calles usando el segmento extraído
    scored: list[tuple[str, float]] = []
    for street in all_streets:
        score = _fuzzy_score(extracted, street)
        if score >= FUZZY_ONLY_THRESHOLD:
            scored.append((street, score))

    top5 = sorted(scored, key=lambda x: x[1], reverse=True)[:5]
    logger.info(
        "[ADDRESS] fuzzy top5          : "
        + (", ".join(f"{n}={s:.3f}" for n, s in top5) if top5 else "ninguno")
    )

    top = top5[:FUZZY_ONLY_TOP_N]
    logger.info(
        "[ADDRESS] candidatos finales  : "
        + (", ".join(f"{n}={s:.3f}" for n, s in top) if top else "ninguno")
    )

    return [name for name, _ in top]


# ---------------------------------------------------------------------------
# Detección sí/no
# ---------------------------------------------------------------------------

def is_yes(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["sí", "si", "correcto", "exacto", "eso", "ese", "esa", "ok", "dale", "claro"])


def is_no(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["no", "negativo", "otro", "otra", "distinto"])


# ---------------------------------------------------------------------------
# Números en palabras → dígitos
# ---------------------------------------------------------------------------

_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22,
    "veintidós": 22, "veintitres": 23, "veintitrés": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintiséis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
}
_TENS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
    "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600,
    "setecientos": 700, "ochocientos": 800, "novecientos": 900,
}
_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_HUNDREDS) | {"mil", "y"}


def _words_to_number(words: list[str]) -> int:
    total = 0
    current = 0
    for w in words:
        if w == "y":
            continue
        if w == "mil":
            current = current or 1
            total += current * 1000
            current = 0
        elif w in _HUNDREDS:
            current += _HUNDREDS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w in _UNITS:
            current += _UNITS[w]
    return total + current


def normalize_numbers_in_text(text: str) -> str:
    """
    "avenida alem mil quinientos" → "avenida alem 1500"
    """
    tokens = text.lower().split()
    out_tokens: list[str] = []
    buffer: list[str] = []

    def flush():
        if buffer:
            out_tokens.append(str(_words_to_number(buffer)))
            buffer.clear()

    for tok in tokens:
        clean = re.sub(r'[^\wáéíóúñ]', '', tok)
        if clean in _NUMBER_WORDS:
            buffer.append(clean)
        else:
            flush()
            out_tokens.append(tok)
    flush()
    return " ".join(out_tokens)
