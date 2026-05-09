@echo off
chcp 65001 >nul
title 启动中...

echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 没找到 Python，请先安装 Python。
    pause
    exit /b
)

echo [2/4] 准备虚拟环境...
if not exist "venv\" (
    echo 正在创建虚拟环境...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo [3/4] 安装依赖...
pip install -r requirements.txt -q
echo 依赖安装完成。

echo [4/4] 启动服务...
echo.
echo ============================================
echo  服务启动中，稍后会自动打开浏览器...
echo ============================================

python -m app.main