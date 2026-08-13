# -*- coding: utf-8 -*-
"""
daemon_loop.py —— 7x24 常驻循环（配合 nssm 注册为 Windows 服务）
每 schedule_hours 小时跑一轮 run_cycle，断网时自动跳过抓取继续用本地数据。
启动：python daemon_loop.py
"""
import os, sys, time, subprocess, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def load_cfg():
    try:
        return json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    except Exception:
        return {"schedule_hours": 6}

def main():
    cfg = load_cfg()
    hours = float(cfg.get("schedule_hours", 6))
    py = sys.executable
    print(f"[daemon] 常驻启动，周期 {hours}h，PID={os.getpid()}")
    while True:
        try:
            subprocess.run([py, "-u", os.path.join(HERE, "run_cycle.py")],
                           cwd=HERE, check=False)
        except Exception as e:
            print("[daemon] cycle error:", e)
        print(f"[daemon] 下一轮在 {hours}h 后", flush=True)
        time.sleep(hours * 3600)

if __name__ == "__main__":
    main()
