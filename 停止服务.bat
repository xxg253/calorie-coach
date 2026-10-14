@echo off
title 停止服务

echo.
echo 正在查找占用 8000 / 5173 端口的进程...
echo.

REM netstat 最后一列是 PID，用 taskkill 结束
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 .*LISTENING"') do (
    echo   停止后端 PID %%p
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173 .*LISTENING"') do (
    echo   停止前端 PID %%p
    taskkill /PID %%p /F >nul 2>&1
)

echo.
echo 完成。如果还有残留，请直接关闭启动时打开的两个命令行窗口。
echo.
timeout /t 3 /nobreak >nul
