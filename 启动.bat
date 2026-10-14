@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
REM %~dp0 自带尾部反斜杠。直接拼进 cd 命令会让 cmd 把结尾反斜杠当转义符，
REM 后面的连接符被吞掉、子命令失败 —— 表现就是双击后窗口一闪而过。下面这行去掉它。
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

title 热量记录 - 智能减肥助手

echo.
echo  ============================================
echo    热量记录 - 智能减肥助手
echo  ============================================
echo.

REM ================= 端口检测 =================
REM 避免重复启动导致 10048 端口占用。
set BUSY=
for /f "tokens=4,5" %%a in ('netstat -ano ^| findstr "LISTENING"') do (
    echo %%a | findstr ":8000" >nul && set BUSY=1
    echo %%a | findstr ":5173" >nul && set BUSY=1
)
if defined BUSY (
    echo  [!] 端口 8000 或 5173 已被占用
    echo.
    echo      服务可能已经在运行，直接打开：
    echo        http://localhost:5173
    echo.
    echo      如需强制重启，双击「停止服务.bat」后再运行本脚本。
    echo.
    pause
    exit /b 1
)

REM ================= 环境自检 =================
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo  [X] 找不到虚拟环境 .venv
    echo      检查路径: %ROOT%\.venv\Scripts\python.exe
    pause
    exit /b 1
)
if not exist "%ROOT%\.env" (
    echo  [!] 未找到 .env，智能对话不可用（记账与图表正常）
)

REM node 可能在多个位置，挨个找
set NODE_DIR=
if exist "C:\Program Files\nodejs\npm.cmd" set NODE_DIR=C:\Program Files\nodejs
if not defined NODE_DIR (
    for %%i in (npm.cmd) do set NODE_DIR=%%~dpi
)
if not defined NODE_DIR (
    echo  [X] 未找到 Node.js / npm
    echo      请安装 Node.js 18 及以上: https://nodejs.org
    pause
    exit /b 1
)
set "PATH=%NODE_DIR%;%PATH%"

if not exist "%ROOT%\frontend\node_modules" (
    echo  [!] 前端依赖未安装，正在安装（首次约 1-2 分钟）...
    pushd "%ROOT%\frontend"
    call npm install
    popd
)

REM ================= 启动后端 =================
echo  [1/2] 启动后端 http://127.0.0.1:8000 ...
start "热量助手-后端" /D "%ROOT%" cmd /k ".venv\Scripts\python.exe -m app.main"

REM 等后端就绪再起前端，否则前端代理会先报连接失败
echo      等待后端就绪...
timeout /t 5 /nobreak >nul

REM ================= 启动前端 =================
echo  [2/2] 启动前端 http://localhost:5173 ...
start "热量助手-前端" /D "%ROOT%\frontend" cmd /k "npm run dev"

timeout /t 6 /nobreak >nul
start "" http://localhost:5173

echo.
echo  ============================================
echo    启动完成
echo.
echo    页面:      http://localhost:5173
echo    接口文档:  http://localhost:8000/docs
echo.
echo    停止服务:  关闭新开的两个窗口
echo              或双击「停止服务.bat」
echo  ============================================
echo.
timeout /t 10 /nobreak >nul
exit /b 0
