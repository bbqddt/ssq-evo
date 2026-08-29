# ssq_evo 引擎健康巡检 — 2026-08-29 12:07 (UTC+8)

## 总体结论：健康（有 1 项非紧急提示）

| # | 检查项 | 结果 | 详情 |
|---|--------|------|------|
| 1 | 容器存活 | ✅ | `ssq-evo-engine` Up 12 minutes |
| 2 | 进程不卡死 | ✅ | run_cycle.py 在跑，CPU TIME 01:35 / ~12min 墙钟（重型 GA 计算，非卡死） |
| 3 | state 新鲜度 | ✅ | cycle_id=456, updated=2026-08-29 12:07:50（实时，<12h） |
|  ｜ best_sig | red_weighted |
| 4 | 防御未绕过 | ✅ | best_sig=red_weighted，不在 artifact_prone=[red_recurrence_mean] |
| 5 | 崩溃痕迹 | ✅ | 无 Traceback；[cycle] 输出实时（12:09 仍是 bias_corrector 落盘 / cycle #39） |
| 6 | 摘要时效 | ✅ | daily_digest 末行 ts=2026-08-29 12:07:50（<24h） |
| 7 | 数据接收 | ✅ | ssq_master.csv 最新期号 26099（26099 已落在 CSV）；今日周六，最近开奖周四→未超 2 天未缺 |
| 8 | 部署一致性 | ⚠️ 提示 | 容器 build_info=939fcdc08db7ce8233ad6faf4c47e61443c9c3fd ≠ 宿主 HEAD=54b1dadffa5698f9f1dc9db6e17610ca7683c0a9 |
| 9 | CI 门禁 | ✅ | 最新 run 33218924597 conclusion=success（head_sha=939fcdc0，与容器一致） |
| 10 | 镜像 SHA 注入 | ✅ | build_info 非 unknown |
| 11 | 数值闸门对账 | ✅ | frontier df_gen=4，daemon.log df_gen=4（无 transient 尖峰）；comp 精英总数=0，不触发空壳告警 |
| 12 | digest↔frontier 一致 | ✅ | 二者 df_gen 均为 4 |
| 13 | 阳性对照 | ✅ | positive_control.verified=True（p=None 但 verified=True，功率有效） |
| 14 | 闸门不可绕过 | ✅ | bypass=0, unrec=0（无绕过、无未记录 verdict） |
| 15 | 元审计 | ⚠️ 误报 | meta_audit BLOCK=1（dockerfile_copy: evolve_predictor.py）—— 经核实为**误报**：该文件是独立实验脚本（pre_commit_check.py 注释明确"不属 Docker 引擎镜像"），daemon 不 import 它，容器运行正常；无需重建。INFO×2 为「汇报诚实」提醒（pick_p=1.0 不优于随机 / df_gen=4 但零确认），已并入结论。 |

## 关键解读
- **部署一致性（唯一需关注项）**：容器跑的是 CI 最新通过版本 939fcdc0（与 GitHub 最新 run head_sha 一致），但宿主工作区 `D:/ssq_evo` 的 git HEAD（54b1dadf）领先于已部署镜像。说明宿主有未部署的本地改动。若你打算把本地改动上线，需重建镜像（`GIT_SHA=$(git -C D:/ssq_evo rev-parse HEAD) docker compose up -d --build`）；若不打算上线，此项可忽略。
- **诚实基线重申**：当前 best_verdict=None、pick_p=1.0（选号不优于随机），所有"边缘/UNCONFIRMED"结论均不应被表述为"突破"或"准确率提升"——与 meta_audit 的 INFO 一致。
- 本次巡检为只读，未修改任何代码/数据。

## 建议
- 部署一致性：按需在合适时机重建镜像以纳入本地 HEAD；否则保持现状无风险。
- meta_audit 提示的 BLOCK 为误报，建议日后整理 `meta_audit.py` 的 dockerfile_copy 检查规则（排除被 pre_commit_check 标记为"非镜像"的脚本），避免反复触发 cry-wolf。
