@echo off
setlocal
cd /d "%~dp0"
echo ====================================================
echo  DEMO V2 - MONITOREO DE FRAUDE INTERNO
echo  Instalacion local
echo ====================================================
where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo.
    echo ERROR: Python no esta instalado o no esta en PATH.
    echo Instale Python 3.11 o superior y vuelva a ejecutar este archivo.
    pause
    exit /b 1
  )
  set PY=python
)
if not exist .venv (
  echo Creando entorno virtual...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR instalando dependencias.
  pause
  exit /b 1
)
echo.
echo Instalacion completada.
echo Ejecute INICIAR_DEMO.bat para abrir el sistema.
pause
