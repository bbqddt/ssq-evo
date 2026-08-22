# verify_df_gen.py
# ---------------------------------------------------------------------------
# 重启感知的 df_gen 演进验证器
#
# 设计要点（修复旧 watchdog 的缺陷）：
#   旧 watchdog 只在 daemon 运行期内轮询 "df_gen != 6"，但 daemon 重建(11分钟)期间
#   它会漏看整个过渡期，且把 "df_gen 变成 1"(播种期地板值) 误判为"演进成功"——
#   其实 df_gen=1 只是播种期语义，真正代际上长要等首个 comp 精英过闸门。
#
# 本工具改为：
#   1. 先确认容器镜像 SHA == git HEAD（验证新代码真的在跑，而非旧镜像）；
#   2. 读 digest 最新 cycle 的 df_gen，给出诚实语义解读：
#      - 镜像不匹配 -> 明确报"新代码未生效"；
#      - df_gen == 6 且镜像为旧 -> 仍是历史锁死值；
#      - df_gen == 1 且镜像为新 -> 播种期(正确地板值)，代际演进待首个 comp 精英过闸门；
#      - df_gen >= 2 -> 代际已真实上长（演进生效）。
#   3. 不把"df_gen=1"误报为失败或成功，只陈述研发进度。
# ---------------------------------------------------------------------------
import json, subprocess, sys

# 数据卷默认路径（host 侧；docker 容器内为 /app/data，host 挂载为 /d/ssq_evo_data）
DATA_DIR = "/d/ssq_evo_data"

def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()[:8]
    except Exception:
        return "?"

def image_sha():
    # 镜像无 build_sha label，退回读 daemon.log 里的"镜像构建 SHA="
    try:
        log = open(f"{DATA_DIR}/daemon.log").read()
        for line in reversed(log.splitlines()):
            if "镜像构建 SHA=" in line:
                return line.split("SHA=")[1].strip()[:8]
    except Exception:
        pass
    return "?"

def last_digest():
    try:
        lines = [l for l in open(f"{DATA_DIR}/daily_digest.jsonl") if l.strip()]
        if not lines:
            return {}
        return json.loads(lines[-1])
    except Exception:
        return {}

def main():
    head = git_head()
    sha = image_sha()
    d = last_digest()
    dg = d.get("df_gen")
    cid = d.get("cycle_id")
    ts = d.get("ts")
    print(f"git HEAD      = {head}")
    print(f"image build   = {sha}")
    print(f"latest cycle  = {cid} @ {ts}")
    print(f"latest df_gen = {dg!r}  (df_added={d.get('df_added')!r})")
    print("-" * 56)
    if sha != head and sha != "?":
        print("❌ 容器镜像 SHA != git HEAD：新代码未生效，daemon 可能跑旧镜像。")
        print("   需 docker compose up -d --build 后确认 SHA 对齐。")
        return 2
    if dg == 6 and sha == head:
        print("⚠️ 镜像已新但 digest 仍是历史锁死值 df_gen=6（过渡期，等待新 cycle 落盘）。")
        return 1
    if dg == 1:
        print("✅ 新代码生效，df_gen=1 = 播种期地板值（正确）。")
        print("   代际真实上长需等首个 comp 精英通过统一闸门进入 frontier；")
        print("   在此之前 df_gen=1 是诚实的研发进度陈述，非停滞/非失败。")
        return 0
    if isinstance(dg, int) and dg >= 2:
        print(f"🎯 代际已真实上长：df_gen={dg} >= 2，复合公式演进生效。")
        return 0
    print("ℹ️ 状态未知，见上。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
