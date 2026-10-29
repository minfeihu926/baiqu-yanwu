@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === push to GitHub ===
echo user: minfeihu926
echo pass: Personal Access Token (scopes: repo, workflow)
echo.
git -c credential.helper= push -u origin main
if errorlevel 1 goto fail
git -c credential.helper= push origin v1.0.1
if errorlevel 1 goto fail
echo.
echo Done. Tag v1.0.1 triggers GitHub Actions build + release:
echo   https://github.com/minfeihu926/baiqu-yanwu/actions
goto end
:fail
echo.
echo Push failed: check the token scopes (repo + workflow) and the repo name.
:end
pause
