"""
app/job/service.py — HASH JOB
Lógica: CV → embeddings, búsqueda laboral con LLM, auto-apply.
"""

import io
import json
import re
import logging
from app.memory.repository import (
    user_exists,
    create_user,
    get_or_create_document,
    add_row,
    get_documents_with_rows,
)
from app.llm.factory import get_llm_provider

log = logging.getLogger(__name__)

CV_DOCUMENT_KEY = "job_cv"


# ── CV ─────────────────────────────────────────────────────────────────────

def has_cv(user_id: str) -> bool:
    if not user_exists(user_id):
        return False
    docs = get_documents_with_rows(user_id)
    return any(d["key"] == CV_DOCUMENT_KEY for d in docs)


def process_cv(user_id: str, pdf_bytes: bytes) -> str:
    """Extrae texto del PDF y lo guarda con embeddings bajo 'job_cv'."""
    text = _extract_pdf_text(pdf_bytes)
    if not text.strip():
        raise ValueError("No se pudo extraer texto del PDF.")

    if not user_exists(user_id):
        create_user(user_id)

    document_id, _ = get_or_create_document(
        user_id,
        CV_DOCUMENT_KEY,
        "CV",
        "Currículum vitae del usuario",
    )

    lines = text.strip().splitlines()
    chunk_size = 800
    chunks = [lines[i:i + chunk_size] for i in range(0, len(lines), chunk_size)]

    for chunk in chunks:
        chunk_text = "\n".join(chunk).strip()
        if chunk_text:
            add_row(document_id, {"message": chunk_text}, with_embedding=True)

    return f"CV procesado correctamente ({len(chunks)} bloque(s))."


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        log.error("Error extrayendo texto del PDF: %s", e)
        raise ValueError("No se pudo leer el PDF.") from e


# ── Búsqueda ───────────────────────────────────────────────────────────────

def search_jobs(user_id: str, query: str = "") -> list[dict]:
    """Scrapea Google Jobs Argentina y rankea con LLM según el CV."""
    cv_text = _get_cv_text(user_id)
    if not cv_text:
        raise ValueError("No se encontró CV para este usuario.")

    search_term = query.strip() if query.strip() else _extract_job_title(cv_text)
    raw_jobs = _scrape_google_jobs(search_term)

    if not raw_jobs:
        raise ValueError("No se encontraron ofertas. Intentá con otra búsqueda.")

    jobs_summary = "\n".join(
        f'{i+1}. {j["title"]} — {j["company"]} | {j["location"]}'
        for i, j in enumerate(raw_jobs)
    )
    prompt = f"""CV:
{cv_text[:3000]}

Ofertas:
{jobs_summary}

Asigná compatibilidad (50-99) a cada oferta según el CV.
Respondé SOLO con array JSON, sin markdown.
Formato: [{{"index": 1, "compatibility": 85}}, ...]"""

    try:
        llm = get_llm_provider("gemini")
        raw = llm.generate([{"role": "user", "content": prompt}])
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        scores = {item["index"]: item["compatibility"] for item in json.loads(clean)}
    except Exception as e:
        log.error("Error rankeando con LLM: %s", e)
        scores = {i+1: 70 for i in range(len(raw_jobs))}

    for i, j in enumerate(raw_jobs):
        j["compatibility"] = scores.get(i+1, 70)

    raw_jobs.sort(key=lambda x: x["compatibility"], reverse=True)
    return raw_jobs[:12]

def _extract_job_title(cv_text: str) -> str:
    """Extrae el título profesional del CV para usarlo como query."""
    try:
        llm = get_llm_provider("gemini")
        raw = llm.generate([{"role": "user", "content": f"Del siguiente CV, extraé únicamente el título profesional principal en inglés (ej: 'software engineer', 'data analyst'). Solo el título, sin explicaciones.\n\n{cv_text[:1000]}"}])
        return raw.strip().split("\n")[0][:60]
    except Exception:
        return "developer"


def _get_cv_text(user_id: str) -> str:
    docs = get_documents_with_rows(user_id)
    cv_doc = next((d for d in docs if d["key"] == CV_DOCUMENT_KEY), None)
    if not cv_doc:
        return ""
    return "\n".join(row.get("message", "") for row in cv_doc.get("rows", []))


def _parse_jobs(raw: str) -> list[dict]:
    clean = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        jobs = json.loads(clean)
        if not isinstance(jobs, list):
            raise ValueError("El LLM no devolvió una lista.")
        return [
            {
                "company":       str(j.get("company", "")).strip(),
                "title":         str(j.get("title", "")).strip(),
                "location":      str(j.get("location", "")).strip(),
                "mode":          str(j.get("mode", "Remoto")).strip(),
                "compatibility": int(j.get("compatibility", 70)),
                "url":           str(j.get("url", "")).strip(),
            }
            for j in jobs
        ]
    except Exception as e:
        log.error("No se pudo parsear la respuesta del LLM: %s\nRaw: %s", e, raw[:500])
        raise ValueError("El LLM devolvió un formato inesperado.") from e


