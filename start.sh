#!/bin/bash
set -e

echo "→ Creando entorno virtual..."
python -m venv .venv

echo "→ Activando entorno virtual..."
source .venv/bin/activate

echo "→ Instalando dependencias..."
pip install -r requirements.txt

echo "→ Iniciando HASH Cloud..."
uvicorn app.main:app --reload
