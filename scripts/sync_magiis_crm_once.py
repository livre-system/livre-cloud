"""Ejecuta una reconciliación completa MAGIIS Web -> CRM local.

Las credenciales y DATABASE_URL se leen del entorno; no se imprimen secretos.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.getenv("DATABASE_URL") and Path("/tmp/railway-vars.json").exists():
    variables = json.loads(Path("/tmp/railway-vars.json").read_text())
    os.environ["DATABASE_URL"] = variables["DATABASE_URL"]

from app.crm.sync import sync_all  # noqa: E402

print(json.dumps(sync_all(), ensure_ascii=False))
