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
import { join } from "node:path";
import { homedir } from "node:os";
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const execFileAsync = promisify(execFile);

const PLUGIN_DIR = join(homedir(), ".pi", "agent", "evolution", "plugin-self-evolution");
const ENGINE = join(PLUGIN_DIR, "scripts", "self_evolve.py");
const KILL_SWITCH = "SELF_EVOLUTION_PLUGIN_DISABLED";

function disabled(): boolean {
  return process.env[KILL_SWITCH] === "1";
}

async function runEngine(args: string[]): Promise<string> {
  const { stdout } = await execFileAsync("python3", [ENGINE, ...args], {
    timeout: 30000,
    maxBuffer: 2 * 1024 * 1024,
  });
  return stdout;
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
}
