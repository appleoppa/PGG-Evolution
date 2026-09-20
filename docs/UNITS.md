# F1-F7 控制单元：把统一进化公式变成会拒绝的代码

> 来源：开智与进化成果包 D02《统一进化公式与工程解释》
> 定位：七个可执行的**状态机 + 布尔硬门**。规格文本不会拒绝你，代码会。
> 引擎：`scripts/evolution_units.py`　测试：`tests/test_evolution_units.py`（31 项）

## 1. 为什么不是又一份文档

D02 原文是 `CANDIDATE` 规格（作者自己标注"不是当前运行能力声明"）。规格的问题在于：
**它只能被"读"，不能被"违反时拒绝"**。本实现把每条硬规则变成返回值：

| 你想偷的懒 | 代码怎么拒绝 |
|---|---|
| 缺字段就默认通过 | `NEEDS_SPEC`（D02 §1.1：无字段不得用默认值补齐） |
| 用高分抵消安全门 | `BLOCKED`（硬门是布尔合取，分数不可折抵） |
| 无限自动迭代 | `NEEDS_SPEC`（必须给 n_max/t_max/b_max/r_max） |
| 未运行的测试当通过 | 缺 `outcome` 一律记 `not_run`，永不算 pass |
| 把不同量纲加成总分 | `BLOCKED`（拒绝"总进化分"，D02 §4） |
| child 结束就报完成 | `WATCH`（child completion ≠ parent receipt） |
| 自己批准自己的变更 | `BLOCKED`（approver 不得等于 owner） |
| 阶段门不全就发布 | `NOT_READY`（不是"默认通过"） |
| 外部仓库直接吸收 | `BLOCKED`（缺来源/许可/去重筛查） |
| 高风险任务自动路由 | `BLOCKED`（转人工） |

## 2. 共同契约

**统一状态词**（D02 §1.1，登记状态，不表示能力等级）：
`NEEDS_SPEC` `NEEDS_EVIDENCE` `PROPOSED` `EXPERIMENTING` `REVIEW`
`APPROVED` `ACTIVE` `REJECTED` `DEPRECATED` `ROLLED_BACK`

**布尔硬门**（D02 §1.2）：

```text
HardGate(c) = identity_ok ∧ scope_ok ∧ permission_ok ∧ data_policy_ok
              ∧ security_ok ∧ legal_review_ok(若适用) ∧ rollback_ready
```

- 缺字段 → `NEEDS_SPEC`（**不得**以默认值补齐）
- 有 false → `BLOCKED`（**不得**被分数/投票/置信/fitness 折抵）
- `legal_review_ok` 不适用时**也必须记录理由**，不得静默跳过

## 3. 七个单元

### F1 — LDR(K) → GapDetect → 候选变更状态机

```text
TASK → RETRIEVE(q,K) → EVIDENCE(E)
EVIDENCE(E) ∧ Reproduce(expected,actual) → GAP(g)
GAP(g) ∧ MinimalDiff(c) ∧ HardGate(c) → EXPERIMENTING(c)
EXPERIMENTING(c) ∧ Verify=pass ∧ A(c)=granted → APPROVED(c)
APPROVED(c) ∧ ReleaseReadback=pass → ACTIVE(c), K'
```

- `create_gap`：缺 `expected/actual/reproduction/severity` → `NEEDS_EVIDENCE`
  （没有最小复现的条目只能是研究假设）
- `propose`：缺最小 diff/测试计划/回滚/owner/风险 → `NEEDS_SPEC`
- `verify`：验证通过但无授权 → 停在 `REVIEW`（不得自动 `APPROVED`）；
  `approver == owner` → `BLOCKED`（自证无效）
- `release`：回读失败 → `ROLLED_BACK`；成功才 `ACTIVE` 并沉淀 `K'`
  （`K'` 只存经验证事实、适用条件、失败边界）

### F2 — Ralph/Harness 有界修复与验证状态机

- 必须有 `n_max / t_max_s / b_max / r_max`，缺一 → `NEEDS_SPEC`（不得无限自迭代）
- `failure_class=unknown` → `BLOCKED`（低置信不得自动改）
- 阻断级失败 → **立即** `STOP_AND_ESCALATE`，不等上限
- 验证矩阵每项只能 `pass/fail/not_applicable/not_run`；缺 `outcome` 记 `not_run`
- 达任一上限 → 恢复 `Ω_t` 快照并升级人工
- `exec_matrix` 需显式授权才真跑命令

### F3 — ΔG/EVM 缺陷账本与候选排序

- **拒绝生成"总进化分"**：不同量纲不得相加相乘（这是刻意的反模式闸门）
- `record`：只接受同量纲差分；方向按 `better` 归一为改善量
- `compare`：硬门与质量不可劣化是**前置布尔资格**，不参与打分排序；
  排序用字典序 `(risk_rank, -quality, cost, latency)` + 帕累托前沿
- 缺值按 `false` 处理；无合格候选 → 不强行选择，转人工

