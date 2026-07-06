@echo off
title VCNR True Format Setup

set SSL_CERT_FILE=
set REQUESTS_CA_BUNDLE=
set CURL_CA_BUNDLE=
set PIP_CERT=

py -3 -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org

echo.
echo Checking FFmpeg...
ffmpeg -version
if errorlevel 1 (
  echo FFmpeg not found.
  echo Install FFmpeg and add it to PATH.
  echo winget install Gyan.FFmpeg
) else (
  echo FFmpeg found.
)

pause