# ── Auto-apply ─────────────────────────────────────────────────────────────

def apply_to_job(user_id: str, job: dict) -> str:
    """Genera el email de postulación y lo guarda en memoria."""
    cv_text = _get_cv_text(user_id)
    if not cv_text:
        raise ValueError("No se encontró CV para este usuario.")

    prompt = f"""Redactá un email de postulación profesional en español para el siguiente puesto:
- Empresa: {job['company']}
- Puesto: {job['title']}
- Modalidad: {job.get('mode', '')}

Basate en este CV:
{cv_text[:2000]}

El email debe ser conciso, profesional y personalizado. Incluí asunto y cuerpo."""

    llm = get_llm_provider("gemini")
    email_body = llm.generate([{"role": "user", "content": prompt}])

    if not user_exists(user_id):
        create_user(user_id)
    doc_id, _ = get_or_create_document(
        user_id,
        "job_applications",
        "Postulaciones",
        "Historial de postulaciones enviadas",
    )
    add_row(doc_id, {
        "company": job["company"],
        "title":   job["title"],
        "email":   email_body,
    })

    # TODO: integrar envío real con EmailJS / SMTP cuando el usuario configure credenciales
    return f"Postulación a {job['company']} registrada correctamente."


def _scrape_google_jobs(search_term: str) -> list[dict]:
    """Busca ofertas de trabajo usando Google Custom Search API."""
    import httpx
    import os

    api_key = os.environ.get("GOOGLE_API_KEY")
    cx      = os.environ.get("GOOGLE_SEARCH_CX")

    if not api_key or not cx:
        log.error("Faltan variables GOOGLE_API_KEY o GOOGLE_SEARCH_CX")
        return _scrape_computrabajo(search_term)

    query  = f"{search_term} empleos Argentina"
    url    = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx":  cx,
        "q":   query,
        "num": 10,
        "gl":  "ar",
        "hl":  "es",
    }

    jobs = []
    try:
        resp = httpx.get(url, params=params, timeout=20)
        log.info("Custom Search API status: %s", resp.status_code)
        data = resp.json()

        if "error" in data:
            log.error("Error de API: %s", data["error"])
            return _scrape_computrabajo(search_term)

        items = data.get("items", [])
        log.info("Resultados recibidos: %d", len(items))

        for item in items:
            title   = item.get("title", "")
            snippet = item.get("snippet", "")
            link    = item.get("link", "")
            domain  = item.get("displayLink", "")

            company = domain.replace("www.", "").split(".")[0].capitalize()

            jobs.append({
                "title":    title,
                "company":  company,
                "location": "Argentina",
                "mode":     "Remoto" if "remoto" in snippet.lower() or "remote" in snippet.lower() else "Presencial",
                "url":      link,
                "snippet":  snippet,
            })

        log.info("Jobs extraídos: %d", len(jobs))

    except Exception as e:
        log.error("Error con Custom Search API: %s", e)

    if not jobs:
        log.info("Sin resultados, cayendo a Computrabajo")
        jobs = _scrape_computrabajo(search_term)

    return jobs


def _scrape_computrabajo(search_term: str) -> list[dict]:
    """Fallback: scrapea Computrabajo Argentina."""
    import httpx
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "es-AR,es;q=0.9",
    }
    slug = search_term.lower().replace(" ", "-")
    url  = f"https://ar.computrabajo.com/trabajo-de-{slug}"

    jobs = []
    try:
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")

        for card in soup.select("article.box_offer, div[class*='offer']")[:20]:
            title_el    = card.select_one("h2, h3, [class*='title']")
            company_el  = card.select_one("[class*='company'], [class*='empresa']")
            location_el = card.select_one("[class*='location'], [class*='ciudad']")
            link_el     = card.select_one("a[href]")

            title    = title_el.get_text(strip=True)    if title_el    else ""
            company  = company_el.get_text(strip=True)  if company_el  else ""
            location = location_el.get_text(strip=True) if location_el else "Argentina"
            href     = link_el["href"]                  if link_el     else ""
            full_url = f"https://ar.computrabajo.com{href}" if href.startswith("/") else href

            if title:
                jobs.append({
                    "title":    title,
                    "company":  company or "Empresa",
                    "location": location,
                    "mode":     "Remoto" if "remoto" in location.lower() else "Presencial",
                    "url":      full_url,
                })
    except Exception as e:
        log.error("Error scrapeando Computrabajo: %s", e)

    return jobs
