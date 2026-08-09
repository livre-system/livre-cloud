from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import Response
from app.core.jwt import require_auth
from app.memory.service import (
    check_memory_status,
    create_user_memory,
    read_user_memory,
    write_user_memory,
    delete_user_document,
    rename_user_document,
    upload_txt_as_memory,
    export_memory,
)
from pydantic import BaseModel

router = APIRouter(prefix="/memory", tags=["memory"])  # v16

MEMORY_ERRORS = {
    "not_found":     (status.HTTP_404_NOT_FOUND,  "Memoria no encontrada"),
    "unauthorized":  (status.HTTP_401_UNAUTHORIZED, "Acceso revocado."),
    "inaccessible":  (status.HTTP_403_FORBIDDEN,   "Documento no accesible."),
}

def _raise_memory_error(error: str):
    code, detail = MEMORY_ERRORS.get(error, (500, "Error inesperado"))
    raise HTTPException(status_code=code, detail=detail)


@router.get("/status")
def memory_status(user: dict = Depends(require_auth)):
    result = check_memory_status(user["id"])
    return {"user_id": user["id"], **result}


@router.get("")
def memory_read(user: dict = Depends(require_auth)):
    result = read_user_memory(user["id"])
    if result is None:
        _raise_memory_error("not_found")
    if "error" in result:
        _raise_memory_error(result["error"])
    return result


@router.get("/export")
def memory_export(user: dict = Depends(require_auth)):
    text = export_memory(user["id"])
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=memoria_hash.txt"}
    )


class WriteMemoryRequest(BaseModel):
    document: str
    name: str
    description: str
    row: dict

@router.post("")
def memory_write(body: WriteMemoryRequest, user: dict = Depends(require_auth)):
    try:
        return write_user_memory(user["id"], body.document, body.name, body.description, body.row)
    except ValueError as e:
        _raise_memory_error(str(e))


class RenameMemoryRequest(BaseModel):
    name: str

@router.delete("/{key}")
def memory_delete(key: str, user: dict = Depends(require_auth)):
    try:
        found = delete_user_document(user["id"], key)
    except ValueError as e:
        _raise_memory_error(str(e))
    if not found:
        _raise_memory_error("not_found")
    return {"deleted": True, "key": key}


@router.patch("/{key}/rename")
def memory_rename(key: str, body: RenameMemoryRequest, user: dict = Depends(require_auth)):
    try:
        found = rename_user_document(user["id"], key, body.name)
    except ValueError as e:
        _raise_memory_error(str(e))
    if not found:
        _raise_memory_error("not_found")
    return {"renamed": True, "key": key, "new_name": body.name}


@router.post("/upload")
async def upload_memory(
    file: UploadFile = File(...),
    user: dict = Depends(require_auth),
):
    if not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .txt")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="El archivo debe estar en UTF-8")

    return upload_txt_as_memory(user["id"], file.filename, text, chat_id=None)


