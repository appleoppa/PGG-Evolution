# PGG-Evolution 自进化方案

> 一个可复用的「智能体自进化」工程方案：把 APEX 公式体系、开智进化循环、EVM 治理
> 整理为可执行的证据驱动受控闭环。**不是玄学公式，是让每次排障变成可复现资产的方法论。**

![version](https://img.shields.io/badge/version-0.2.0-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/appleoppa/PGG-Evolution/actions/workflows/ci.yml/badge.svg)

> **English**: A reusable self-evolution framework for AI agents — APEX formulas + evidence-driven
> closed loop + holdout evaluation. Works with any agent (Pi/Codex/Claude/DeepSeek...).

---

## 这是什么

- **5-6 月**的「自我进化」研究（APEX 公式 + 开智循环 + EVM 治理 + GitHub 进化工厂）
- **2026-09** 首次真实闭环验证（本地法条检索从瘫痪救活到真实泛化 100%）
- 整理成一套 **任何人/任何 agent 都能用的自进化方案**：公式、方法、工具、证据

**核心定位**：它是"带门禁的半自动闭环"，不是"自主进化"。它让 AI 能：
1. **自己发现问题**（Observe→Diagnose，真实工具故障会被发现）
2. **有依据地改进**（Propose→Gate→Apply，人工审批 + 可回滚）
3. **诚实验证**（warmup/holdout 分离评测，只有 holdout 提升才算真进化）

## 快速开始

```bash
# 1. 健康检查
python3 scripts/self_evolve.py --health

# 2. 初始化一个进化循环工作区
python3 scripts/self_evolve.py --init <task_name>

# 3. 三顺序代入找短板（核心方法）
python3 scripts/self_evolve.py --substitute <task_name> --order 21354

# 4. 跑兼容性测试
python3 tests/test_self_evolve.py
```

## 目录结构

```
PGG-Evolution/
├── README.md            # 本文件（快速入门）
├── docs/
│   ├── FORMULAS.md      # APEX 14 维公式 + EVM 治理公式
│   ├── THREE_ORDERS.md  # 三顺序代入方法（21354/12534/14325）
│   ├── LOOP.md          # 证据驱动受控闭环流程（Observe→...→Record）
│   ├── GATES.md         # 五层门禁 + 三重进化门禁
│   └── USAGE.md         # 详细使用说明
├── scripts/
│   └── self_evolve.py   # 自进化引擎（可执行）
├── plugins/
│   └── pi/              # Pi 宿主接入（pgg-self-evolution.ts）
├── tests/
│   └── test_self_evolve.py  # 兼容性测试
├── examples/
│   └── pilot-legal-kb-retrieval/  # 实战案例（完整证据链）
└── evidence/            # 实战证据（评测曲线等）
```

## 为什么值得用

| 传统做法 | 本方案 |
|---|---|
| 修完 bug 说"好了" | 冻结评测 + holdout 验证，有数字 |
| 手写规则永远追不完表述 | 三顺序代入系统性找短板 |
| 报告存在就当完成 | 证据驱动，状态字段 ≠ 完成 |
| 改完怕崩 | 备份 + 人工审批 + 回滚演练 |

## 实战成果（2026-09）

**试点任务：本地法条检索优化**

| 阶段 | 结果 |
|---|---|
| 发现问题 | catalog 哈希漂移导致检索全链路瘫痪（fail-closed） |
| 修复 | manifest 重建 + 向量索引对齐 + 罪名映射 |
| 第一轮评测 | warmup 0% → 75% |
| 第二轮 | warmup → 100%（aliases + 同义词） |
| **holdout 揭示** | **warmup 100% 是过拟合，真泛化仅 33.3%** |
| **真泛化提升** | **fuzzy 匹配 holdout 33.3% → 100%** |

**核心教训**：warmup 100% ≠ 真进化。holdout 分离评测是自进化的诚实底线。

## 安全边界

- 默认只读，写入限沙箱
- 生产变更必须人工审批
- 不写 canonical memory、不改权限/路由/安全策略/凭据
- kill switch：`SELF_EVOLUTION_PLUGIN_DISABLED=1`
- 法律/记忆/路由/进化边界由宿主治理，本方案不自授予

## 设计来源

- APEX 公式体系（`1.Apex仓库/`，2026-05）
- 开智进化循环执行规范（`2.apex公式的使用规范/`，2026-06）
- EVM-Entropy-Vibe-Mathing 仓库（`3.EVM/`，2026-06）
- 智能体开智与进化完整成果包（2026-07）
- 实战验证：本地法条检索优化（2026-09，证据链见 examples/）

## License

MIT — 自由使用，注明来源即可。
