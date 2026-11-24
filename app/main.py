from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
from app.core.config import AUTO_MIGRATE, SESSION_SECRET, validate_runtime_config
from app.core.outbox import run_once
from app.core.database import get_cursor, init_db, run_migrations
from app.chat.repository import ensure_tables
from app.auth.router import router as auth_router
from app.context.router import router as context_router
from app.memory.router import router as memory_router
from app.compiler.router import router as compiler_router
from app.chat.router import router as chat_router
from app.voice.realtime import router as voice_router
from app.voice.elevenlabs import router as voice_eleven_router, patch_agent_voice
from app.voice.gemini_live import router as voice_gemini_router
from app.voice.telnyx import router as voice_telnyx_router   # ← nuevo
from app.voice.elevenlabs_memory import router as elevenlabs_memory_router
from app.voice.elevenlabs_postcall import router as elevenlabs_postcall_router
from app.voice.elevenlabs_debug import router as elevenlabs_debug_router
from app.voice.elevenlabs_trip import router as elevenlabs_trip_router
from app.crm.router import router as crm_router
from app.mobility.router import router as mobility_router
from app.payments.router import router as payments_router, whatsapp_router as whatsapp_payments_router
from app.integrations.meta_webhook import router as meta_webhook_router
from app.integrations.custom_channel import router as custom_channel_router
from app.core.outbox import run_once

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_config()
    init_db()
    if AUTO_MIGRATE:
        run_migrations()
    ensure_tables()
    await patch_agent_voice()
    outbox_task = None
    if os.getenv("OUTBOX_WORKER_ENABLED", "true").lower() == "true":
        outbox_task = asyncio.create_task(_outbox_loop())
    try:
        yield
    finally:
        if outbox_task is not None:
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)


async def _outbox_loop():
    while True:
        try:
            result = await asyncio.to_thread(run_once)
            if result["claimed"]:
                logging.getLogger("uvicorn.error").info("[OUTBOX TRACE] worker_result=%s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("uvicorn.error").exception("[OUTBOX TRACE] worker_loop_error")
        await asyncio.sleep(5)

app = FastAPI(title="Livre Cloud", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://livre-chatbot.vercel.app",
        "https://livre-ia-front.vercel.app",
        "https://livre-chatbot-production.up.railway.app",
        "https://livre-pasajeros.vercel.app",
        "https://livre-conductor-production.up.railway.app",
        "https://livre-pasajeros-production.up.railway.app",
        "https://livre-system.github.io",
    ],
    allow_origin_regex=r"https://livre-conductor(?:-[a-z0-9-]+)?\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=True,
    same_site="none",
)

app.include_router(auth_router)
app.include_router(context_router)
app.include_router(memory_router)
app.include_router(compiler_router)
app.include_router(chat_router)
app.include_router(voice_router)
app.include_router(voice_eleven_router)
app.include_router(voice_gemini_router)
app.include_router(voice_telnyx_router)   # ← nuevo
app.include_router(elevenlabs_memory_router)
app.include_router(elevenlabs_postcall_router)
app.include_router(elevenlabs_debug_router)
app.include_router(elevenlabs_trip_router)
app.include_router(crm_router)
app.include_router(mobility_router)
app.include_router(payments_router)
app.include_router(whatsapp_payments_router)
app.include_router(meta_webhook_router)
app.include_router(custom_channel_router)


# Servir imágenes subidas al volumen
UPLOAD_DIR = Path("/data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    """Indica si la app puede atender tráfico dependiente de PostgreSQL."""
    from fastapi import HTTPException

    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS ok, to_regclass('public.schema_migrations') AS migrations")
            row = cur.fetchone()
            if not row or row["ok"] != 1 or row["migrations"] is None:
                raise RuntimeError("schema_migrations no está disponible")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Base de datos o migraciones no disponibles") from exc
    return {"status": "ready", "database": "ok", "migrations": "ok"}
