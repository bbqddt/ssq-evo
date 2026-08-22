# C 盘空间盘点报告（只读扫描，未改动任何文件）

生成时间：2026-08-21 18:42 (GMT+8)
工作模式：仅只读盘点 + 报告；任何删除/改动需用户逐条确认后执行。

## 1. 当前状态

| 指标 | 数值 |
|---|---|
| C 盘总容量 | 176.38 GB (已用 125.61 + 剩余 50.77) |
| 已用 | 125.61 GB |
| 剩余 | 50.77 GB |
| 使用率 | 71% |

> 偏差说明：用户记忆中记录 C 盘「约 92% 满 / 红色」。当前实测为 71% 已用、剩余 50.77 GB。
> 可能此前已部分释放，或当时为瞬时峰值。结论：尚未到红色临界点，但清理后仍建议保留充足余量。

## 2. 主要占用排行（Top，含可读扫描无法看见的系统隐藏项如 pagefile/hiberfil）

### 2.1 用户数据 / 应用（AppData，隐藏目录，常规浏览器不显示）
| 位置 | 大小 | 性质 | 清理建议 |
|---|---|---|---|
| AppData\Local\wsl | 18.89 GB | Docker/WSL2 后端 VHDX | 用户要求不动本体；如需回收须迁移到 D 盘（高风险） |
| AppData\Local\Docker | 7.79 GB | Docker Desktop 数据 | 同上，不动本体 |
| AppData\Roaming\anythingllm-desktop | 4.71 GB | anythingllm 数据 | 工具数据，需确认后瘦身 |
| AppData\Local\hermes | 3.05 GB | WorkBuddy 运行时 | 含缓存，部分可清，需确认 |
| AppData\Local\Programs | 3.54 GB | 已安装程序 | 一般不动 |
| AppData\Local\Temp | 1.66 GB | 临时文件 | ✅ 安全清理 |
| AppData\Roaming\npm | 1.91 GB | npm 全局包/缓存 | 部分可清，需确认 |
| Chrome User Data | 5.97 GB | 浏览器（含缓存） | 可清缓存子目录 |
| ms-playwright | 0.67 GB | 浏览器自动化二进制 | 若不用浏览器自动化可删 |
| npm-cache | 0.18 GB | npm 包缓存 | ✅ 安全清理 |
| pip Cache | 0.05 GB | pip 包缓存 | ✅ 安全清理 |
| AppData\Roaming\Tencent | 0.96 GB | 微信 | 用户要求不动本体 |
| OpenAI | 0.70 GB | 缓存 | 可清 |
| Microsoft | 1.10 GB | MS 系缓存 | 可清非关键缓存 |

### 2.2 系统
| 位置 | 大小 | 性质 | 清理建议 |
|---|---|---|---|
| Windows\WinSxS | 19.74 GB | 系统组件存储 | 可 DISM 组件清理回收 superseded 更新（需管理员，系统级） |
| Windows | 37.57 GB (总) | 系统目录 | 勿手动删；仅走系统清理工具 |
| Program Files | 8.23 GB | 程序 | 不动 |
| Program Files (x86) | 5.55 GB | 程序 | 不动 |
| C:\$Recycle.Bin | 0 GB | 回收站 | 已空 |

## 3. 安全快速回收项（零风险、不动任何工具本体，合计约 1.9 GB）
- AppData\Local\Temp → 1.66 GB
- AppData\Local\npm-cache → 0.18 GB
- AppData\Local\pip\Cache → 0.05 GB

## 4. 需用户确认的中/高风险项
1. **WinSxS DISM 组件清理**：通常可回收数 GB，系统级，需管理员权限，安全但建议确认。
2. **Chrome 缓存清理**：可释放 5.97 GB 中的一部分（不影响账号/书签）。
3. **ms-playwright 删除**：0.67 GB，前提是不再用浏览器自动化。
4. **Docker/WSL 迁移到 D 盘**：最大回收约 26 GB，但需停机 + 重配，风险高，需单独方案。
5. **anythingllm 数据瘦身 / OpenAI 缓存清理 / npm 全局包清理**：需逐项确认。

## 5. 执行纪律（安全规范）
- 任何删除前，先逐条列出具体文件路径与大小，等用户最终确认。
- 绝不触碰用户要求保留的 WSL / Docker / 微信 本体。
- 删除一律走系统回收机制（先备份/移回收站），不用 rm 硬删。
- 不向 C 盘写入任何新文件/数据（报告本身存于 D 盘）。

## 6. 下一步
请用户选择要执行的清理项（见对话中的确认选项）。选择后我将先给出逐条路径清单，确认后再执行。
