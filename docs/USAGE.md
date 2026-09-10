# 详细使用说明

## 1. 环境要求

- Python 3.10+
- macOS / Linux / Windows（路径会自动调整）

## 2. 安装

```bash
# 直接 clone 或下载
git clone https://github.com/appleoppa/PGG-Evolution.git
cd PGG-Evolution

# 无第三方依赖（纯标准库）
```

## 3. 基础使用

```bash
# 健康检查：确认引擎就绪
python3 scripts/self_evolve.py --health

# 列出三顺序
python3 scripts/self_evolve.py --list-orders

# 初始化进化循环工作区（在 ~/.pi/agent/evolution/ 沙箱）
python3 scripts/self_evolve.py --init <task_name>

# 三顺序代入找短板（核心方法）
python3 scripts/self_evolve.py --substitute <task_name> --order 21354
python3 scripts/self_evolve.py --substitute <task_name> --order 12534
python3 scripts/self_evolve.py --substitute <task_name> --order 14325

# 运行兼容性测试
python3 tests/test_self_evolve.py
```

## 4. 完整自进化流程（示例）

### 场景：优化一个检索系统

```bash
# 1. 初始化工作区
python3 scripts/self_evolve.py --init legal-kb-retrieval

# 2. 三顺序代入找短板（三条路径都跑）
python3 scripts/self_evolve.py --substitute legal-kb-retrieval --order 21354
python3 scripts/self_evolve.py --substitute legal-kb-retrieval --order 12534
python3 scripts/self_evolve.py --substitute legal-kb-retrieval --order 14325

# 3. 对暴露的短板：
#    - Observe：记录真实异常（证据）
#    - Diagnose：根因分析（含反证）
#    - Propose：候选变更（dry-run）
#    - Gate：五层门禁
#    - Apply：人工审批后执行（备份+回滚）
#    - Evaluate：冻结评测 warmup + holdout
#    - Record：全部落盘
```

### 评测纪律（最重要）

```bash
# warmup（已知样例，可调优）— 调优用
python3 scripts/self_evolve.py --eval <task> --set warmup

# holdout（未见样例，禁看）— 验证真泛化
python3 scripts/self_evolve.py --eval <task> --set holdout
```

**铁律**：
- warmup 提升 ≠ 真进化（可能是过拟合）
- 只有 **holdout 提升** 才算真泛化证据
- holdout 一旦被调优看过，就必须换新一批

## 5. 通用接入（推荐，任何宿主）

核心引擎 `scripts/self_evolve.py` 是**纯 Python 标准库、零依赖、不绑定任何 agent**：

```bash
# 任何宿主都能直接用（bash/工具调用）
python3 scripts/self_evolve.py --health
python3 scripts/self_evolve.py --status
```

- **Codex / Claude / DeepSeek / 其他 agent**：直接用工具/bash 调用 CLI
- **远程 CI / 容器**：把脚本放进 workflow，无需宿主持有
- **无宿主**：作为独立脚本/技能使用

## 6. 宿主接入示例（以 Pi 为例）

> 这不是绑定：Pi 只是**一个演示如何接入**的例子。任何宿主都可以用 §5 的通用方式，
> 或仿照 `plugins/pi/pgg-self-evolution.ts` 写自己的适配器。

把 `plugins/pi/pgg-self-evolution.ts` 复制到 `~/.pi/agent/extensions/`，然后 `/reload` 或重启 Pi。
注册后可用工具（17 个）：
- `pgg_self_evolution_health` — 健康检查（引擎就绪/kill switch）
- `pgg_self_evolution_orders` — 三顺序列表
- `pgg_self_evolution_substitute` — 三顺序代入（短板探查）
- `pgg_self_evolution_init` — 初始化进化循环工作区（五目录）
- `pgg_self_evolution_eval` — 评测汇总（warmup/holdout 分离）
- `pgg_self_evolution_dimensions` — 14 维模块列表
- `pgg_self_evolution_gene` — 进化基因沉淀（规则提取）
- `pgg_self_evolution_llm_substitute` — LLM 三顺序代入（失败降级规则）
- `pgg_self_evolution_gene_llm` — LLM 基因生成
- `pgg_self_evolution_match` — 基因匹配复用（默认过滤已淘汰基因）
- `pgg_self_evolution_gene_sync` — 双向写回 A 向（基因→记忆颗粒）
- `pgg_self_evolution_gene_from_memory` — 双向写回 B 向（记忆→基因）
- `pgg_self_evolution_health_deep` — Ψ 深度健康监测（基因库/记忆库/沙箱/反馈完整性，含 repair_hint ε 闭环）
- `pgg_self_evolution_feedback` — Φ 记录复用反馈（success/failure）
- `pgg_self_evolution_feedback_stats` — Φ 反馈统计（复用成功率）
- `pgg_self_evolution_prune` — Φ 淘汰无效基因（失败率≥阈值标记 deprecated，可回滚）
- `pgg_self_evolution_status` — Λ 统一状态入口（健康+基因+反馈一处汇总）

引擎直接调用：`python3 scripts/self_evolve.py --health-deep / --feedback ... / --prune-genes / --status`（--help 见全部 CLI）

## 7. 禁用 / 卸载

```bash
# 禁用（kill switch）
export SELF_EVOLUTION_PLUGIN_DISABLED=1

# 卸载
rm -rf PGG-Evolution/
# 无残留：不写 canonical memory、不改生产配置
```

## 8. 常见问题

**Q: 三顺序是什么顺序？**
A: 不是流程步骤。是 APEX 公式模块编号（1=Θ 2=K 3=ε 4=Φ 5=Ψ）的代入遍历路径。
详见 docs/THREE_ORDERS.md。

**Q: 为什么 warmup 100% 还不够？**
A: 因为对着已知样例写规则可以刷到 100%（过拟合），但没见过的新场景可能全错。
holdout 是冻结的、没看过的样例，只有它提升才是真泛化。

**Q: 这个方案能自动进化吗？**
A: 不能完全自主。它是"带门禁的半自动闭环"：发现问题✅自动，改进✅有人工审批，验证✅诚实。
不宣称 full AGI、不宣称模型权重已改变、不把样例 100% 说成泛化 100%。

**Q: 会不会碰我的生产系统？**
A: 不会。默认只读 + 沙箱写入 + 人工审批 + kill switch。红线永不放开。
