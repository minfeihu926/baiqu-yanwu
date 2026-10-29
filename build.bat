@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 安装依赖...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo [2/3] 打包单文件 exe...
python -m PyInstaller --noconfirm --clean baiqu_yanwu.spec
if errorlevel 1 goto fail

echo [3/3] 完成: dist\baiqu_yanwu.exe
pause
exit /b 0

:fail
echo 构建失败，请检查上面的报错。
pause
exit /b 1
