@echo off
cd /d "%~dp0"
py -3 -m uvicorn vcnr_secure_backend:app --host 127.0.0.1 --port 8030
pause
