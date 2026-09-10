# 证据驱动受控闭环（核心流程）

> 来源：开智进化循环执行规范（2026-06）+ 智能体开智与进化完整成果包（2026-07）
> 这是本方案的工作引擎：**Observe → Diagnose → Propose → Gate → Apply → Evaluate → Record**

## 1. 完整闭环

```
Observe → Diagnose → Propose → Gate → Apply → Evaluate → Record
   │          │          │         │       │        │         │
  发现       根因       候选变更   门禁    人工执行   验证      落盘
  异常       分析       (dry-run)  检查     (备份)    (评测)    (证据)
```

## 2. 每阶段产出

| 阶段 | 做什么 | 必须产出 |
|---|---|---|
| **Observe** | 调用真实工具/系统，记录异常 | 证据文件（facts，含 verified 标记） |
| **Diagnose** | 根因分析，逐条验证（含反证） | 根因清单（区分事实/推断） |
| **Propose** | 候选变更，默认 dry-run | 候选文件（状态 PROPOSED） |
| **Gate** | 5 层门禁检查 | 门禁回执 |
| **Apply** | 人工审批后执行（备份+回滚演练） | 变更记录 |
| **Evaluate** | 冻结评测 + holdout 对照 | 评测运行文件 |
| **Record** | 全部证据/缺口/候选/决策/运行落盘 | 完整目录 |

## 3. 工作区结构（沙箱）

```
loop-<task>/
├── evidence/    # 证据文件（facts + verified 标记）
├── gaps/        # 短板/缺口
├── candidates/  # 候选变更（PROPOSED→EXECUTED）
├── decisions/   # 审批请求 + 决策
└── runs/        # 评测运行记录
```

## 4. 核心原则（来自成果包）

1. **默认只读**：写入限沙箱；生产变更人工审批
2. **备份必做**：每次变更前备份，回滚必演练
3. **硬门不可被分数抵消**：VERIFIED 需 E8 真实任务闭环证据
4. **holdout 分离评测**：warmup（可调优）+ holdout（禁看），只有 holdout 提升才算真进化
5. **禁止**：伪装审计通过、状态字段冒充完成、把模拟当真实部署、文件存在当任务完成

## 5. 完成门禁（7 项全过才算完成）

1. 短板来源可说明（哪次代入/观察暴露的）
2. 外部学习真实（读到原文内容，非搜索摘要）
3. 来源真实（仓库/链接/star 可追溯）
4. 改变动作落地（学习点确实改变了下一步动作）
5. 入库（基因/技能沉淀）
6. 数据库验证（生效确认）
7. 迁移（跨场景复用）

## 6. 闭环分级（风险控制）

| 级别 | 含义 | 要求 |
|---|---|---|
| **green** | 只读/可逆/低风险 | 既定批准范围内运行 |
| **yellow** | 有限写入/外部调用 | 显式审批 + 增强 receipt |
| **red** | 高影响/不可逆 | 禁 auto_act，人工 gate + 双审 |

## 7. 实战示例（试点完整走通）

本地法条检索优化（2026-09）：
1. **Observe**：检索全链路返回 BLOCKED_SOURCE_MANIFEST
2. **Diagnose**：catalog 哈希漂移（manifest 与向量库 metadata 不同步），内容一致性已证明
3. **Propose**：cand-001 哈希同步（官方工具 dry-run 验证）+ cand-002 语义映射
4. **Gate**：默认只读 + 人工审批（苹果哥批准 A/B）
5. **Apply**：manifest 重建 + 向量索引对齐 + 罪名映射优化（全部备份可回滚）
6. **Evaluate**：warmup 0%→75%→100%；**holdout 揭示 100% 是过拟合，真泛化 33.3%**
7. **Record**：全证据链落盘（evidence/candidates/decisions/runs）

## 8. 与远程进化工厂的配合（可选）

本方案可作为**本地调度层**，与远程 GitHub Actions 进化工厂配合：

```
本地（本方案）：Observe→Diagnose→Propose→Gate→Apply→Evaluate→Record
                    │                        │
远程（GitHub工厂）：任务包→Actions容器执行→成果回流→真实性核验→基因沉淀
```

- 本地负责：调度、验收、吸收、评测
- 远程负责：检索、容器执行、自动化验证、成果回流
- 三重门禁（EVM缺陷治理 / 超级路由 / GitHub工厂学习）确保每次进化有真实来源和落地
- 本方案**不依赖任何特定宿主**：任何 agent（Pi/Codex/Claude/DeepSeek 等）都可调用
