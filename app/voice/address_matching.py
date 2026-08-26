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

from rapidfuzz import fuzz, process as rf_process

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
    # Prefijos de vía pública
    "calle", "avenida", "av", "avda", "bulevar", "boulevard", "pasaje", "pje",
    "ruta", "camino", "vereda", "esquina", "esq", "entre", "y", "la", "el",
    "las", "los", "de", "del", "desde", "hasta", "por",
    # Palabras conversacionales frecuentes en ASR de taxi/remis.
    # Ninguna de estas es ni puede ser nombre de calle en Bahía Blanca.
    # Agregarlas acá hace que normalize_street_text() las descarte antes
    # de cualquier comparación fuzzy, eliminando falsos positivos sin
    # necesidad de tocar thresholds.
    "hola", "como", "estas", "bien", "mal", "bueno", "buenas",
    "quiero", "salir", "salgo", "arrancar", "pasame", "buscar",
    "voy", "hacia", "solicita", "solicito", "viaje", "viajes",
    "correcto", "exacto", "gracias", "ok", "dale", "claro",
    "si", "no", "un", "una", "me", "te", "se", "mi", "su",
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
    Score fuzzy entre el query normalizado y el nombre de calle normalizado.
    Retorna valor ∈ [0, 1].

    Usa fuzz.ratio (comparación carácter a carácter, sin substrings).

    Razón del cambio respecto a WRatio:
      WRatio delega internamente en partial_ratio cuando los strings difieren
      en longitud, lo que acepta cualquier palabra que sea substring fonético
      de un nombre de calle:
        "solicita" → partial_ratio → Solís   = 0.80  (falso positivo)
        "viaje"    → partial_ratio → Rivadavia = 0.67 (falso positivo)
        "si"       → partial_ratio → Alsina   = 1.00  (falso positivo)

      fuzz.ratio penaliza la diferencia de longitud correctamente:
        "solicita" vs "solis"    → ratio = 0.615  (bajo threshold o rechazado por len_ratio)
        "cavera"   vs "saavedra" → ratio = 0.714  (error STT real, aceptado)
        "saavedra" vs "saavedra" → ratio = 1.000  (perfecto)

    RapidFuzz devuelve [0, 100]; dividimos por 100 para mantener la
    interfaz [0, 1] que usan todos los thresholds existentes.
    """
    cand_norm = normalize_street_text(candidate)
    if not query_norm or not cand_norm:
        return 0.0
    return fuzz.ratio(query_norm, cand_norm) / 100.0


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
#   0.50 permite rescatar errores de STT como "cavera" → "Saavedra" (~0.57).
#   El gap mínimo (MIN_SCORE_GAP) actúa como segunda defensa contra falsos positivos.
FUZZY_ONLY_THRESHOLD = 0.50

# Cuántos candidatos devolver en modo fuzzy-only.
FUZZY_ONLY_TOP_N = 3

# Segunda capa anti-falsos positivos: diferencia mínima entre el score del
# candidato #1 y el #2 para aceptar el resultado.
#
# Intuición:
#   - Calle real ("saavedra" → Saavedra 1.00, siguiente ~0.60) → gap 0.40 ✓
#   - Error STT  ("cavera"  → Saavedra ~0.57, siguiente ~0.44) → gap 0.13 ✓
#   - Conversación ("sí correcto", "viaje", etc.) → scores parejos ~0.50-0.52,
#     gap < 0.05 → ✗ rechazado
#
# Con 0.09 se permite rescatar errores STT claros como "cavera"→"Saavedra"
# (gap real ~0.099) y se bloquea conversación (gaps < 0.02).
MIN_SCORE_GAP = 0.09

# Longitud mínima (en caracteres) del segmento extraído para que sea válido
# como candidato a nombre de calle.
# 4 chars: descarta monosílabos ("si", "no") y palabras de 3 letras que
# escaparon de _STOP_WORDS. Las calles reales más cortas de BB tienen 4+
# chars normalizados (Alem=4, Roca=4, Cuyo=4, Ruiz=4).
MIN_SEGMENT_LEN = 4

# Proporción máxima entre la longitud del segmento y la longitud normalizada
# del mejor candidato. Si el segmento es mucho más largo que la calle, la
# coincidencia es espuria: fuzz.ratio castiga la diferencia pero no alcanza
# a bloquearla cuando el score queda entre threshold y el candidato destaca.
#
# Ejemplo: "solicita"(8) vs "solis"(5) → ratio=0.615, len_ratio=1.60 → rechazado
#          "cavera"(6)   vs "saavedra"(8) → ratio=0.714, len_ratio=0.75 → aceptado
#          "saavedra"(8) vs "saavedra"(8) → ratio=1.000, len_ratio=1.00 → aceptado
MAX_LEN_RATIO = 1.5

# Score mínimo para aceptar un candidato único (sin competidores sobre threshold).
# Si solo hay un candidato, exigimos que su score sea muy alto para evitar
# que tokens comunes matcheen una sola calle por coincidencia.
# Ej: "como" → solo Colón con 0.667 → rechazado (< 0.85).
#     "saavedra" → solo Saavedra con 1.000 → aceptado (≥ 0.85).
MIN_SOLO_SCORE = 0.85

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
#   Bajado a 0.50 para permitir extraer segmentos con errores STT fuertes
#   como "cavera" (best score ~0.57 vs "Saavedra").
#   La segunda defensa (MIN_SCORE_GAP) evita que frases de conversación
#   pasen: sus scores son bajos Y parejos entre candidatos.
WINDOW_MIN_SCORE = 0.50


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

    # Normalizar las calles una sola vez para toda la búsqueda de ventanas.
    # normalize_street_text() es idempotente y el resultado se usa solo acá.
    streets_norm = [normalize_street_text(s) for s in all_streets]

    for window_size in range(1, MAX_WINDOW_TOKENS + 1):
        for start in range(len(text_tokens) - window_size + 1):
            window = " ".join(text_tokens[start : start + window_size])
            # scorer=fuzz.ratio (NO WRatio) para la extracción de ventana.
            # Razón: WRatio delega en partial_ratio para strings cortos, lo que
            # infla el score de tokens de 2-3 letras que son substrings de calles
            # (ej: "si" → partial_ratio("si","alsina")=100 → win_score=0.90).
            # fuzz.ratio hace comparación carácter a carácter sin substrings,
            # por lo que tokens cortos reciben score correcto (bajo) y no
            # confunden la extracción de segmento. WRatio sigue usándose en el
            # scoring final (Paso 5) donde el segmento ya tiene longitud adecuada.
            result = rf_process.extractOne(
                window,
                streets_norm,
                scorer=fuzz.ratio,
                score_cutoff=0,
            )
            if result is not None:
                _, score_100, _ = result
                score = score_100 / 100.0
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

    # Detección de patrón conversacional de repetición: "X por X", "X y X", etc.
    # Cuando el transcript normalizado repite la misma palabra clave, es probable
    # que sea conversación ("viaje por viaje", "así así") y no una calle.
    norm_tokens = query_norm.split()
    if len(norm_tokens) >= 2:
        unique_tokens = set(norm_tokens)
        repetition_ratio = 1 - (len(unique_tokens) / len(norm_tokens))
        if repetition_ratio >= 0.5:  # la mitad o más de los tokens se repiten
            logger.info(f"[ADDRESS] mejor candidata     : ninguna")
            logger.info(f"[ADDRESS] score               : -")
            logger.info(
                f"[ADDRESS] decisión final      : RECHAZADO "
                f"(patrón de repetición conversacional detectado, ratio={repetition_ratio:.2f})"
            )
            return []

    # Validación de longitud mínima del segmento
    if len(extracted) < MIN_SEGMENT_LEN:
        logger.info(f"[ADDRESS] mejor candidata     : ninguna")
        logger.info(f"[ADDRESS] score               : -")
        logger.info(
            f"[ADDRESS] decisión final      : RECHAZADO "
            f"(segmento '{extracted}' demasiado corto, < {MIN_SEGMENT_LEN} chars)"
        )
        return []

    # Paso 5: fuzzy contra todas las calles usando el segmento extraído.
    # scorer=fuzz.ratio en todos los casos (sin WRatio ni partial_ratio).
    # Razón: WRatio/partial_ratio inflan el score de palabras conversacionales
    # que son substrings de nombres de calle ("solicita"→Solís, "viaje"→Rivadavia).
    # fuzz.ratio compara carácter a carácter y penaliza diferencia de longitud,
    # lo que mantiene la tolerancia a errores ASR reales ("cavera"→0.714) sin
    # aceptar palabras conversacionales (scores bajos o bloqueados por MAX_LEN_RATIO).
    streets_norm_list = [normalize_street_text(s) for s in all_streets]
    rf_results = rf_process.extract(
        extracted,
        streets_norm_list,
        scorer=fuzz.ratio,
        limit=len(all_streets),
        score_cutoff=0,
    )
    # rf_results: list of (matched_norm, score_100, index)
    # Reconstituir nombres originales (no normalizados) para devolverlos al llamador.
    all_scored: list[tuple[str, float, str]] = [
        (all_streets[idx], score_100 / 100.0, streets_norm_list[idx])
        for _, score_100, idx in rf_results
    ]
    # Ya viene ordenado por score desc desde RapidFuzz.

    # Log detallado top5.
    top5_detail: list[str] = []
    for street_name, sc, cn in all_scored[:5]:
        top5_detail.append(f"{street_name}(ratio={sc:.3f})")
    logger.info(
        "[ADDRESS] candidatos RapidFuzz : "
        + (", ".join(top5_detail) if top5_detail else "ninguno")
    )

    # Filtrar por threshold mínimo
    scored = [(n, s, cn) for n, s, cn in all_scored if s >= FUZZY_ONLY_THRESHOLD]

    if not scored:
        logger.info(f"[ADDRESS] mejor candidata     : ninguna (threshold {FUZZY_ONLY_THRESHOLD})")
        logger.info("[ADDRESS] score               : -")
        logger.info("[ADDRESS] decisión final      : RECHAZADO (sin candidatos sobre threshold)")
        return []

    best_name, best_score, best_cn = scored[0]

    # Validación de proporción de longitud: si el segmento es mucho más largo
    # que el nombre de calle normalizado, la coincidencia es espuria.
    # "solicita"(8) vs "solis"(5) → len_ratio=1.60 > 1.5 → rechazado.
    # "cavera"(6)   vs "saavedra"(8) → len_ratio=0.75 → aceptado.
    len_ratio = len(extracted) / max(len(best_cn), 1)
    if len_ratio > MAX_LEN_RATIO:
        logger.info(f"[ADDRESS] mejor candidata     : '{best_name}'")
        logger.info(f"[ADDRESS] score               : {best_score:.3f}")
        logger.info(
            f"[ADDRESS] decisión final      : RECHAZADO "
            f"(len_ratio {len_ratio:.2f} > MAX_LEN_RATIO {MAX_LEN_RATIO}: "
            f"'{extracted}'→'{best_cn}')"
        )
        return []

    scored2 = [(n, s) for n, s, _ in scored]
    second_score = scored2[1][1] if len(scored2) > 1 else 0.0
    gap = best_score - second_score

    # Validación de candidato único
    if len(scored2) == 1 and best_score < MIN_SOLO_SCORE:
        logger.info(f"[ADDRESS] mejor candidata     : '{best_name}'")
        logger.info(f"[ADDRESS] score               : {best_score:.3f} (único candidato)")
        logger.info(
            f"[ADDRESS] decisión final      : RECHAZADO "
            f"(candidato único con score {best_score:.3f} < MIN_SOLO_SCORE {MIN_SOLO_SCORE})"
        )
        return []

    logger.info(f"[ADDRESS] mejor candidata     : '{best_name}'")
    logger.info(f"[ADDRESS] score               : {best_score:.3f} (gap vs #2: {gap:.3f})")

    # El candidato #1 debe destacarse suficientemente del #2
    if gap < MIN_SCORE_GAP:
        logger.info(
            f"[ADDRESS] decisión final      : RECHAZADO "
            f"(gap {gap:.3f} < MIN_SCORE_GAP {MIN_SCORE_GAP} → posible conversación, no calle)"
        )
        return []

    top = scored2[:FUZZY_ONLY_TOP_N]
    logger.info(
        "[ADDRESS] decisión final      : ACEPTADO → "
        + ", ".join(f"{n}={s:.3f}" for n, s in top)
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
