@echo off
rem Запуск CQA «с чистого листа» на Windows: стирает базу (cqa.db) и запускает сервер заново.
chcp 65001 >nul
title CQA - Freedom Broker (с чистого листа)
cd /d "%~dp0"
call "%~dp0start-windows.bat" --reset
