from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
from app.core.config import SESSION_SECRET
from app.core.database import init_db
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
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_migrations()
    ensure_tables()
    await patch_agent_voice()
    yield


app = FastAPI(title="Livre Cloud", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://livre-chatbot.vercel.app",
    ],
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


# Servir imágenes subidas al volumen
UPLOAD_DIR = Path("/data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

@app.get("/health")
def health():
    return {"status": "ok"}
