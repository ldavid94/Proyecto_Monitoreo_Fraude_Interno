@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo La demo aun no esta instalada.
  echo Ejecute primero INSTALAR_DEMO.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8000
echo ====================================================
echo DEMO V2 - Monitoreo de Fraude Interno
echo URL: http://127.0.0.1:8000
echo Para cerrar presione CTRL+C
echo ====================================================
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
