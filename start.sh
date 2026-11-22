#!/bin/bash
set -e

echo "→ Creando entorno virtual..."
python -m venv .venv

echo "→ Activando entorno virtual..."
source .venv/bin/activate

echo "→ Instalando dependencias..."
pip install -r requirements.txt

echo "→ Iniciando HASH Cloud..."
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
