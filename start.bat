@echo off
chcp 65001 >nul
REM 社区机器人一键启动（Windows）
REM 用法：双击 start.bat

cd /d "%~dp0"

echo === 社区机器人一键启动 ===
echo.

REM 1. 检查 Python（兼容 python / py 启动器）
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [失败] 没找到 Python，请先安装（见 docs\安装指南.md）
        echo        安装时记得勾选 "Add python.exe to PATH"
        pause
        exit /b 1
    ) else (
        set PYCMD=py -3
    )
) else (
    set PYCMD=python
)
echo [OK] Python 已找到

REM 2. 安装依赖
echo.
echo 正在安装依赖...
%PYCMD% -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [失败] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)
echo [OK] 依赖已安装

REM 3. 检查配置文件
if not exist "config\config.yaml" (
    echo.
    echo 没找到 config\config.yaml，正在从示例复制...
    copy "config\config.example.yaml" "config\config.yaml" >nul
    echo [OK] 已复制，可先用 Web 界面配置
) else (
    echo [OK] 配置文件已存在
)

REM 4. 管理密码
if not defined WEB_PASSWORD (
    echo.
    set /p WEB_PASSWORD=请设置 Web 管理密码（登录 http://127.0.0.1:52323 用）:
    if not defined WEB_PASSWORD (
        echo [失败] 密码不能为空
        pause
        exit /b 1
    )
)
echo [OK] 管理密码已设置

REM 5. 启动（开两个窗口：守护进程 + Web）
echo.
echo 正在启动...
if not exist logs mkdir logs
if not exist data mkdir data

start "社区机器人-守护进程" %PYCMD% -m src.main
echo [OK] 守护进程已在新窗口启动

echo Web 地址：http://127.0.0.1:52323
echo 按 Ctrl+C 可停止 Web（守护进程在另一个窗口，关掉那个窗口即停）
echo.
%PYCMD% -m src.web.app
pause
