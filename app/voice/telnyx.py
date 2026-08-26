"""
test_address_rapidfuzz.py
=========================
Tests standalone de la capa RapidFuzz en address_matching.py.

No requiere DB, embeddings, ni telnyx.
Simula _street_names_cache con calles reales de Bahía Blanca.
"""

import sys
import logging
import unicodedata
import re
from rapidfuzz import fuzz, process as rf_process

# ── Logging bien visible ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── Calles de prueba (subconjunto representativo de calles_BB.json) ─────────
CALLES_BB = [
    "Saavedra", "Cuyo", "Estomba", "Rodríguez", "Pueyrredón",
    "Moreno", "Mitre", "Alem", "Brown", "San Martín",
    "Rivadavia", "Belgrano", "Roca", "Colón", "Alsina",
    "Drago", "Donado", "Darragueira", "Las Heras", "Guido",
    "Zelarrayán", "Vieytes", "Undiano", "Soler", "Ruiz",
    "Newton", "Nación", "Murray", "Lucero", "Lamadrid",
    "Hipólito Yrigoyen", "General Paz", "Entre Ríos",
    "Sixto Laspiur", "White", "Spurr", "España",
    "Brandsen", "Chiclana", "Lavalle",
]

# ── Reimplementación local de las funciones (sin importar el módulo real) ──
_STOP_WORDS = {
    "calle", "avenida", "av", "avda", "bulevar", "boulevard", "pasaje", "pje",
    "ruta", "camino", "vereda", "esquina", "esq", "entre", "y", "la", "el",
    "las", "los", "de", "del", "desde", "hasta", "por",
    "quiero", "salir", "salgo", "arrancar", "pasame", "buscar",
    "ir", "voy", "desde", "hasta", "hacia", "me", "a",
}

def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def normalize_street_text(text: str) -> str:
    t = text.lower()
    t = _strip_accents(t)
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    t = re.sub(r'\b\d+\b', ' ', t)
    tokens = [w for w in t.split() if w not in _STOP_WORDS]
    return " ".join(tokens).strip()

def _fuzzy_score_rapidfuzz(query_norm: str, candidate: str) -> dict:
    """Devuelve los tres scores individuales para diagnóstico."""
    cand_norm = normalize_street_text(candidate)
    if not query_norm or not cand_norm:
        return {"ratio": 0.0, "WRatio": 0.0, "token_set_ratio": 0.0}
    return {
        "ratio":           fuzz.ratio(query_norm, cand_norm) / 100.0,
        "WRatio":          fuzz.WRatio(query_norm, cand_norm) / 100.0,
        "token_set_ratio": fuzz.token_set_ratio(query_norm, cand_norm) / 100.0,
    }

MAX_WINDOW_TOKENS = 3
WINDOW_MIN_SCORE  = 0.50
FUZZY_ONLY_THRESHOLD = 0.50
MIN_SCORE_GAP     = 0.09
MIN_SOLO_SCORE    = 0.85
MIN_SEGMENT_LEN   = 3
FUZZY_ONLY_TOP_N  = 3

streets_norm = [normalize_street_text(s) for s in CALLES_BB]

def _extract_segment(transcript_norm: str) -> tuple[str, float]:
    tokens = [t for t in transcript_norm.split() if t]
    text_tokens = [t for t in tokens if not re.fullmatch(r'\d+', t)]
    if not text_tokens:
        return transcript_norm, 0.0
    if len(text_tokens) == 1:
        return text_tokens[0], 0.0

    best_window = ""
    best_score  = 0.0
    for ws in range(1, MAX_WINDOW_TOKENS + 1):
        for start in range(len(text_tokens) - ws + 1):
            window = " ".join(text_tokens[start : start + ws])
            result = rf_process.extractOne(window, streets_norm, scorer=fuzz.WRatio, score_cutoff=0)
            if result:
                _, sc100, _ = result
                sc = sc100 / 100.0
                if sc > best_score:
                    best_score  = sc
                    best_window = window
    if best_score >= WINDOW_MIN_SCORE:
        return best_window, best_score
    return " ".join(text_tokens), best_score

def match(transcript: str) -> list[str]:
    """Pipeline completo: transcript → candidatos."""
    query_norm = normalize_street_text(transcript)
    if not query_norm:
        return []
    extracted, win_score = _extract_segment(query_norm)

    rf_results = rf_process.extract(
        extracted, streets_norm, scorer=fuzz.WRatio,
        limit=len(CALLES_BB), score_cutoff=0,
    )
    all_scored = [(CALLES_BB[idx], sc100 / 100.0) for _, sc100, idx in rf_results]

    scored = [(n, s) for n, s in all_scored if s >= FUZZY_ONLY_THRESHOLD]
    if not scored:
        return []
    best_name, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    gap = best_score - second_score
    if len(scored) == 1 and best_score < MIN_SOLO_SCORE:
        return []
    if gap < MIN_SCORE_GAP:
        return []
    return [n for n, _ in scored[:FUZZY_ONLY_TOP_N]]

