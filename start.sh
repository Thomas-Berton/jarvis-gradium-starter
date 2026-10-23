#!/usr/bin/env bash
# Lance Jarvis sous Mac / Linux : ./start.sh
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Création de l'environnement Python..."
  python3 -m venv .venv || { echo "Python 3.10+ introuvable : https://www.python.org/downloads/"; exit 1; }
fi
source .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Mets ta clé Gradium dans le fichier .env (GRADIUM_API_KEY=...) puis relance ./start.sh"
  exit 0
fi

exec python main.py
