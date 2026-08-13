# 双色球结构搜索引擎 · 7×24 部署指南

## 这个系统在做什么（再说一次，避免误用）

它是一个**自适应连续结构搜索仪器**，不是预测器：

- 每期新开奖 = 对"序列无结构"零假设的一次新检验；样本越多，对"隐藏结构"的排除越硬。
- 算子 = (信号映射 × 检验统计 × 参数) 在**演化搜索**中不断变异/重组，探索更广的假设空间
  （你提的"能量/频率/振动"已落成 `vector_mag / vector_phase / complex_field / red_energy` 等映射）。
- 每个算子都和 **AAFT surrogate**（保留频谱+分布、破坏时序）对比，再用 **BH 假发现率(FDR)**
  跨全部候选校正，防止演化搜索本身挖出假规律。
- 全量最优算子还要在**最近 20% 数据上样本外复现**，过不了就是过拟合，不报。
- **唯一成功条件**：某算子经 FDR(q<0.01) 且样本外复现 → 触发看板警报。否则持续返回 null。
- 持续 null 是科学结果（证据的缺席随测试增强），不构成对时间是否存在的形而上学证明，
  也不赋予任何预测/下注权。

## 文件结构

```
ssq_evo/
  engine_core.py   信号映射库 + 检验统计库 + surrogate + 演化 + FDR + 样本外验证
  data.py          增量抓取(500彩票网) + 按期号合并
  store.py         SQLite 持久化(runs / evals)
  run_cycle.py     一轮编排：抓数→演化→FDR→OOS→写库+state.json
  serve.py         7×24 看板(http.server, 无第三方依赖)
  daemon_loop.py   常驻循环(配合 nssm)
  config.json      参数(epochs/pop/surrogate 数/端口/周期)
  ssq_master.csv   本地主表(自动生成)
  ssq_evo.db       历史库(自动生成)
  state.json       看板数据源(每轮生成)
```

## 依赖（一次性）

```
python -m venv venv
venv\Scripts\pip install numpy scipy
```

## 部署到本机（7×24 持久化）

> ⚠️ **磁盘约束**：按项目约定，运行数据/库应落在 **D 盘**，不要写 C 盘。
> 把整个 `ssq_evo/` 目录复制到 `D:\ssq_evo\`，并在该目录放好 venv 与数据。

### 方式 A：nssm 注册常驻服务（推荐，开机自启）

1. 下载 nssm，执行 `nssm install ssq-evo "D:\ssq_evo\venv\Scripts\python.exe"`
2. 在 nssm 界面 Application 参数填：`D:\ssq_evo\daemon_loop.py`
3. 工作目录：`D:\ssq_evo\`
4. `nssm start ssq-evo`
   - daemon_loop 会每 `schedule_hours`(默认6h) 跑一轮 run_cycle，断网自动跳过抓取。
5. 看板：`python serve.py` 后访问 `http://localhost:8088`
   （serve 也可单独用 nssm 再注册一个服务，或用 `nssm set ssq-evo AppParameters` 改为同时拉起——建议分开两个服务：ssq-evo-daemon 与 ssq-evo-web）

### 方式 B：Windows 计划任务（更轻）

1. 任务计划程序 → 创建任务 → 触发器"每隔 6 小时"。
2. 操作：启动 `D:\ssq_evo\venv\Scripts\python.exe`，参数 `D:\ssq_evo\run_cycle.py`。
3. 勾选"不管用户是否登录都要运行" + "最高权限"。
4. 看板按需手动 `python serve.py`，或用计划任务在登录时拉起。

## 调参（config.json）

| 键 | 含义 | 调大效果 |
|---|---|---|
| epochs | 演化代数 | 搜得更深，更慢 |
| pop | 每代种群大小 | 假设空间更广，更慢 |
| k_light / k_heavy | 轻/重检验的 surrogate 数 | 显著性估计更稳，更慢 |
| alert_q / alert_oos_p | 警报阈值 | 调小更严格 |
| schedule_hours | 自动周期 | 调小更新更频繁 |
| http_port | 看板端口 | 避免冲突 |

若一轮耗时过长（>20min），优先降 `pop` 与 `k_heavy`，并重检验的 `sub` 子采样上限
（在 engine_core.py 的 `t_rq_determinism / t_lyap_rosenstein / t_approx_entropy` 中）。

## 怎样"修正、演进"你的方向

- 想加新的"能量/频率/振动"映射：在 `engine_core.py` 的 `SIGMAPS` 里加一个函数即可，
  演化会自动把它纳入候选空间。
- 想加新的检验统计：在 `TESTS` 里加，标注 direction（'high'/'low'）与 tier（'light'/'heavy'）。
- 想换更强的零假设对照：surrogate 默认 AAFT；可在 `evaluate()` 把 `sur_type` 切到 `"shuffle"`。

## 诚实边界（写进代码，也写在这里）

本系统监控的是"序列中是否存在可检测结构"。它**不能**证明或反驳时间是否存在；
即便某日触发警报，也只说明"此序列在该算子下有非随机结构"，不等于可预测下期、
更不等于时间无存。任何下注/投资行为都属误用。
