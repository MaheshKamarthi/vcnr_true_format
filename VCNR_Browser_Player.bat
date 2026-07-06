@echo off
cd /d "%~dp0"
py -3 vcnr_web_server.py %*
pause
