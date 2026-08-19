# -*- coding: utf-8 -*-
"""
ssq_health.py —— 唯一真实状态读取入口（单一真理源）

设计目的：根治"沙箱陈旧视图陷阱"与"自动化读到假数据"。
Bash 工具沙箱对 D:/ssq_evo_data 有一份元数据新鲜、内容陈旧的覆盖快照，
裸路径读 state.json/daemon.log 会得到 cycle 2 / 8-13 的旧值，导致误判
"引擎停了 / 状态过期"。所有只读实时状态必须走 docker exec 进容器内
/app/data（真实挂载卷），或经 GitHub API 查 CI。

规则（全项目强制）：
  - 任何脚本/自动化要查 ssq_evo 实时状态，import 本模块，禁止裸 open('D:/ssq_evo_data/*')。
  - 判"系统死活"只用：container_status() / get_live_state() / get_digest_tail()。
"""
import subprocess
import json
import os

CONTAINER = "ssq-evo-engine"
PROJECT_DIR = r"D:\ssq_evo"
DATA_DIR = r"D:\ssq_evo_data"


def _docker_exec(args, timeout=30):
    """容器内执行命令，返回 (rc, stdout_stripped, stderr_stripped)。"""
    try:
        r = subprocess.run(
            ["docker", "exec", CONTAINER] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def container_status():
    """容器运行态：返回 `docker ps` 的 Status 字段（含 Up 时长）。空=未运行。"""
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", f"name={CONTAINER}",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=20,
        )
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def container_running():
    s = container_status()
    return bool(s) and "Up" in s


def get_live_state():
    """读容器内 state.json（真实卷），返回 dict；失败返回 None。"""
    rc, out, _ = _docker_exec(["cat", "/app/data/state.json"])
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def get_digest_tail(n=1):
    """读 daily_digest.jsonl 末 n 行（权威结论源），返回 list[dict]。"""
    rc, out, _ = _docker_exec(["tail", "-n", str(n), "/app/data/daily_digest.jsonl"])
    if rc != 0 or not out:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def image_build_sha():
    """读容器内镜像构建 SHA（Dockerfile 注入的 build_info.txt）。None=未知。"""
    rc, out, _ = _docker_exec(["cat", "/app/build_info.txt"])
    return out if rc == 0 and out else None


def expected_git_sha():
    """本地 git HEAD（宿主机实时，沙箱视图对 D:/ssq_evo 源码是实时的）。"""
    try:
        r = subprocess.run(
            ["git", "-C", PROJECT_DIR, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def get_ci_status():
    """查 GitHub Actions 最近一次 run 的状态（本机用 gh，无则返回 None）。

    返回 dict: {run_number, status, conclusion, head_sha, created_at} 或 None。
    注意：云端自动化环境访问 GitHub API 应走 WebFetch，不要依赖本函数。
    """
    try:
        r = subprocess.run(
            ["gh", "run", "list", "--repo", "bbqddt/ssq-evo", "--limit", "1",
             "--json", "number,status,conclusion,headSha,createdAt"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        import json as _j
        rows = _j.loads(r.stdout)
        if not rows:
            return None
        row = rows[0]
        return {
            "run_number": row.get("number"),
            "status": row.get("status"),
            "conclusion": row.get("conclusion"),
            "head_sha": row.get("headSha"),
            "created_at": row.get("createdAt"),
        }
    except Exception:
        return None


def build_sha_in_sync():
    """镜像构建 SHA 是否等于本地 git HEAD。返回 (in_sync: bool, detail: str)。"""
    img = image_build_sha()
    exp = expected_git_sha()
    if not img or not exp:
        return None, f"unknown (image={img}, git={exp})"
    if img == exp:
        return True, f"image={img[:8]} == git HEAD"
    return False, f"image={img[:8]} != git HEAD={exp[:8]} (容器跑的是旧镜像!)"


if __name__ == "__main__":
    print("container:", container_status())
    print("live_state:", get_live_state())
    print("build_sha_in_sync:", build_sha_in_sync())
    print("ci:", get_ci_status())
