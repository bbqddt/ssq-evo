@echo off
setlocal
set DATA_DIR=D:\ssq_evo_data
rem 用 pythonw 避免弹出控制台窗口；进程脱离父 shell，更耐会话结束
"D:\ssq_evo_venv\Scripts\pythonw.exe" -u "D:\ssq_evo\daemon_loop.py" >> "D:\ssq_evo_data\daemon.log" 2>&1
endlocal