### F4 — Select–Read–Act / 记忆 / 路由

```text
Feasible(s) = {a | schema ∧ permission ∧ data_domain ∧ quota ∧ budget ∧ risk}
Eligible(s) = {a ∈ Feasible | quality_noninferiority = true}
a* = lexicographic_min(risk_rank, estimated_cost, estimated_latency)
```

- 约束字段缺失 → 该路径不入选；`Feasible` 为空 → 转人工
- 高风险任务 → 不得自动路由
- 校准置信低于阈值 → 转人工
- `memory_check`：长期记忆缺 `source_ref/data_class/acl/ttl/delete_path` → `BLOCKED`

### F5 — 多智能体 DAG 与多模型裁决

- `dag_validate`：必须无环；节点缺 `owner/contract/timeout/retry/budget` → `NEEDS_SPEC`；
  共享写入/发布/权限/高风险工具节点必须显式依赖审批节点
- `correlation`：按同一 `correlation_id` 对账
  `dispatch → start → tool → completion → receipt`
  - **child 完成但无 parent receipt → `WATCH`**（不得称任务已完成）
  - 无 child start → `WATCH`（拒绝或重派，不无限等待）
- `adjudicate`：`m_eff < m_min` 或 `d ≥ d_threshold` 或高风险 → `HUMAN`
  （保留分歧与出处，不多数表决掩盖）

### F6 — 进化队列与资产生命周期

```text
PROPOSED → Source/License/Dedup Screen → SCREENED
→ 隔离评测 + 独立审查 + HardGate + 授权 → APPROVED → ACTIVE
ACTIVE + regression|expiry|withdrawal → DEPRECATED
```

- `PROPOSED` 直跳 `ACTIVE` → `BLOCKED`
- 缺 `source/version/license/dedup_key` → 不得 `SCREENED`（不得直接"吞噬"）
- 重复资产 → 只关联主条目，不重复计权
- `ACTIVE` 前必须有 `baseline/tests/owner/rollback/expiry`，缺一即拒

### F7 — 质量门、发布判决与运行回读

七阶段发布门 `RS1-RS7`：

```text
RS1 需求与风险 → RS2 可测试计划 → RS3 隔离实现 → RS4 独立审查
→ RS5 验证/重放 → RS6 发布判决与授权 → RS7 运行回读/复盘

Release(c) ⇔ ∧(适用的 G_i = pass) ∧ HardGate(c) ∧ A_u ∧ RollbackDrill(R)=pass
```

- 任一门不过 → `NOT_READY`（**不是**"默认通过"）
- `generator == verifier` → `NOT_READY`（自证无效）
- 缺授权 / 回滚演练未过 → `NOT_READY`
- `readback` 异常 → `ROLLBACK_REQUIRED`

## 4. 用法

```bash
# 列出全部单元与动作
python3 scripts/evolution_units.py --list

# 读回沙箱状态
python3 scripts/evolution_units.py --show-state

# 调用（payload 为 JSON；拒绝类结果 exit=1，便于 CI/脚本判真拦）
python3 scripts/evolution_units.py --unit F3 --action total_score --payload '{}'
# → BLOCKED, exit=1

python3 scripts/evolution_units.py --unit F1 --action create_gap \
  --payload '{"gap_id":"G1","expected":"待确认","actual":"空字段","reproduction":"min.sh","severity":"med"}'

# 大 payload 用文件
python3 scripts/evolution_units.py --unit F7 --action release --payload-file /tmp/rel.json
```

沙箱状态落 `~/.pi/agent/evolution/units/state.json`（可删可回滚）。

## 5. 与其余三层的关系

| 层 | 文件 | 管什么 |
|---|---|---|
| 门禁 | `docs/GATES.md` | 变更**能不能落**（备份/白名单/diff/密钥/危险模式） |
| 证据账本 | `docs/EVIDENCE.md` | 主张**凭什么说**（E0-E9 证据够不够） |
| 控制单元 | 本文件 | 流程**按什么状态机走**（F1-F7 状态转移 + 硬门） |
| 公式 | `docs/FORMULAS.md` | 14 维**诊断排序**（辅助，不是判据） |

四层互补：门禁防乱改，账本防谎报，控制单元防跳步，公式辅助诊断。

## 6. 禁止跳步清单（D02 §10，全部已实现为拒绝）

1. 检索 ≠ 缺口成立 → 无复现只能是研究假设
2. 候选 ≠ 已写入 → 一切改动先是候选
3. 分数 ≠ 验证/授权 → 拒绝伪总分
4. 多模型 ≠ 独立/正确 → 高分歧升级人工
5. 记忆/归档 ≠ 真相/永久 → 需来源/ACL/TTL/删除
6. 沙箱通过 ≠ 生产可用 → 需授权与回滚演练
7. 外部来源 ≠ 可吸收能力 → 需来源/许可/去重筛查
8. **child completion ≠ parent receipt** → 缺环标 `WATCH`
9. 交付文档 ≠ 实现事实 → 本实现自身也须按证据链核验