# ── Helpers de test ──────────────────────────────────────────────────────────
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []

def run_test(label: str, transcript: str, expect_hit: bool, expect_street: str | None = None):
    print(f"\n{'─'*70}")
    print(f"  TEST: {label}")
    print(f"  INPUT: '{transcript}'")

    q_norm = normalize_street_text(transcript)
    extracted, win_score = _extract_segment(q_norm)
    print(f"  [ADDRESS] transcript original  : '{transcript}'")
    print(f"  [ADDRESS] segmento detectado   : '{extracted}' (win_score={win_score:.3f})")

    rf_results = rf_process.extract(
        extracted, streets_norm, scorer=fuzz.WRatio,
        limit=5, score_cutoff=0,
    )
    all_top = [(CALLES_BB[idx], sc100 / 100.0) for _, sc100, idx in rf_results]

    detail = []
    for name, _ in all_top:
        scores = _fuzzy_score_rapidfuzz(extracted, name)
        detail.append(
            f"{name}(ratio={scores['ratio']:.3f},"
            f"WRatio={scores['WRatio']:.3f},"
            f"tsr={scores['token_set_ratio']:.3f})"
        )
    print(f"  [ADDRESS] candidatos RapidFuzz : {', '.join(detail)}")

    candidatos = match(transcript)
    best = candidatos[0] if candidatos else None
    best_score = [s for _, s in all_top[:1]]
    print(f"  [ADDRESS] score                : {best_score[0]:.3f}" if best_score else "  [ADDRESS] score                : -")
    if candidatos:
        print(f"  [ADDRESS] decisión final       : ACEPTADO → {', '.join(candidatos)}")
    else:
        print(f"  [ADDRESS] decisión final       : RECHAZADO")

    hit = bool(candidatos)
    ok = (hit == expect_hit)
    if expect_street and hit:
        ok = ok and (expect_street in candidatos)
    icon = PASS if ok else FAIL
    status = "OK" if ok else f"ESPERABA {'HIT:'+expect_street if expect_hit else 'RECHAZO'}, GOT {'HIT:'+str(candidatos) if hit else 'RECHAZO'}"
    print(f"  {icon} {status}")
    results.append(ok)


# ── Tests positivos — nombres directos ──────────────────────────────────────
run_test("nombre directo: Saavedra",    "Saavedra",    True,  "Saavedra")
run_test("nombre directo: Cuyo",        "Cuyo",        True,  "Cuyo")
run_test("nombre directo: Estomba",     "Estomba",     True,  "Estomba")
run_test("nombre directo: Rodríguez",   "Rodríguez",   True,  "Rodríguez")
run_test("nombre directo: Pueyrredón",  "Pueyrredón",  True,  "Pueyrredón")

# ── Tests positivos — frases naturales ──────────────────────────────────────
run_test("frase: quiero arrancar desde calle Saavedra",
         "quiero arrancar desde calle Saavedra", True, "Saavedra")
run_test("frase: salgo desde Cuyo 710",
         "salgo desde Cuyo 710",                True, "Cuyo")
run_test("frase: quiero salir desde Rodríguez 26",
         "quiero salir desde Rodríguez 26",     True, "Rodríguez")
run_test("frase: desde Estomba 710",
         "desde Estomba 710",                   True, "Estomba")
run_test("frase: pasame a buscar por Estomba 710",
         "pasame a buscar por Estomba 710",     True, "Estomba")
run_test("frase: salgo de Pueyrredón 200",
         "salgo de Pueyrredón 200",             True, "Pueyrredón")

# ── Tests negativos — conversación pura ─────────────────────────────────────
run_test("negativo: hola cómo estás",   "hola cómo estás",  False)
run_test("negativo: viaje por viaje",   "viaje por viaje",  False)
run_test("negativo: gracias",           "gracias",          False)
run_test("negativo: sí correcto",       "sí correcto",      False)

# ── Resumen ──────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(results)
print(f"\n{'═'*70}")
print(f"  RESULTADO: {passed}/{total} tests OK")
if passed == total:
    print(f"  \033[92mTODOS LOS TESTS PASARON\033[0m")
else:
    print(f"  \033[91m{total-passed} TESTS FALLARON\033[0m")
print(f"{'═'*70}\n")
sys.exit(0 if passed == total else 1)
