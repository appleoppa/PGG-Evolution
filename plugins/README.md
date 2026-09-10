# plugins/ — 宿主接入层

**核心引擎是通用的**（`scripts/self_evolve.py`，纯 Python 标准库），本目录只是"如何把引擎接到宿主"的适配层。

## 目录说明

| 路径 | 作用 |
|---|---|
| `plugin.json` | **通用**插件元数据（任何宿主可读；沙箱路径用 `EVOLUTION_SANDBOX` 环境变量配置） |
| `pi/` | **示例适配器**：演示如何在 Pi 宿主把 CLI 包装成工具（`pgg-self-evolution.ts`） |

## 通用接入（推荐）

不需要适配器，任何 agent 直接用：

```bash
python3 scripts/self_evolve.py --health       # 健康检查
python3 scripts/self_evolve.py --status       # 统一状态
python3 scripts/self_evolve.py --help         # 全部命令
```

## 写自己的适配器

仿照 `pi/pgg-self-evolution.ts`：把 CLI 命令包装成宿主工具即可。
Codex / Claude / DeepSeek / 其他宿主同理——核心引擎不绑定任何 agent。
