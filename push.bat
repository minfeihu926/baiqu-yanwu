@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 推送到 GitHub ===
echo 用户名: minfeihu926
echo 密码  : GitHub Personal Access Token (需要 repo + workflow 权限)
echo.
git -c credential.helper= push -u origin main
if errorlevel 1 goto fail
git -c credential.helper= push origin v1.0.1
if errorlevel 1 goto fail
echo.
echo 推送完成。tag v1.0.1 会触发 Actions 自动构建并创建 Release:
echo   https://github.com/minfeihu926/baiqu-yanwu/actions
goto end
:fail
echo.
echo 推送失败: 检查 token 权限(repo + workflow), 或仓库名是否已改成 baiqu-yanwu
:end
pause
