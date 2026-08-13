# -*- coding: utf-8 -*-
"""
daemon_loop.py —— 7x24 常驻循环（配合 nssm 注册为 Windows 服务，或计划任务 SYSTEM 开机自启）
每 schedule_hours 小时跑一轮 run_cycle，断网时自动跳过抓取继续用本地数据。
启动：python daemon_loop.py
"""
import os, sys, time, subprocess, json, errno, atexit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA_DIR = os.environ.get("DATA_DIR") or r"D:\ssq_evo_data"
LOCK = os.path.join(DATA_DIR, "daemon.lock")

# 跨平台 PID 存活检测
def _pid_alive(pid):
    if sys.platform == "win32":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if h == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

def _release_lock():
    try:
        if os.path.exists(LOCK):
            os.remove(LOCK)
    except OSError:
        pass

def acquire_lock():
    """原子创建锁文件并写入 PID；若已有存活实例则退出，避免多开抢 frontier.json。"""
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
            # 锁已存在：判断持有者是否存活
            oldpid = None
            try:
                with open(LOCK) as f:
                    c = f.read().strip()
                oldpid = int(c) if c else None
            except (ValueError, OSError):
                oldpid = None
            if oldpid and _pid_alive(oldpid):
                print(f"[daemon] 已有实例 PID={oldpid} 运行中，本进程退出", flush=True)
                sys.exit(0)
            # 陈旧锁 -> 移除后重试
            try:
                os.remove(LOCK)
            except OSError:
                pass
            continue
        # 成功创建，立即写入 PID
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        # 二次校验：确认锁仍归自己（极小概率被抢占）
        try:
            with open(LOCK) as f:
                if f.read().strip() != str(os.getpid()):
                    print("[daemon] 锁被抢占，退出", flush=True)
                    sys.exit(0)
        except OSError:
            pass
        atexit.register(_release_lock)
        return

def load_cfg():
    try:
        return json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    except Exception:
        return {"schedule_hours": 6}

def main():
    acquire_lock()
    cfg = load_cfg()
    hours = float(cfg.get("schedule_hours", 6))
    py = sys.executable
    print(f"[daemon] 常驻启动，周期 {hours}h，PID={os.getpid()}，锁={LOCK}")
    while True:
        try:
            subprocess.run([py, "-u", os.path.join(HERE, "run_cycle.py")],
                           cwd=HERE, check=False)
        except Exception as e:
            print("[daemon] cycle error:", e, flush=True)
        print(f"[daemon] 下一轮在 {hours}h 后", flush=True)
        time.sleep(hours * 3600)

if __name__ == "__main__":
    main()
