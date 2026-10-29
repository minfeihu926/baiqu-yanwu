@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] install dependencies ...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo [2/3] build onefile exe ...
python -m PyInstaller --noconfirm --clean baiqu_yanwu.spec
if errorlevel 1 goto fail

echo [3/3] done: dist\baiqu_yanwu.exe
pause
exit /b 0

:fail
echo Build failed, check the errors above.
pause
exit /b 1
