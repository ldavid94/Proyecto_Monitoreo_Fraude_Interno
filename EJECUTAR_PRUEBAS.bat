@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Ejecute primero INSTALAR_DEMO.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
pushd app\motor
python -m pytest -q
popd
pause
