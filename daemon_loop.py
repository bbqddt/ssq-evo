# -*- coding: utf-8 -*-
"""
daemon_loop.py —— 7x24 常驻循环（配合 nssm 注册为 Windows 服务，或计划任务 SYSTEM 开机自启）

调度模式（优先级从高到低）：
  1. 环境变量 CYCLE_MINUTES：
     =0 或负数 → 连续模式(60s 冷却)，每轮都跑全量 cycle（兼容旧行为）
     >0        → 定时模式，每 N 分钟跑一轮
  2. config.json 的 schedule_mode="data_driven"（推荐）：
     无新数据时每 idle_minutes 检查一次(轻量，不跑 cycle)；
     新数据到达时立即触发全量评估 + 摘要。
     这让引擎从"同一数据反复空转"变为"等新开奖→响应→再等"，真正实现定时回馈。

日志改进：子进程输出逐行实时落盘（不再黑洞），cycle 摘要写入 daily_digest.jsonl（追加式 JSONL），
供外部/看板直接读取结论，不依赖解析 daemon.log。

启动：python daemon_loop.py
"""
import os, sys, time, subprocess, json, errno, atexit, threading
import ssq_log
import paths

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA_DIR = paths.DATA_DIR
LOCK = os.path.join(DATA_DIR, "daemon.lock")
DIGEST = os.path.join(DATA_DIR, "daily_digest.jsonl")

# ── 跨平台 PID 存活检测 ──────────────────────────────────────────────
def _pid_alive(pid):
    if sys.platform == "win32":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
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
    except OSError as _e:
        ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:52 silent-except")


def acquire_lock():
    """原子创建锁文件并写入 PID；若已有存活实例则退出，避免多开抢 frontier.json。"""
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
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
            try:
                os.remove(LOCK)
            except OSError as _e:
                ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:76 silent-except")
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        try:
            with open(LOCK) as f:
                if f.read().strip() != str(os.getpid()):
                    print("[daemon] 锁被抢占，退出", flush=True)
                    sys.exit(0)
        except OSError as _e:
            ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:86 silent-except")
        atexit.register(_release_lock)
        return


def load_cfg():
    """读取引擎配置：YAML(engine.yaml) 为 canonical 源，config.json 覆盖部署键。

    注意：本函数此前只读了 config.json，导致 engine.yaml 的 schedule_mode 等键
    从未生效（daemon 一直落到 schedule_hours 兜底=定时模式）。现在与 run_cycle.load_cfg
    保持一致——先 YAML 后 config.json 覆盖。"""
    cfg = {}
    # 1) YAML（configs/engine.yaml）作为 canonical 源
    try:
        sys.path.insert(0, HERE)
        from configs import load_engine_config
        cfg.update(load_engine_config())
    except Exception as _e:
        ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:104 silent-except")
    # 2) config.json 覆盖（部署键如 http_port / schedule_hours 应留在此）
    for base in (DATA_DIR, HERE):
        try:
            cfg.update(json.load(open(os.path.join(base, "config.json"), encoding="utf-8")))
            break
        except Exception:
            continue
    return cfg


def _log(msg):
    """写日志 + flush。"""
    print(msg, flush=True)


def run_cycle_subprocess(py):
    """运行一轮 run_cycle，逐行实时落盘子进程输出（修复 Docker 日志黑洞）。

    ⚠️ 关键修复（生产事故根因）：原实现用
        for raw_line in iter(proc.stdout.readline, b""):
            ...
        proc.wait(timeout=1800)
    读取循环会一直阻塞到 run_cycle 关闭 stdout（即子进程退出）才执行 proc.wait，
    导致「单轮 30min 强杀」对卡死/超慢的 run_cycle 完全失效——守护进程死等子进程，
    永不触发杀进程重启。2026-08-25 实测：run_cycle 卡死 8h 无人强杀，state.json 陈旧 21h。
    现改用独立读取线程排空 stdout + 主线程 proc.wait(timeout) 真正生效的超时强杀。
    """
    log_path = os.path.join(DATA_DIR, "daemon.log")
    proc = subprocess.Popen(
        [py, "-u", os.path.join(HERE, "run_cycle.py")],
        cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1,  # line-buffered
    )

    def _reader():
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                for raw_line in iter(proc.stdout.readline, b""):
                    lf.write(raw_line.decode("utf-8", errors="replace"))
                    lf.flush()
        except Exception as _e:
            ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:146 silent-except")

    rt = threading.Thread(target=_reader, daemon=True)
    rt.start()
    try:
        proc.wait(timeout=2700)  # 单轮最长 45min 防死循环(原30min因读取阻塞从未生效; 放宽容许重 GA)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception as _e:
            ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:158 silent-except")
        _log("[daemon] cycle 超时(45min), 已强杀并重启")
        rc = -1
    except Exception as e:
        _log(f"[daemon] cycle 异常: {e}")
        rc = -1
    rt.join(timeout=5)
    regen_dashboard(py)   # 每轮结束(无论成败)重建 CloudStudio 看板, 保证对外监控始终最新
    run_ingest_subprocess(py)  # 摄入驾3云端提案(ga-candidates), 过统一闸门后并入 frontier
    run_failure_absorber_subprocess(py)  # L1 失败吸收器: 吃驾1 state+驾3 fate, 落 avoidance_prior 回馈三驾车
    run_bias_corrector_subprocess(py)  # L3 偏置纠正器: 读 L1 产物+驾3 fate, 落 bias_corrector.json 回馈三驾车
    return rc


