# 实战案例：本地法条检索优化（2026-09）

> 这是本方案第一次真实闭环的完整证据链。**诚实地展示了"100% 是过拟合"这一关键教训。**

## 背景

本地法律知识库的罪名映射检索优化。任务：让"案件事实描述 → 法条"的检索更准。

## 完整历程（评测曲线）

| 阶段 | 变更 | warmup(12条) | holdout1(5条) | holdout2(6条) |
|---|---|---|---|---|
| 基线 | 修复前 | 0% | - | - |
| 第一轮 | manifest 重建 + 向量对齐 + 阈值 | 75% | - | - |
| 第二轮 | aliases + 同义词降级 | 100% | - | - |
| holdout 基线 | 建 holdout | 100% | 0% | **33.3%** |
| 补齐 aliases | 人工精选（看失败原因） | 100% | 100% | - |
| **真泛化** | **fuzzy 匹配（通用算法）** | **100%** | - | **100%** |

## 关键教训（本案例的核心价值）

1. **warmup 100% 是过拟合**：对着已知 12 条样例写规则，能刷到 100%
2. **holdout 揭穿真相**：没见过的场景（holdout2）只有 **33.3%**
3. **真进化 = 通用算法**：fuzzy_alias_match（字符序列近似匹配）是通用方法，不是针对样例写规则
4. **holdout 污染**：一旦用 holdout 失败样例调优，它就失去纯净性，需换新批

## 证据文件

| 文件 | 内容 |
|---|---|
| `evidence/frozen-eval-set-001.json` | warmup 冻结集（12条） |
| `evidence/holdout-set-001.json` | 第1批 holdout（已污染） |
| `evidence/holdout-set-002.json` | 第2批纯净 holdout（冻结，未调优） |
| `evidence/evidence-20260910-holdout-reveals-overfit.json` | 100%→0% 揭示证据 |
| `evidence/evidence-20260910-holdout-improvement-chain.json` | 0→60→100 链路 |
| `evidence/evidence-20260910-holdout2-frozen-baseline.json` | 33.3% 冻结基线 |
| `evidence/evidence-20260910-fuzzy-alias-real-generalization.json` | 33.3%→100% 真泛化 |
| `runs/*.json` | 各阶段评测运行原始数据 |
| `run_frozen_eval.py` | 评测 runner（warmup/holdout/holdout2） |

## 复现

```bash
# 需要本地法律知识库（本地优先的法律法规/案例检索库，含罪名别名映射）
# 1. 先建 holdout2 纯净集（evidence/holdout-set-002.json 已含）
# 2. 跑评测
python3 run_frozen_eval.py --set warmup   # 100%
python3 run_frozen_eval.py --set holdout2 # 100%（真泛化）
```
