@echo off
chcp 65001 >nul
title 资金流向实时看板（D 盘版）
cd /d "%~dp0"

set "PYEXE="

rem ---- 依次寻找可用的 Python ----
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE ( where py >nul 2>nul && set "PYEXE=py -3" )
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "C:\Python313\python.exe" set "PYEXE=C:\Python313\python.exe"
if not defined PYEXE if exist "C:\Users\user\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=C:\Users\user\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PYEXE if exist "C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PYEXE=C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not defined PYEXE (
  echo.
  echo   [错误] 没有找到 Python。
  echo   本程序需要 Python 3.8 或更高版本 ^(无需安装任何额外库^)。
  echo   请到 https://www.python.org/downloads/ 下载安装，
  echo   安装时务必勾选 "Add Python to PATH"，然后重新双击本文件。
  echo.
  pause
  exit /b 1
)

echo.
echo   正在启动 资金流向实时看板 ...
echo   使用解释器：%PYEXE%
echo   数据目录：%~dp0data
echo   浏览器会自动打开；关闭本窗口即停止服务。
echo.
echo   注意：请通过本窗口打开页面，不要直接双击 html 文件 ——
echo         只有这样才能把历史数据写到 D 盘。
echo.

%PYEXE% serve.py
echo.
echo   服务已停止。
pause
