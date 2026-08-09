"""
payment_monitor.py
Monitorea la wallet TRC20 buscando pagos de USDT.
Si encuentra un pago de $10 USDT activa el plan pro del usuario.
"""
import asyncio
import httpx
import logging
from app.core.database import get_cursor

log = logging.getLogger(__name__)

WALLET = "TDPfrfpipHtENAANT2zkgLZNFmZE6MaJRw"
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # USDT TRC20 oficial
REQUIRED_AMOUNT = 10_000_000  # 10 USDT (6 decimales)
TRONGRID_URL = "https://api.trongrid.io/v1/accounts/{}/transactions/trc20"
CHECK_INTERVAL = 300  # 5 minutos


def get_last_checked() -> str | None:
    """Devuelve el fingerprint del último tx procesado."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT value FROM hash_cloud WHERE key = 'last_tx_id'")
            row = cur.fetchone()
            return row["value"] if row else None
    except Exception:
        return None


def save_last_checked(tx_id: str):
    try:
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO hash_cloud (key, value) VALUES ('last_tx_id', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, [tx_id])
    except Exception as e:
        log.error(f"save_last_checked error: {e}")


def activate_plan(user_id: str):
    try:
        with get_cursor() as cur:
            cur.execute("""
                UPDATE memory_users
                SET plan = 'pro', plan_activated_at = NOW()
                WHERE user_id = %s
            """, [user_id])
        log.info(f"Plan pro activado para {user_id}")
    except Exception as e:
        log.error(f"activate_plan error: {e}")


def find_user_by_email(email: str) -> str | None:
    """Busca user_id por email."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT user_id FROM memory_users WHERE email = %s", [email])
            row = cur.fetchone()
            return row["user_id"] if row else None
    except Exception:
        return None


def get_pending_email() -> str | None:
    """Devuelve el email pendiente de pago más reciente."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT email FROM payment_pending
                ORDER BY created_at DESC LIMIT 1
            """)
            row = cur.fetchone()
            return row["email"] if row else None
    except Exception:
        return None


def clear_pending_email(email: str):
    try:
        with get_cursor() as cur:
            cur.execute("DELETE FROM payment_pending WHERE email = %s", [email])
    except Exception as e:
        log.error(f"clear_pending_email error: {e}")


async def check_payments():
    url = TRONGRID_URL.format(WALLET)
    params = {
        "contract_address": USDT_CONTRACT,
        "limit": 20,
        "order_by": "block_timestamp,desc",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url, params=params)
            if res.status_code != 200:
                log.warning(f"TronGrid error: {res.status_code}")
                return
            data = res.json().get("data", [])

        last_tx = get_last_checked()
        new_txs = []

        for tx in data:
            tx_id = tx.get("transaction_id", "")
            if tx_id == last_tx:
                break
            new_txs.append(tx)

        if not new_txs:
            return

        # Guardar el tx más reciente como checkpoint
        save_last_checked(new_txs[0].get("transaction_id", ""))

        for tx in new_txs:
            value = int(tx.get("value", 0))
            if value >= REQUIRED_AMOUNT:
                from_addr = tx.get("from", "")
                log.info(f"Pago detectado: {value/1_000_000} USDT desde {from_addr}")
                email = get_pending_email()
                if email:
                    user_id = find_user_by_email(email)
                    if user_id:
                        activate_plan(user_id)
                        clear_pending_email(email)
                        log.info(f"Plan activado para {email}")
                    else:
                        log.warning(f"Email {email} no encontrado en DB")
                else:
                    log.warning("Pago detectado pero no hay email pendiente")

    except Exception as e:
        log.error(f"check_payments error: {e}")


async def monitor_loop():
    log.info("Payment monitor iniciado.")
    while True:
        await check_payments()
        await asyncio.sleep(CHECK_INTERVAL)
