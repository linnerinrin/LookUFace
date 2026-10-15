@echo off
chcp 65001 >nul
title LookUFace

REM ===========================================================================
REM  LookUFace 启动脚本
REM
REM  相对旧版的修复：
REM    1. 旧版先 taskkill 占用 8000 端口的进程 —— 会误杀其他程序，
REM       且对系统进程会失败并留下半死状态。现在只在端口被占时提示并退出。
REM    2. 旧版每次都执行 pip install -r requirements.txt，离线环境下
REM       会长时间重试甚至失败。现在仅在依赖缺失时才安装。
REM    3. 旧版不校验 SECRET_KEY，配置错时在应用内抛一堆 pydantic 错误。
REM       现在先跑一次配置预检，报错更直观。
REM    4. 使用 python -m app 作为统一入口（与 pyproject 的 console script 一致）。
REM ===========================================================================

cd /d "%~dp0"

echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.11 并加入 PATH。
    pause
    exit /b 1
)

echo [2/4] 准备虚拟环境...
if not exist "venv\Scripts\activate.bat" (
    echo 正在创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
)
call venv\Scripts\activate.bat

echo [3/4] 检查依赖...
python -c "import fastapi, cv2, mediapipe, sqlalchemy, jose, qrcode" >nul 2>&1
if %errorlevel% neq 0 (
    echo 依赖不完整，开始安装（需要网络）...
    pip install -e .
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败。若处于离线环境，请在有网络时先执行：pip install -e .
        pause
        exit /b 1
    )
) else (
    echo 依赖已就绪。
)

echo [4/4] 校验配置...
python -c "from app.config import settings; print('配置 OK ->', settings.APP_NAME, settings.APP_VERSION)" 
if %errorlevel% neq 0 (
    echo.
    echo [错误] 配置校验失败。请检查 .env 是否存在且已填写 SECRET_KEY。
    echo        可复制 .env.example 为 .env 后修改。
    echo        生成密钥：python -c "import secrets; print(secrets.token_urlsafe(64))"
    pause
    exit /b 1
)

REM 端口占用检查：只提示，不杀进程
for /f "tokens=5" %%i in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo [警告] 端口 8000 已被进程 %%i 占用。
    echo         请先关闭该进程，或修改 .env 中的 PORT 后重试。
    pause
    exit /b 1
)

echo.
echo ============================================
echo  服务启动中，稍后会自动打开浏览器...
echo  访问地址：http://localhost:8000/static/idx.html
echo  API 文档：http://localhost:8000/docs
echo ============================================
echo.

python -m app

echo.
echo 服务已停止。
pause
