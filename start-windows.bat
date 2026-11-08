@echo off
rem Запуск CQA на Windows: двойной клик по этому файлу. Остановить — закрыть окно.
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
title CQA - Freedom Broker
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo [CQA] Python не найден. Установите Python 3.11+ с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^).
  pause
  exit /b 1
)
%PYEXE% tools\run.py %*
if errorlevel 1 pause