def run_failure_absorber_subprocess(py):
    """L1 失败吸收器（学习模块）：每轮 cycle 后跑。
    输入=驾1 闸门 state.json + 驾3 提案过闸 fate(ingest_fate.jsonl)；产出=avoidance_prior.json，
    供 run_cycle / ci_evolve 生成候选时降权已知死胡同（契约基石二：学习回馈三驾车）。
    容错：失败不影响主流程（下一轮重试）。"""
    try:
        proc = subprocess.run(
            [py, "-u", os.path.join(HERE, "failure_absorber.py"), "--from-state"],
            cwd=HERE, capture_output=True, text=True, timeout=120,
        )
        for line in (proc.stdout or "").splitlines():
            _log("[L1] " + line)
        if proc.returncode != 0:
            _log(f"[daemon] L1 失败吸收器返回码={proc.returncode}")
    except subprocess.TimeoutExpired:
        _log("[daemon] L1 失败吸收器超时(120s), 跳过本轮")
    except Exception as e:
        _log(f"[daemon] L1 失败吸收器失败(不影响主流程): {e}")


def run_bias_corrector_subprocess(py):
    """L3 偏置纠正器（学习模块）：每轮 L1 之后跑。
    输入=L1 的 failure_taxonomy.json + avoidance_prior.json + 驾3 提案 fate(ingest_fate.jsonl)；
    产出=bias_corrector.json（已证伪路线清零/降 seed 预算、高新颖度倾斜、驾1 精英保留偏置），
    供 ci_evolve(驾3) / engine_core.Evolution(驾1) 消费（契约基石二：学习回馈三驾车）。
    容错：失败不影响主流程（下一轮重试）。"""
    try:
        proc = subprocess.run(
            [py, "-u", os.path.join(HERE, "bias_corrector.py"), "--from-state"],
            cwd=HERE, capture_output=True, text=True, timeout=120,
        )
        for line in (proc.stdout or "").splitlines():
            _log("[L3] " + line)
        if proc.returncode != 0:
            _log(f"[daemon] L3 偏置纠正器返回码={proc.returncode}")
    except subprocess.TimeoutExpired:
        _log("[daemon] L3 偏置纠正器超时(120s), 跳过本轮")
    except Exception as e:
        _log(f"[daemon] L3 偏置纠正器失败(不影响主流程): {e}")


def run_ingest_subprocess(py):
    """摄入驾3 云端提案（ga-candidates 分支的 candidates.json），过统一闸门后并入 frontier。
    候选由看门狗(宿主)定期 fetch 到 DATA_DIR/candidates.json；容器只读本地文件，
    避免容器内直接访问 GitHub 的网络/代理依赖。无候选/失败均不影响主流程。"""
    try:
        proc = subprocess.run(
            [py, "-u", os.path.join(HERE, "ingest_candidates.py"), "--local",
             os.path.join(DATA_DIR, "candidates.json")],
            cwd=HERE, capture_output=True, text=True, timeout=120,
        )
        for line in (proc.stdout or "").splitlines():
            _log("[ingest] " + line)
        if proc.returncode not in (0, 2):  # 2=无真实数据(跳过)
            _log(f"[daemon] 摄入驾3提案返回码={proc.returncode}")
        elif proc.returncode == 0:
            # 摄入成功后消费即删：候选只处理一次，避免每轮重复跑 48×surrogate 白耗算力
            try:
                p = os.path.join(DATA_DIR, "candidates.json")
                if os.path.exists(p):
                    os.remove(p)
                    _log("[ingest] 已消费 candidates.json（处理一次，防重复摄入）")
            except Exception as e:
                _log(f"[ingest] 删除 candidates.json 失败: {e}")
    except subprocess.TimeoutExpired:
        _log("[daemon] 摄入驾3提案超时(120s), 跳过本轮")
    except Exception as e:
        _log(f"[daemon] 摄入驾3提案失败(不影响主流程): {e}")


