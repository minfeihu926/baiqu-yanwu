@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import numpy, PIL" 2>nul
if errorlevel 1 (
    echo [setup] installing numpy pillow ...
    python -m pip install numpy pillow
    if errorlevel 1 (
        echo [setup] install failed, run: python -m pip install numpy pillow
        pause
        exit /b 1
    )
)
python baiqu_yanwu.py %*
echo.
pause
