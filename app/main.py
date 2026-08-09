from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
from app.core.config import SESSION_SECRET
from app.core.database import init_db, get_cursor
from app.chat.repository import ensure_tables
from app.auth.router import router as auth_router
from app.context.router import router as context_router
from app.memory.router import router as memory_router
from app.compiler.router import router as compiler_router
from app.chat.router import router as chat_router
from app.job.router import router as job_router
from app.commerce.router import router as commerce_router
from app.payment_monitor import monitor_loop
from app.paypal_webhook import router as paypal_router
from app.mercadopago_webhook import router as mp_router
from app.support_bot import run_bot
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def ensure_payment_tables():
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hash_cloud (
                key text PRIMARY KEY,
                value text
            )
        """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_tables()
    ensure_payment_tables()
    task = asyncio.create_task(monitor_loop())
    bot_task = asyncio.create_task(run_bot())
    yield
    task.cancel()
    bot_task.cancel()


app = FastAPI(title="HASH Cloud", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hash-ai.vercel.app",
        "https://hash-phi-weld.vercel.app",
        "https://hash-ia.site",
        "https://www.hash-ia.site",
        "https://hash-job.vercel.app",
        "https://hash-commerce-inky.vercel.app",
        "https://hashcommerce.site",
        "https://www.hashcommerce.site",
    ],
    allow_origin_regex=r"https://.*\.hashcommerce\.site",
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
app.include_router(job_router)
app.include_router(paypal_router)
app.include_router(mp_router)
app.include_router(commerce_router)


# Servir imágenes subidas al volumen
UPLOAD_DIR = Path("/data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

@app.get("/health")
def health():
    return {"status": "ok"}
