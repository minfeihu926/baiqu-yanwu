@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import numpy, PIL" 2>nul
if errorlevel 1 (
    echo [setup] installing numpy pillow ...
    python -m pip install numpy pillow
)
python baiqu_yanwu.py --check --dump "%~dp0shot.png"
echo.
echo 已保存 shot.png, 把它发给我即可排查识别问题。
pause
