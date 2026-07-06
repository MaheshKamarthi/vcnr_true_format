@echo off
cd /d "%~dp0"

set SSL_CERT_FILE=
set REQUESTS_CA_BUNDLE=
set CURL_CA_BUNDLE=
set PIP_CERT=

py -3 -m pip install pyinstaller --trusted-host pypi.org --trusted-host files.pythonhosted.org
py -3 -m PyInstaller --onefile --noconsole --name VCNR_True_Converter vcnr_true_converter_gui.py

pause
