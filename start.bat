@echo off
chcp 65001 >nul 2>&1

REM ===========================================================================
REM  LookUFace launcher
REM
REM  This file MUST keep CRLF line endings. cmd.exe's batch parser requires
REM  CRLF; with LF-only line endings REM comments and commands get split
REM  byte-by-byte and the script dies immediately (window flashes and closes).
REM
REM  Notes:
REM    * All Python is invoked as venv\Scripts\python.exe directly, never as a
REM      bare "python". On many machines "python" resolves to an unrelated
REM      virtualenv (e.g. an editor or launcher bundle), which would install
REM      dependencies into the wrong interpreter.
REM    * Dependency install is OPTIONAL: if the venv already has everything,
REM      nothing is downloaded. A failed install only produces a warning, so
REM      the script still works offline.
REM    * The occupied-port check only reports; it never kills processes.
REM ===========================================================================

cd /d "%~dp0"
set "VENV_PY=%~dp0venv\Scripts\python.exe"
set "NEED_INSTALL=0"

echo [1/4] Checking Python...
if not exist "%VENV_PY%" goto CREATE_VENV
"%VENV_PY%" --version >nul 2>&1
if errorlevel 1 (
    echo        venv is broken, it will be recreated.
    rmdir /s /q "venv" >nul 2>&1
    goto CREATE_VENV
)
for /f "delims=" %%v in ('"%VENV_PY%" --version 2^>^&1') do echo        venv OK: %%v
goto CHECK_DEPS

:CREATE_VENV
echo        No usable venv found, creating one...
set "BASE_PY=py"
where py >nul 2>&1
if errorlevel 1 set "BASE_PY=python"
%BASE_PY% -m venv venv
if not exist "%VENV_PY%" (
    echo.
    echo [ERROR] Failed to create the virtual environment.
    echo         Install Python 3.11 and make sure "py" or "python" is on PATH.
    echo.
    pause
    exit /b 1
)
set "NEED_INSTALL=1"

:CHECK_DEPS
echo [2/4] Checking dependencies...
"%VENV_PY%" -c "import fastapi, cv2, mediapipe, sqlalchemy, jose, qrcode" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"

if "%NEED_INSTALL%"=="0" (
    echo        All dependencies present.
    goto CHECK_CONFIG
)

echo        Missing dependencies, installing (network required)...
"%VENV_PY%" -m pip install -e . --disable-pip-version-check
"%VENV_PY%" -c "import fastapi, cv2, mediapipe, sqlalchemy, jose, qrcode" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNING] Some dependencies are still missing.
    echo           The service may fail to start. When you have network access, run:
    echo               venv\Scripts\python.exe -m pip install -e .
    echo.
) else (
    echo        Dependencies installed.
)

:CHECK_CONFIG
echo [3/4] Validating configuration...
"%VENV_PY%" -c "from app.config import settings; print('       Config OK ->', settings.APP_NAME, settings.APP_VERSION)"
if errorlevel 1 (
    echo.
    echo [ERROR] Configuration check failed.
    echo         Copy .env.example to .env and set SECRET_KEY.
    echo         Generate one with:
    echo             venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
    echo.
    pause
    exit /b 1
)

echo [4/4] Checking port 8000...
for /f "tokens=5" %%i in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo.
    echo [ERROR] Port 8000 is already in use by PID %%i.
    echo         Close that process, or change PORT in .env and run again.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Starting service...
echo  App: http://localhost:8000/static/idx.html
echo  API: http://localhost:8000/docs
echo ============================================
echo.

"%VENV_PY%" -m app
set "EXITCODE=%errorlevel%"

echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] The service exited with code %EXITCODE%.
    echo         Scroll up to see the traceback.
) else (
    echo Service stopped.
)
echo.
pause
exit /b %EXITCODE%