def regen_dashboard(py):
    """每轮 cycle 后重建 CloudStudio 看板：读取最新 daily_digest.jsonl,
    复制 jsonl 进 dashboard/ 并重新嵌入快照, 使对外监控层始终反映最新结论。
    容错：失败不影响主流程（下一轮会重试）。"""
    try:
        _log("[daemon] 重建 CloudStudio 看板...")
        subprocess.run(
            [py, "-u", os.path.join(HERE, "make_dashboard.py")],
            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            timeout=120,
        )
        _log("[daemon] 看板已重建")
    except subprocess.TimeoutExpired:
        _log("[daemon] 看板重建超时(120s), 跳过本轮")
    except Exception as e:
        _log(f"[daemon] 看板重建失败(不影响主流程): {e}")


def get_last_issue():
    """读取 state.json 中记录的最新期号（无 state 则返回 None）。"""
    sp = os.path.join(DATA_DIR, "state.json")
    try:
        s = json.load(open(sp, encoding="utf-8"))
        return s.get("last_issue")
    except Exception:
        return None


def check_new_data(py):
    """轻量检查是否有新开奖数据（调 data.fetch_recent 不跑 cycle）。
    返回 (new_count, latest_issue) 或 (0, None)。
    """
    try:
        proc = subprocess.run(
            [py, "-c",
             "import sys; sys.path.insert(0,'" + HERE.replace("\\","/") + "');"
             "import data as D; f=D.fetch_recent();"
             "print(len(f) if f else -1);"
             "print(f[-1][0] if f else 'NONE') if f else print('NONE')"],
            capture_output=True, text=True, timeout=60, cwd=HERE,
        )
        out = proc.stdout.strip().splitlines()
        if len(out) >= 2 and out[0].isdigit():
            total = int(out[0])
            latest = out[1].strip()
            last = get_last_issue()
            if last and latest > last:
                # 粗略估算新增：不能精确知道（fetch 返回全量），用最新期号差上界
                return min(total, 10), latest  # 双色球每周3期，最多新增~3-4期
            return 0, latest
        return 0, None
    except Exception as e:
        _log(f"[daemon] 数据检查异常: {e}")
        return 0, None


def print_daily_summary():
    """从 daily_digest.jsonl 读最近 N 条摘要，打印人可读的日报。"""
    if not os.path.exists(DIGEST):
        _log("[digest] 尚无摘要记录（首轮 cycle 完成后将生成）")
        return
    try:
        lines = open(DIGEST, encoding="utf-8").readlines()
        recent = [json.loads(l) for l in lines[-24:]]  # 最近 24 条
        if not recent:
            return
        last = recent[-1]
        alerts = sum(1 for r in recent if r.get("alert"))
        artifacts = set()
        for r in recent:
            for a in (r.get("artifact_prone") or []):
                artifacts.add(a)
        _log("=" * 60)
        _log(f"[日报] 截止 {last['ts']}  |  cycle {last['cycle_id']}  |  "
             f"{last['n_issues']}期  |  最新={last.get('last_issue','?')}  "
             f"新增={last.get('added',0)}")
        _log(f"[日报] 最佳: {last.get('best_sig')}/{last.get('best_test')}  "
             f"q={last.get('best_q')}  |  判定: {last.get('verdict')}  |  "
             f"备注: {last.get('note','?')}")
        _log(f"[日报] 覆盖度: {last.get('coverage')}  |  "
             f"最近{len(recent)}轮 ALERT次数: {alerts}  |  "
             f"构造伪结构: {sorted(artifacts) if artifacts else '无'}")
        if last.get("wf_verdict"):
            _log(f"[日报] Walk-Forward: {last['wf_verdict']}")
        _log("=" * 60)
    except Exception as e:
        _log(f"[日报] 生成失败: {e}")


