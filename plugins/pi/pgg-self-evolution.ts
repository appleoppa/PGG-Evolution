// pgg-self-evolution.ts — Pi 原生「自进化闭环」接入层
//
// 定位：把 ~/.pi/agent/evolution/plugin-self-evolution/ 的自进化方案引擎
// （self_evolve.py）注册为 Pi 可用工具。只读 + 沙箱写入，符合插件契约红线。
//
// 授权红线（与 MODULE_PLUGIN_CONTRACT_V0_1 §4 一致）：
//   - 默认 deny：只暴露 health / list-orders / substitute / init（沙箱写）。
//   - 不提供任何生产写权限；生产变更仍需人工审批。
//   - 不写 canonical memory、不改权限/路由/安全策略/scheduler/凭据。
//   - kill switch：SELF_EVOLUTION_PLUGIN_DISABLED=1 时所有工具拒绝执行。
//
// 安装：放入 ~/.pi/agent/extensions/ 自动发现；/reload 或重启 Pi 后生效。
// 引擎：scripts/self_evolve.py（Python，兼容性测试见 tests/）。

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { join, dirname } from "node:path";
import { homedir } from "node:os";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const execFileAsync = promisify(execFile);

// 引擎位置解析（修可移植性缺陷）
//
// 原实现硬编码 `~/.pi/agent/evolution/plugin-self-evolution`，
// 实测后果（CI run 35609238449 真失败）：在没装 Pi 的环境（如 CI 空白机）
// 里那个目录不存在，探针一调就 `python3: can't open file ... No such file`，
// 整个插件挂掉。
//
// 改为多候选依次探测（环境变量优先，便于测试与自定义部署）：
//   ① PGG_SELF_EVOLUTION_DIR（显式指定）
//   ② 宿主装机路径（~/.pi/agent/evolution/plugin-self-evolution）
//   ③ 仓库自身（本文件在 plugins/pi/ 下，上溯两级即仓库根）
// 都找不到时：工具返回 NEEDS_SPEC 而非抛异常（fail-closed 但不崩栈）。
const ENGINE_REL = join("scripts", "self_evolve.py");

function engineCandidates(): string[] {
  const out: string[] = [];
  const envDir = process.env["PGG_SELF_EVOLUTION_DIR"];
  if (envDir) out.push(join(envDir, ENGINE_REL));
  out.push(join(homedir(), ".pi", "agent", "evolution", "plugin-self-evolution", ENGINE_REL));
  // 本文件位于 <repo>/plugins/pi/ → 上溯两级为仓库根
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    out.push(join(here, "..", "..", ENGINE_REL));
  } catch {
    /* import.meta.url 不可用时跳过候选③ */
  }
  return out;
}

function resolveEngine(): string | null {
  for (const p of engineCandidates()) {
    try {
      if (existsSync(p)) return p;
    } catch {
      /* 忽略不可读候选 */
    }
  }
  return null;
}

const ENGINE = resolveEngine();
const UNITS = ENGINE ? join(dirname(ENGINE), "evolution_units.py") : null;
const KILL_SWITCH = "SELF_EVOLUTION_PLUGIN_DISABLED";

function disabled(): boolean {
  return process.env[KILL_SWITCH] === "1";
}

/** 引擎缺失时的统一回复（fail-closed，但不抛异常炸掉整个插件）。 */
function engineMissing(): string {
  return JSON.stringify({
    status: "NEEDS_SPEC",
    reason: "未找到自进化引擎 self_evolve.py",
    searched: engineCandidates(),
    hint: "设置 PGG_SELF_EVOLUTION_DIR 指向仓库根，或确认插件已装入宿主",
  });
}

async function runEngine(args: string[]): Promise<string> {
  if (!ENGINE) return engineMissing();
  const { stdout } = await execFileAsync("python3", [ENGINE, ...args], {
    timeout: 30000,
    maxBuffer: 2 * 1024 * 1024,
  });
  return stdout;
}

