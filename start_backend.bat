@echo off
title BACKEND - 127.0.0.1:8787
cd /d "%~dp0backend"
call .venv\Scripts\activate
uvicorn main:app --host 127.0.0.1 --port 8787
pause
