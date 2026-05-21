@echo off
chcp 65001 >nul
title LookUFace

echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 没找到 Python，请先安装 Python。
    pause
    exit /b
)

echo [2/5] 准备虚拟环境...
if not exist "venv\" (
    echo 正在创建虚拟环境...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo [3/5] 安装依赖...
pip install -r requirements.txt -q
echo 依赖安装完成。

echo [4/5] 检查端口冲突...
for /f "tokens=5" %%i in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo 发现端口 8000 被进程 %%i 占用，正在关闭...
    taskkill /f /pid %%i >nul 2>&1
    timeout /t 1 /nobreak >nul
)

echo [5/5] 启动服务...
echo.
echo ============================================
echo  服务启动中，稍后会自动打开浏览器...
echo ============================================

python -m app.main

echo.
echo 服务已停止。
pause
\r\r