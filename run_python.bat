@echo off
chcp 65001 >nul
python "%~1" %*
exit /b %errorlevel%
