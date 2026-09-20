# 基因 L5 约束层（constraints / validation）

## 为什么加这个

基因库此前只存了「怎么做」（`strategy`/`mechanism`），没存：

- **什么条件下不该用**（`constraints`）
- **怎么验证做对了**（`validation`）

实测本库 53 条基因：`constraints` 显式 **0 条**、`validation` 显式 **0 条**、`preconditions` 只有 5 条。

这是「文件存在冒充能力完成」在基因层的同构缺陷：一条只写了步骤、没写边界和验收的知识，被复用时无法判断是否适用、也无法判断是否生效。

来源：Apex 资源盘《标准基因模版》给出的母体基因 5 层结构：

| 层 | 字段 | 作用 |
|---|---|---|
| L1 元 | id / category | 谁 + 哪个域 |
| L2 触发 | signals_match | 什么信号激活它 |
| L3 前置 | preconditions | 激活前要满足什么 |
| L4 执行 | strategy | 怎么做的步骤序列 |
| L5 约束 | constraints / validation | 边界 + 验证标准 |

## 设计原则：不凭空编造

| 情况 | 处理 |
|---|---|
| 能从已有文本可靠推导 | 推导，并标 `derived_from: text-derivation` |
| 推不出来 | 显式标 `UNSPECIFIED`，不假装有 |
| 基因已显式带 `constraints` | 以显式为准，`derived_from: explicit` |

**关键：推导值不得冒充显式值。** `explicit` 字段如实反映来源。

## 能推导的约束信号（保守，宁少勿错）

| 约束名 | 触发信号 | 含义 |
|---|---|---|
| `rollback_required` | 回滚/备份/.bak | 执行前必须准备可回滚手段 |
| `readback_required` | 读回/验证/确认/实测 | 执行后必须读回验证 |
| `authorization_required` | 授权/审批/人工/门禁 | 涉及门禁项，未授权必须冻结 |
| `sandbox_only` | 沙箱/隔离/staging | 只能在隔离区执行 |
| `no_production_write` | 禁止直写正本/生产 | 不得对正本或生产写入 |

推不出的显式标未定义：
- `environment_scope`（50/53 未定义）—— 环境边界未知，**不得默认它在沙箱内**
- `preconditions`（48/53 未定义）

实测：20/53 条能推出至少一条约束。

## 用法

```bash
# 审计（只读）：逐条推导 + 缺口统计
python3 scripts/self_evolve.py --gene-l5

# 回填推导字段（默认 dry-run，不写盘）
python3 scripts/self_evolve.py --gene-l5-backfill

# 真写盘（需显式 --apply）
python3 scripts/self_evolve.py --gene-l5-backfill --apply
```

**回填只写 `_l5_constraints` / `_l5_validation` / `_l5_unspecified` 派生字段，
不填 `constraints`/`validation` 本体**——缺口保持可见，不掩盖。

## 复用时的警告

`--match` 返回的基因若缺显式 L5，会带：

```json
{
  "l5_warning": "本基因缺显式 L5——复用前必须自己确定：①什么条件下不该用 ②怎么验证做对了",
  "l5_derived": { "constraints": {...}, "unspecified": [...], "validation": [...] }
}
```

并在顶层给 `l5_missing` 计数。**缺 L5 的基因不得静默复用。**

## 边界

- 推导值来自文本规则匹配，**不等于作者本意**。
- 显式覆盖率为 0 是**真实缺口**，不得当已完成。
- 模板推导的验证命令**未与真实产物绑定时，不得声称已通过**。

## 只读模式（吸收自《超级进化21》）

同一份材料主张：`Agent_read ∩ ¬Agent_edit = Max(Safety)`——智能体仅保留配置读取权限。

本引擎沙箱内写是允许的，但提供硬开关把「只读」变成可执行约束：

```bash
PGG_EVOLUTION_READONLY=1 python3 scripts/self_evolve.py --health    # exit=0 放行
PGG_EVOLUTION_READONLY=1 python3 scripts/self_evolve.py --feedback ... # exit=1 READONLY_BLOCKED
```

两层防护：

1. **入口层**：按动作分类拦截，返回 `READONLY_BLOCKED` + `blocked_actions` 清单，exit=1
2. **兜底层**：`safe_write_text()` / `_save_state()` 抛 `ReadOnlyViolation`——**不静默跳过**，防绕过入口

`self_evolve.py` 与 `evolution_units.py` 共用同一环境变量，行为一致。
