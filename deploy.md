# ssq_evo · 快速部署

> **完整架构、模块地图、红线、操作手册 → 见 [ARCHITECTURE.md](./ARCHITECTURE.md)**
>
> 本文件仅保留"从零到跑起来"的最小步骤。

## 一句话定位

双色球开奖序列的**结构搜索引擎**（不是预测器）。域大概率是 null——诚实结论就是"未发现可复现结构"，这本身有效。

## 前提

- Python 3.13+ / numpy / scipy
- Docker（可选，推荐用于 7×24 常驻）
- 数据目录 `D:/ssq_evo_data`（代码在 `D:/ssq_evo`，绝不写 C 盘）

## 快速启动

### 本机直接跑（单轮测试）

```bash
cd D:\ssq_evo
python -m venv venv
venv\Scripts\pip install numpy scipy
venv\Scripts\python run_cycle.py          # 跑一轮
venv\Scripts\python smoke_test.py         # 冒烟测试（应输出 SMOKE_OK）
venv\Scripts\python formula_viz.py        # 公式可视化（产出 formula_language.html）
```

### Docker 7×24 常驻（生产）

```bash
cd D:\ssq_evo
docker compose up -d --build              # 重建镜像并启动
# 数据卷自动挂载 D:/ssq_evo_data -> /app/data
# daemon_loop 数据驱动调度：新开奖到达即评估
docker compose logs -f --tail 20          # 看日志
```

### 看门狗（崩溃自愈）

```powershell
# 以管理员运行：注册计划任务（登录时 + 每30min）
.\install_watchdog.ps1
# 检测：容器存活/log静止>90min/state过旧>48h/cycle卡>120min → 自动 docker compose up -d
```

### CloudStudio 看板

```bash
python make_dashboard.py                  # 读 daily_digest.jsonl 生成 dashboard/index.html
# 手动把 D:/ssq_evo_data/dashboard/ 发布到 CloudStudio
```

## 开奖日自动化

```bash
python predict_tonight.py auto            # 注册候选（引擎公式驱动）→ 开奖后抓取校对 → 评分
# 或通过 Automation（每开奖日 18:00 注册 / 22:30 校对）
```

## 核心文件速查

| 文件 | 用途 |
|------|------|
| `run_cycle.py` | 一轮编排（多源候选→统一闸门→digest） |
| `engine_core.py` | 演化引擎 + 信号库 + 检验统计 + surrogate |
| `firewall.py` | 四道物理防火墙（数据隔离/指标隔离/审计/随机重放） |
| `proposer.py` | 智能演进子系统（默认关，`intelligent_evolution_enabled`） |
| `scoring.py` | 正确评分规则 + live 排行榜 |
| `evaluator.py` (#41) | 发现/确认分离 walk-forward |
| `run_axes.py` | 轴驱动器 + representation_zoo + layered_null |
| `formula_viz.py` | 公式语言可视化（带确认闸门状态） |
| `daemon_loop.py` | 7×24 常驻循环（数据驱动调度） |
| `watchdog.ps1` | 崩溃循环检测 + 自愈 |
| `ARCHITECTURE.md` | **完整文档**（架构图/红线/模块清单/科学结论） |

## 诚实红线摘要

1. **禁止绕闸门**：所有候选源汇入同一 BH-FDR + #41 + 随机对照闸门
2. **null 域不造假阳性**：无监督优化器不得以"过闸"为目标搜索
3. **预测必须接引擎结论**：不得另起朴素频率计数器绕过引擎
4. **改代码必重建镜像**：Dockerfile COPY 列表须同步新增 .py
5. **看板产物不进 GitHub**：dashboard/ + daily_digest.jsonl 已 .gitignore

## 当前科学结论

- **真实数据：无经确认结构**（null 域）
- **阳性对照：AR(1) 注入检出 SIGNAL**（闸门功率正常）
- 结论：不是"没找出来"，而是"真的没有可复现结构"