def main():
    # 日志落盘（追加），便于监控
    try:
        _lf = open(os.path.join(DATA_DIR, "daemon.log"), "a", encoding="utf-8")
        sys.stdout = _lf
        sys.stderr = _lf
    except Exception as _e:
        ssq_log.log_exception("daemon_loop", _e, "daemon_loop.py:338 silent-except")
    acquire_lock()

    cfg = load_cfg()
    py = sys.executable

    # ── 调度模式解析 ────────────────────────────────────────────────
    # 兜底初值（防止 CYCLE_MINUTES="" 或非法值时 UnboundLocalError）
    mode = "continuous"
    label = "连续(60s冷却)"

    env_min = os.environ.get("CYCLE_MINUTES")
    if env_min and env_min.strip():
        try:
            minutes = float(env_min.strip())
        except ValueError:
            _log(f"[daemon] WARNING: CYCLE_MINUTES='{env_min}' 非法，回退配置模式")
            minutes = None
        if minutes is not None:
            if minutes <= 0:
                mode = "continuous"
                label = "连续(60s冷却)"
            else:
                mode = "timed"
                label = f"定时({minutes}分钟)"
    elif cfg.get("schedule_mode") == "data_driven":
        mode = "data_driven"
        idle_min = float(cfg.get("idle_minutes", 360))   # 无新数据时休眠间隔(默认6h)
        check_min = float(cfg.get("check_minutes", 60))   # 休眠中多久查一次新数据(默认1h)
        label = f"数据驱动(空闲{idle_min}min/检查{check_min}min)"
    else:
        hours = float(cfg.get("schedule_hours", 0.25))
        if hours == 0:
            mode = "continuous"
            label = "连续(60s冷却)"
        else:
            mode = "timed"
            minutes = hours * 60
            label = f"定时({minutes:.0f}分钟)"

    _log(f"[daemon] 常驻启动 PID={os.getpid()} 模式={label} 锁={LOCK}")

    # 打印镜像构建 SHA（Dockerfile 注入 build_info.txt），便于核对"容器是否跑最新代码"。
    # 真正的旧码检测由 verify_deployment.check_build_sha() 比对 git HEAD 完成。
    try:
        binfo = os.path.join(HERE, "build_info.txt")
        if os.path.exists(binfo):
            _sha = open(binfo, encoding="utf-8").read().strip() or "unknown"
            _log(f"[daemon] 镜像构建 SHA={_sha[:8]}  (verify 比对此值 vs git HEAD)")
        else:
            _log("[daemon] 镜像构建 SHA=未知 (build_info.txt 缺失)")
    except Exception as e:
        _log(f"[daemon] 读 build_info 失败(不影响主流程): {e}")

    # 启动时打印最近日报
    print_daily_summary()

    # 启动即跑一次全量评估：应用 artifact 闸门 + 写摘要 + 校正 best_sig。
    # 否则切到 data_driven 后会一直 idle 等数据，现有历史数据永不被处理/出结论。
    _log("[daemon] 启动初始全量评估（应用随机对照闸门 + 写摘要）...")
    run_cycle_subprocess(py)
    print_daily_summary()

    # ── 主循环 ───────────────────────────────────────────────────────
    if mode == "data_driven":
        while True:
            # 1. 检查新数据
            n_new, latest = check_new_data(py)
            if n_new > 0:
                _log(f"[daemon] ★ 检测到新数据! 最新={latest}, 约{n_new}期新 → 触发全量评估")
                run_cycle_subprocess(py)
                print_daily_summary()
                _log(f"[daemon] 全量完成，{idle_min:.0f}min 后再次检查")
                time.sleep(idle_min * 60)
            else:
                _log(f"[daemon] 无新数据(当前最新≈{latest or '?'})，{check_min:.0f}min 后复查")
                time.sleep(check_min * 60)

    elif mode == "continuous":
        while True:
            run_cycle_subprocess(py)
            _log("[daemon] 连续模式，60s 后下一轮")
            time.sleep(60)

    else:  # timed
        sleep_sec = minutes * 60
        while True:
            run_cycle_subprocess(py)
            _log(f"[daemon] 定时模式，{sleep_sec:.0f}s 后下一轮")
            time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
