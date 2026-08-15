@echo off
setlocal
cd /d D:\ssq_evo
rem Wait for Docker engine to be ready (up to ~60s), then start the engine container.
rem Runs as a SYSTEM "At startup" scheduled task so the engine comes up after a reboot
rem without requiring login. Docker Desktop should also be set to "Start at login".
set "LOG=D:\ssq_evo_data\docker_autostart.log"
for /l %%i in (1,1,12) do (
    docker info >nul 2>&1 && goto :run
    timeout /t 5 >nul
)
echo %date% %time% Docker engine not ready - skip autostart >> "%LOG%"
exit /b 1
:run
docker compose up -d >> "%LOG%" 2>&1
echo %date% %time% docker compose up -d done >> "%LOG%"
endlocal
