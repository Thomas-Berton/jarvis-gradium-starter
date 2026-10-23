@echo off
REM Lance Jarvis sous Windows : double-clic ou "start.bat" dans un terminal.
cd /d "%~dp0"

if not exist .venv (
  echo Creation de l'environnement Python...
  py -3 -m venv .venv 2>nul || python -m venv .venv || (echo Python 3.10+ introuvable : https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Mets ta cle Gradium dans le fichier .env ^(GRADIUM_API_KEY=...^) puis relance start.bat
  notepad .env
  exit /b 0
)

python main.py
pause
