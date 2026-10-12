@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
if errorlevel 1 goto failed
py -3.12 -c "import struct; raise SystemExit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>&1
if not errorlevel 1 (
    py -3.12 "start.py" %*
    if errorlevel 1 goto failed
    popd
    exit /b 0
)
python -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if errorlevel 1 goto missing
python "start.py" %*
if errorlevel 1 goto failed
popd
exit /b 0
:missing
echo Python 3.12 ^(64-bit^) is required.
echo Install it from https://www.python.org/downloads/release/python-31210/
echo Select Python Launcher and Add python.exe to PATH, then run this file again.
:failed
echo Startup did not complete. See the message above.
pause
popd
exit /b 1