@router.get("/graph")
async def memory_graph(user: dict = Depends(require_auth)):
    """Parsea la estructura TEMA del txt y genera el grafo en tiempo real sin LLM."""
    import re
    from collections import Counter, defaultdict
    from app.memory.repository import get_documents_with_rows

    STOPWORDS = {
        "de", "la", "el", "en", "y", "a", "que", "los", "las", "un", "una",
        "es", "se", "del", "con", "por", "para", "su", "al", "lo", "le",
        "me", "te", "si", "no", "the", "and", "of", "to", "in", "is", "it",
        "that", "was", "for", "on", "are", "with", "as", "he", "she", "they",
        "este", "esta", "esto", "más", "pero", "como", "hay", "ya", "vez",
        "ser", "unos", "unas", "también", "cuando", "muy", "porque", "sin",
        "entre", "desde", "hasta", "sobre", "sido", "son", "está", "tiene",
        "todo", "toda", "todos", "hacer", "hace", "hice", "tengo", "quiero",
        "voy", "vos", "eso", "esa", "esos", "esas", "algo", "cada", "ahora",
        "bien", "solo", "siempre", "nunca", "nada", "algo", "así", "acá",
        "porque", "aunque", "mientras", "después", "antes", "entonces",
        "donde", "cuando", "quien", "cual", "cuál", "qué", "cómo", "para",
        "fueron", "tenés", "podés", "hacer", "haber", "estar", "había",
        "puedo", "puede", "mismo", "misma", "tipo", "igual", "otra", "otro",
        "decir", "dice", "dije", "quería", "quiere", "saber", "salud",
    }

    documents = get_documents_with_rows(user["id"])
    documents = [d for d in documents if not d["key"].startswith("chat_log")]

    if not documents:
        return {"nodes": [], "edges": []}

    # Unir todo el contenido desencriptado
    full_text = "\n".join(
        row.get("message", "")
        for doc in documents
        for row in doc["rows"]
        if row.get("message")
    )

    # Parsear secciones TEMA: X del txt
    sections: dict[str, str] = {}
    current_tema = None
    current_lines = []
    for line in full_text.splitlines():
        match = re.match(r'^TEMA:\s*(.+)', line.strip())
        if match:
            if current_tema:
                sections[current_tema] = " ".join(current_lines)
            current_tema = match.group(1).strip().upper()
            current_lines = []
        else:
            if current_tema:
                current_lines.append(line.strip())
    if current_tema:
        sections[current_tema] = " ".join(current_lines)

    # Si no hay estructura TEMA, usar los documentos como nodos principales
    if not sections:
        sections = {
            doc["name"].upper(): " ".join(
                row.get("message", "") for row in doc["rows"] if row.get("message")
            )
            for doc in documents
        }

    FIRMA_PATTERNS = [
        r"No conf[ií]o en que sea[^.]*\.",
        r"No me como los mocos[^.]*\.",
        r"Motor potente[^.]*\.",
        r"memoria viva rebelde[^.]*\.",
        r"Se termin[oó] el teatro[^.]*\.",
        r"Soy esto ahora[^.]*\.",
        r"Sigo rompiendo[^.]*\.",
    ]
    NOISE_WORDS = {"mocos", "motor", "rebelde", "tesla", "teatro", "glitch", "hdp", "orto", "posta", "potente", "rompiendo", "viva"}

    def clean_text(text: str) -> str:
        for pat in FIRMA_PATTERNS:
            text = re.sub(pat, "", text, flags=re.IGNORECASE)
        return text

    def extract_concepts(text: str, top_n: int = 8) -> list[str]:
        text = clean_text(text)
        words = re.findall(r'\b[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]{3,}\b', text.lower())
        words = [w for w in words if w not in STOPWORDS]
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        bigrams = [bg for bg in bigrams if not any(n in bg for n in NOISE_WORDS)]
        top_bigrams = [bg for bg, c in Counter(bigrams).most_common(top_n * 2) if c >= 2]
        selected = top_bigrams[:top_n]
        if len(selected) < 4:
            for w, _ in Counter(words).most_common(top_n):
                if w not in " ".join(selected) and len(selected) < top_n:
                    selected.append(w)
        return selected[:top_n]

    # Conceptos por tema
    tema_keywords: dict[str, list[str]] = {
        tema: extract_concepts(texto)
        for tema, texto in sections.items()
    }

    # Detectar conceptos que aparecen en múltiples temas
    keyword_to_temas: dict[str, list[str]] = defaultdict(list)
    for tema, kws in tema_keywords.items():
        for kw in kws:
            keyword_to_temas[kw].append(tema)

    shared_concepts = {kw: temas for kw, temas in keyword_to_temas.items() if len(temas) >= 2}

    # Construir grafo
    nodes = [{"id": "cerebro", "label": "Cerebro", "main": True}]
    edges = []
    node_ids = {"cerebro"}

    # Nodos de TEMA — van al anillo del medio (sin main ni sub)
    for tema in sections:
        tid = f"tema_{tema}"
        nodes.append({"id": tid, "label": tema.capitalize()})
        edges.append({"from": "cerebro", "to": tid})
        node_ids.add(tid)

    # Subnodos exclusivos de cada tema (no compartidos)
    for tema, kws in tema_keywords.items():
        tid = f"tema_{tema}"
        for kw in kws:
            if kw not in shared_concepts:
                kid = f"kw_{kw}"
                if kid not in node_ids:
                    nodes.append({"id": kid, "label": kw, "sub": True})
                    node_ids.add(kid)
                edges.append({"from": tid, "to": kid})

    # Nodos compartidos — conectados a todos los temas donde aparecen
    for kw, temas in shared_concepts.items():
        kid = f"kw_{kw}"
        if kid not in node_ids:
            nodes.append({"id": kid, "label": kw, "sub": True, "shared": True})
            node_ids.add(kid)
        for tema in temas:
            edges.append({"from": f"tema_{tema}", "to": kid})

    # Conectar temas que comparten 3+ keywords
    tema_list = list(sections.keys())
    for i in range(len(tema_list)):
        for j in range(i + 1, len(tema_list)):
            shared = set(tema_keywords.get(tema_list[i], [])) & set(tema_keywords.get(tema_list[j], []))
            if len(shared) >= 3:
                edges.append({"from": f"tema_{tema_list[i]}", "to": f"tema_{tema_list[j]}"})

    return {"nodes": nodes, "edges": edges}