async function runUnits(unit: string, action: string, payload: unknown): Promise<string> {
  if (!UNITS) return engineMissing();
  // 非零退出码 = 真拦截；这里把 stdout 原样返回，让调用方看到拒绝理由
  try {
    const { stdout } = await execFileAsync(
      "python3",
      [UNITS, "--unit", unit, "--action", action, "--payload", JSON.stringify(payload ?? {})],
      { timeout: 60000, maxBuffer: 2 * 1024 * 1024 },
    );
    return stdout;
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string };
    if (e.stdout) return e.stdout; // 拒绝类结果也带 JSON，照实回传
    throw err;
  }
}

function result(text: string) {
  return {
    content: [{ type: "text" as const, text }],
    details: { ok: true, engine: "self_evolve.py" },
  };
}

export default function pggSelfEvolution(pi: ExtensionAPI): void {
  // ── 1. 健康检查 ────────────────────────────────────────────────
  const healthTool = defineTool({
    name: "pgg_self_evolution_health",
    label: "自进化方案 · 健康检查",
    description: "自进化闭环方案插件健康检查：引擎是否就绪、沙箱可写、kill switch 状态、三顺序可用性。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--health"]));
    },
  });

  // ── 2. 三顺序列表 ──────────────────────────────────────────────
  const ordersTool = defineTool({
    name: "pgg_self_evolution_orders",
    label: "自进化方案 · 三顺序",
    description: "列出 APEX 三顺序代入路径（21354/12534/14325）及其模块映射。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--list-orders"]));
    },
  });

  // ── 3. 三顺序代入（短板探查）───────────────────────────────────
  const substituteTool = defineTool({
    name: "pgg_self_evolution_substitute",
    label: "自进化方案 · 三顺序代入",
    description: "对真实任务做三顺序代入短板探查：按指定顺序（21354/12534/14325）遍历 APEX 模块，输出每个模块的自查问题。只读（探查结果写入沙箱 runs/）。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务名（如 legal-kb-retrieval）" }),
      order: Type.Union([Type.Literal("21354"), Type.Literal("12534"), Type.Literal("14325")], { description: "三顺序之一" }),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--substitute", params.task, "--order", params.order]));
    },
  });

  // ── 4. 初始化进化循环工作区（沙箱写）────────────────────────────
  const initTool = defineTool({
    name: "pgg_self_evolution_init",
    label: "自进化方案 · 初始化循环",
    description: "在 ~/.pi/agent/evolution/ 沙箱初始化一个进化循环工作区（evidence/gaps/candidates/decisions/runs 五目录）。仅沙箱写入，不碰生产。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务名（如 legal-kb-retrieval）" }),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--init", params.task]));
    },
  });

  pi.registerTool(healthTool);
  pi.registerTool(ordersTool);
  pi.registerTool(substituteTool);
  pi.registerTool(initTool);

  // ── 5. 评测汇总（只读）──────────────────────────────────────
  const evalTool = defineTool({
    name: "pgg_self_evolution_eval",
    label: "自进化方案 · 评测汇总",
    description: "评测汇总：读 loop-<task>/runs/ 下的评测运行文件统计命中率；warmup 可调优，holdout 提升才算真泛化。只读。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务名（如 legal-kb-retrieval）" }),
      set: Type.Optional(Type.Union([Type.Literal("warmup"), Type.Literal("holdout"), Type.Literal("holdout2"), Type.Literal("all")], { description: "评测集合，默认 all" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--eval", params.task];
      if (params.set && params.set !== "all") args.push("--set", params.set);
      return result(await runEngine(args));
    },
  });

  // ── 6. 14 维模块列表（只读）────────────────────────────────────
  const dimsTool = defineTool({
    name: "pgg_self_evolution_dimensions",
    label: "自进化方案 · 14 维模块",
    description: "列出 APEX 14 维模块（D1-D14）及其公式与含义。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--list-dimensions"]));
    },
  });

  pi.registerTool(evalTool);
  pi.registerTool(dimsTool);

  // ── 7. 进化基因沉淀（D12 元学习，沙箱写）─────────────────────────
  const geneTool = defineTool({
    name: "pgg_self_evolution_gene",
    label: "自进化方案 · 基因沉淀",
    description: "进化基因沉淀：从 loop-<task>/ 的 evidence/gaps/candidates 提取经验基因（短板模式+修复动作），写入沙箱基因库供跨任务复用（D12 元学习）。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务名（如 legal-kb-retrieval）" }),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--gene", params.task]));
    },
  });

  pi.registerTool(geneTool);

  // ── 8. LLM 三顺序代入（走白名单凭据桥，失败降级）─────────────────
  const llmTool = defineTool({
    name: "pgg_self_evolution_llm_substitute",
    label: "自进化方案 · LLM 三顺序代入",
    description: "LLM 三顺序代入：让 LLM 结合任务内容分析每个模块的真实短板（比规则模板更深刻）。密钥走白名单凭据桥，只进内存不落盘；LLM 失败自动降级规则模式（fail-closed）。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务描述（如 local-kb-retrieval 优化）" }),
      order: Type.Union([Type.Literal("21354"), Type.Literal("12534"), Type.Literal("14325")], { description: "三顺序之一" }),
      provider: Type.Optional(Type.String({ description: "LLM provider（默认 deepseek-v4-flash）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--llm", params.task, "--order", params.order, "--llm-provider", params.provider || "deepseek-v4-flash"];
      return result(await runEngine(args));
    },
  });

  pi.registerTool(llmTool);

  // ── 9. LLM 基因生成（打通基因库↔记忆颗粒）────────────────────────
  const geneLlmTool = defineTool({
    name: "pgg_self_evolution_gene_llm",
    label: "自进化方案 · LLM 基因生成",
    description: "LLM 基因生成：把短板教训自动总结成结构化可复用基因（signals_match/strategy 格式，对齐 hermes 基因库），供程序匹配执行。密钥走白名单凭据桥。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务名（如 self-evolution-plugin-self-improve）" }),
      provider: Type.Optional(Type.String({ description: "LLM provider（默认 deepseek-v4-flash）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--gene-llm", params.task, "--llm-provider", params.provider || "deepseek-v4-flash"];
      return result(await runEngine(args));
    },
  });

  pi.registerTool(geneLlmTool);

  // ── 10. 基因匹配复用（D12 元学习闭环）────────────────────────────
  const matchTool = defineTool({
    name: "pgg_self_evolution_match",
    label: "自进化方案 · 基因匹配",
    description: "基因匹配复用：新任务描述匹配历史基因（signals_match/strategy），供跨任务复用。只读。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务描述（如 本地检索系统故障）" }),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--match", params.task]));
    },
  });

  // ── 11. 双向写回 A 向：基因→记忆颗粒 ────────────────────────────
  const geneSyncTool = defineTool({
    name: "pgg_self_evolution_gene_sync",
    label: "自进化方案 · 基因→记忆",
    description: "双向写回 A 向：基因→记忆颗粒（生成结算文档，走标准记忆管线 STAGING→审批→向量化）。沙箱写。",
    parameters: Type.Object({
      task: Type.String({ minLength: 1, description: "任务名（如 self-evolution-plugin-self-improve）" }),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--gene-sync", params.task]));
    },
  });

  // ── 12. 双向写回 B 向：记忆颗粒→基因 ────────────────────────────
  const geneFromMemoryTool = defineTool({
    name: "pgg_self_evolution_gene_from_memory",
    label: "自进化方案 · 记忆→基因",
    description: "双向写回 B 向：从记忆库检索经验，LLM 提炼为可复用基因。只读记忆库，密钥走凭据桥。",
    parameters: Type.Object({
      topic: Type.String({ minLength: 1, description: "检索主题（如 自进化）" }),
      provider: Type.Optional(Type.String({ description: "LLM provider（默认 deepseek-v4-flash）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--gene-from-memory", params.topic, "--llm-provider", params.provider || "deepseek-v4-flash"];
      return result(await runEngine(args));
    },
  });

  pi.registerTool(matchTool);
  pi.registerTool(geneSyncTool);
  pi.registerTool(geneFromMemoryTool);

  // ── 13. Ψ 深度健康监测（基因库/记忆库/沙箱/反馈完整性）──────────────
  const healthDeepTool = defineTool({
    name: "pgg_self_evolution_health_deep",
    label: "自进化方案 · Ψ 深度健康监测",
    description: "Ψ 深度健康监测：基因库文件完整性、基因 schema 合法性、记忆库连通性、沙箱可写性、kill switch 与反馈状态。输出 actionable repair_hint + retest 命令，与 ε 自修复形成 监测→修复→复检 闭环。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--health-deep"]));
    },
  });

  // ── 14. Φ 记录基因复用反馈 ─────────────────────────────────────
  const feedbackTool = defineTool({
    name: "pgg_self_evolution_feedback",
    label: "自进化方案 · 记录复用反馈",
    description: "Φ 记录基因复用反馈：success（复用有效）或 failure（复用无效/误导）。写入沙箱 feedback.json，供淘汰决策。",
    parameters: Type.Object({
      task_desc: Type.String({ minLength: 1, description: "任务描述（如 本地检索优化）" }),
      outcome: Type.Union([Type.Literal("success"), Type.Literal("failure")], { description: "复用结果" }),
      gene_id: Type.Optional(Type.String({ description: "被复用基因 id（可省略）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--feedback", params.task_desc, params.outcome, params.gene_id || "-"];
      return result(await runEngine(args));
    },
  });

  // ── 15. Φ 反馈统计 ─────────────────────────────────────────────
  const feedbackStatsTool = defineTool({
    name: "pgg_self_evolution_feedback_stats",
    label: "自进化方案 · 反馈统计",
    description: "Φ 反馈统计：按基因/总体统计复用成功率，供淘汰决策（failure_rate 超阈值可 --prune-genes 淘汰）。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--feedback-stats"]));
    },
  });

  // ── 16. Φ 淘汰无效基因 ─────────────────────────────────────────
  const pruneTool = defineTool({
    name: "pgg_self_evolution_prune",
    label: "自进化方案 · 淘汰无效基因",
    description: "Φ 淘汰无效基因：复用失败率 ≥ 阈值（默认 0.5）且样本 ≥ 2 的基因标记 deprecated。淘汰只写 deprecated.json（运行时过滤），不改原始基因文件，可回滚。",
    parameters: Type.Object({
      threshold: Type.Optional(Type.Number({ description: "失败率阈值，默认 0.5" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--prune-genes"];
      if (params.threshold !== undefined) args.push(String(params.threshold));
      return result(await runEngine(args));
    },
  });

  // ── 17. Λ 统一状态入口 ──────────────────────────────────────────
  const statusTool = defineTool({
    name: "pgg_self_evolution_status",
    label: "自进化方案 · 统一状态",
    description: "Λ_ctx 统一状态入口：健康 + 基因库 + 反馈统计 一处汇总（切换损耗收敛）。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--status"]));
    },
  });

  pi.registerTool(healthDeepTool);
  pi.registerTool(feedbackTool);
  pi.registerTool(feedbackStatsTool);
  pi.registerTool(pruneTool);
  pi.registerTool(statusTool);

  // ── 18. 证据等级登记（E0-E9 诚实性内核）────────────────────────
  const evidenceTool = defineTool({
    name: "pgg_self_evolution_evidence",
    label: "自进化方案 · 登记证据等级",
    description:
      "登记一条主张的证据等级（E0-E9）。等级不够会被拒（谎报工件/跳级/夸大词/空文件均拦截）。" +
      "E0 需 note 主张原文；E1/E4/E7/E8/E9 需 artifact；E2 需 artifact+note 符号名；" +
      "E3/E5/E6 需 artifact+verify_cmd，且要 execute=true 才真跑命令。沙箱写。",
    parameters: Type.Object({
      claim_id: Type.String({ description: "主张 id，如 CLM-001" }),
      level: Type.String({ description: "证据等级 E0-E9" }),
      artifact: Type.Optional(Type.String({ description: "证据工件路径（E1-E9）" })),
      verify_cmd: Type.Optional(Type.String({ description: "E3/E5/E6 的可执行校验命令" })),
      execute: Type.Optional(Type.Boolean({ description: "显式授权真跑校验命令（默认 false）" })),
      note: Type.Optional(Type.String({ description: "E0 主张文本 / E2 符号名 / 备注" })),
      claim_text: Type.Optional(Type.String({ description: "主张原文（夸大词扫描用）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--evidence", params.claim_id, "--level", params.level];
      if (params.artifact) args.push("--artifact", params.artifact);
      if (params.verify_cmd) args.push("--verify-cmd", params.verify_cmd);
      if (params.execute) args.push("--execute");
      if (params.note) args.push("--note", params.note);
      if (params.claim_text) args.push("--claim-text", params.claim_text);
      return result(await runEngine(args));
    },
  });

  // ── 19. 证据链读回（派生允许的状态词）────────────────────────
  const evidenceStatusTool = defineTool({
    name: "pgg_self_evolution_evidence_status",
    label: "自进化方案 · 读回证据链",
    description:
      "读回证据链：重新校验每条证据，按前置依赖算最高可支撑等级，派生**允许的**状态词与禁用表述。" +
      "不带 execute 时命令类证据（E3/E5/E6）保守失效；工件被删即降级。只读。",
    parameters: Type.Object({
      claim_id: Type.Optional(Type.String({ description: "主张 id（省略=全部）" })),
      execute: Type.Optional(Type.Boolean({ description: "重跑命令类证据（默认 false）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const args = ["--evidence-status"];
      if (params.claim_id) args.push(params.claim_id);
      if (params.execute) args.push("--execute");
      return result(await runEngine(args));
    },
  });

  pi.registerTool(evidenceTool);
  pi.registerTool(evidenceStatusTool);

  // ── 20. F1-F7 控制单元（状态机 + 布尔硬门）───────────────────
  const unitsTool = defineTool({
    name: "pgg_evolution_units",
    label: "进化控制单元 · F1-F7",
    description:
      "调用 F1-F7 可执行控制单元（状态机 + 布尔硬门）。unit 取 F1-F7，action 取该单元动作" +
      "（如 F1.create_gap/propose/verify/release，F2.plan/run，F3.record/compare/total_score，" +
      "F4.route/memory_check，F5.dag_validate/ready/correlation/adjudicate，" +
      "F6.propose/screen/approve/activate/deprecate，F7.evaluate/release/readback）。" +
      "payload 为该动作的 JSON 参数。拒绝类结果会带 status=BLOCKED/NEEDS_SPEC 并说明理由" +
      "（例：F3.total_score 永远 BLOCKED——不同量纲不得相加）。沙箱写。",
    parameters: Type.Object({
      unit: Type.String({ description: "控制单元 F1-F7" }),
      action: Type.String({ description: "单元内动作，如 create_gap / total_score / correlation" }),
      payload: Type.Optional(Type.String({ description: "JSON 参数（对象）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      let parsed: unknown = {};
      if (params.payload) {
        try {
          parsed = JSON.parse(params.payload);
        } catch {
          return result(JSON.stringify({ status: "NEEDS_SPEC", reason: "payload 非法 JSON" }));
        }
      }
      return result(await runUnits(params.unit, params.action, parsed));
    },
  });

  const unitsListTool = defineTool({
    name: "pgg_evolution_units_list",
    label: "进化控制单元 · 清单",
    description: "列出 F1-F7 七个控制单元及其动作。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const { stdout } = await execFileAsync("python3", [UNITS, "--list"], {
        timeout: 30000, maxBuffer: 1024 * 1024,
      });
      return result(stdout);
    },
  });

  pi.registerTool(unitsTool);
  pi.registerTool(unitsListTool);

  const geneL5Tool = defineTool({
    name: "pgg_evolution_gene_l5",
    label: "基因 L5 约束层 · 审计",
    description:
      "基因库 L5 约束层审计：逐条推导 constraints/validation 并统计显式覆盖率缺口。" +
      "只读，不写盘。用于发现「基因只存了怎么做、没存什么条件下别用」的缺口。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      if (!ENGINE) return result(engineMissing());
      const { stdout } = await execFileAsync("python3", [ENGINE, "--gene-l5"], {
        timeout: 60000, maxBuffer: 8 * 1024 * 1024,
      });
      return result(stdout);
    },
  });

  pi.registerTool(geneL5Tool);

  const claimScanTool = defineTool({
    name: "pgg_evolution_claim_scan",
    label: "汇报文本扫描 · D07 排除词表",
    description:
      "扫描汇报/结算/主张文本，判定能否作为「现状/能力/授权」结论。" +
      "基于 D07 排除词表（7 类）+ D05 §6.1 未接线清单（8 类）。" +
      "只扫描，不改写被扫文本。违规时 exit=1。",
    parameters: Type.Object({
      text: Type.Optional(Type.String({ description: "直接传入要扫描的文本" })),
      file: Type.Optional(Type.String({ description: "要扫描的文件路径（与 text 二选一）" })),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      if (!params.text && !params.file) {
        return result(JSON.stringify({ status: "NEEDS_SPEC", reason: "需提供 text 或 file" }));
      }
      if (!ENGINE) return result(engineMissing());
      const args = params.file ? [ENGINE, "--claim-file", params.file] : [ENGINE, "--claim-scan", params.text];
      const { stdout } = await execFileAsync("python3", args, {
        timeout: 60000, maxBuffer: 8 * 1024 * 1024,
      });
      return result(stdout);
    },
  });

  // ── 22. 系统短板体检（缺陷率接进决策路径）──────────────────────
  const shortfallTool = defineTool({
    name: "pgg_self_evolution_shortfall",
    label: "自进化方案 · 系统短板体检",
    description:
      "把实测缺口喂进缺陷率公式（最大短板非线性惩罚），给出单一可比较的短板指标" +
      "与「最该修哪个」。取不到数据的维度计 1.0 而非丢弃（没测≠没毛病）；" +
      "总分**不得用于提权**（优先级仍看 R0-R4 与平台/法律硬约束）。只读。",
    parameters: Type.Object({}),
    async execute(_toolCallId) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      return result(await runEngine(["--shortfall"]));
    },
  });

  // ── 23. 机遇信号 → 进化路径选择（任务分发）──────────────────────
  const routeTool = defineTool({
    name: "pgg_self_evolution_route",
    label: "自进化方案 · 选进化路径",
    description:
      "按机会信号选进化路径（APEX-EVOLUTION-ROUTE §1.1）：" +
      "P-OPTIMIZE/P-REPAIR/P-INNOVATE/P-EXPLORE/P-CURRICULUM。" +
      "**无命中时 route=null（不默认选一条）**；平票时 ambiguous=true" +
      "（不武断取第一个）；未知信号原样列出不静默丢弃。只读。",
    parameters: Type.Object({
      signals: Type.Array(Type.String(), {
        description: "机会信号键数组，如 [\"recurring_error\"] 或 [\"perf_bottleneck\",\"stable_success_plateau\"]",
      }),
    }),
    async execute(_toolCallId, params) {
      if (disabled()) return result(JSON.stringify({ status: "DISABLED", reason: `${KILL_SWITCH}=1` }));
      const sigs = (params.signals ?? []).map((s: string) => String(s).trim()).filter(Boolean);
      if (!sigs.length) {
        return result(JSON.stringify({ status: "NEEDS_SPEC", reason: "需提供至少一个机会信号" }));
      }
      return result(await runEngine(["--route", sigs.join(",")]));
    },
  });

  pi.registerTool(shortfallTool);
  pi.registerTool(routeTool);

  pi.registerTool(claimScanTool);
}
