"""
app/job/router.py — HASH JOB
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.core.jwt import require_auth
from app.job.service import process_cv, search_jobs, apply_to_job, has_cv

router = APIRouter(prefix="/job", tags=["job"])


@router.post("/cv")
async def upload_cv(
    file: UploadFile = File(...),
    user: dict = Depends(require_auth),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF.")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El archivo supera el límite de 5 MB.")
    try:
        result = process_cv(user_id=user["id"], pdf_bytes=content)
        return {"ok": True, "message": result}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/cv/status")
def cv_status(user: dict = Depends(require_auth)):
    return {"has_cv": has_cv(user["id"])}


@router.post("/search")
def search(body: dict, user: dict = Depends(require_auth)):
    query = body.get("query", "").strip()
    try:
        jobs = search_jobs(user_id=user["id"], query=query)
        return jobs
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error en la búsqueda.")


@router.post("/apply")
def apply(body: dict, user: dict = Depends(require_auth)):
    for field in ["company", "title"]:
        if not body.get(field):
            raise HTTPException(status_code=400, detail=f"Falta el campo '{field}'.")
    try:
        result = apply_to_job(user_id=user["id"], job=body)
        return {"ok": True, "message": result}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error al procesar la postulación.")
