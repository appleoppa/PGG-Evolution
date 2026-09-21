// probe_plugin_tools.mjs — 插件工具**真加载**探针
//
// 为什么需要它：`ls extensions/*.ts` 只能证明**文件存在**，
// 不能证明工具**真被注册**、更不能证明**真能调用**。
// 本探针用 mock pi 真加载扩展、真调 execute，把「文件存在」升级为「能力可用」。
//
// 用法：node --experimental-strip-types tests/probe_plugin_tools.mjs
// 依赖：node ≥ 22（type stripping）；需要能解析 @earendil-works/*（见下）

import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLUGIN = resolve(HERE, "..", "plugins", "pi", "pgg-self-evolution.ts");

// 期望注册的工具（与引擎 CLI 一一对应）
const EXPECTED = [
  "pgg_self_evolution_health",
  "pgg_self_evolution_shortfall",
  "pgg_self_evolution_route",
];

if (!existsSync(PLUGIN)) {
  console.error(`FAIL: 插件文件不存在: ${PLUGIN}`);
  process.exit(1);
}

// 依赖解析：优先用本地 node_modules；找不到时明确报 SKIP 而不是假通过
let mod;
try {
  mod = await import(pathToFileURL(PLUGIN).href);
} catch (err) {
  const msg = String(err);
  if (msg.includes("Cannot find module") || msg.includes("ERR_MODULE_NOT_FOUND")) {
    console.log(`SKIP: 依赖未解析（${msg.slice(0, 90)}）— 非能力缺失，是环境缺依赖`);
    process.exit(0);
  }
  console.error(`FAIL: 扩展加载抛异常: ${msg.slice(0, 300)}`);
  process.exit(1);
}

if (typeof mod.default !== "function") {
  console.error("FAIL: 扩展未导出 default 函数");
  process.exit(1);
}

const registered = [];
const mockPi = {
  registerTool: (t) => registered.push(t),
  on: () => {},
  registerCommand: () => {},
  registerProvider: () => {},
};

try {
  mod.default(mockPi);
} catch (err) {
  console.error(`FAIL: 扩展初始化抛异常: ${String(err).slice(0, 300)}`);
  process.exit(1);
}

// ① 注册数 > 0（防「加载了但一个工具没注册」）
if (registered.length === 0) {
  console.error("FAIL: 扩展加载成功但未注册任何工具");
  process.exit(1);
}

// ② 每个工具必须有 name / execute
for (const t of registered) {
  if (!t.name || typeof t.execute !== "function") {
    console.error(`FAIL: 工具定义不完整: ${JSON.stringify(Object.keys(t))}`);
    process.exit(1);
  }
}

// ③ 期望工具必须都在
const names = new Set(registered.map((t) => t.name));
const missing = EXPECTED.filter((n) => !names.has(n));
if (missing.length) {
  console.error(`FAIL: 期望工具未注册: ${missing.join(", ")}`);
  process.exit(1);
}

// ④ **真调用**：不是看名字，是看它真返回引擎结果
const byName = Object.fromEntries(registered.map((t) => [t.name, t]));

async function call(toolName, params) {
  const t = byName[toolName];
  const r = await t.execute("probe", params ?? {});
  return String(r?.content?.[0]?.text ?? "");
}

// shortfall：必须返回合法 JSON 且含 defect_rate
const sfRaw = await call("pgg_self_evolution_shortfall");
let sf;
try {
  sf = JSON.parse(sfRaw);
} catch {
  console.error(`FAIL: shortfall 返回非 JSON: ${sfRaw.slice(0, 200)}`);
  process.exit(1);
}
if (typeof sf.defect_rate !== "number" || !sf.dimensions) {
  console.error(`FAIL: shortfall 结果结构不对: ${sfRaw.slice(0, 200)}`);
  process.exit(1);
}

// route：已知信号必须给出路径；空信号必须 NEEDS_SPEC（不得瞎选）
const rOk = JSON.parse(await call("pgg_self_evolution_route", { signals: ["recurring_error"] }));
if (rOk.route !== "P-REPAIR") {
  console.error(`FAIL: recurring_error 应 → P-REPAIR，实得 ${rOk.route}`);
  process.exit(1);
}
const rEmpty = JSON.parse(await call("pgg_self_evolution_route", { signals: [] }));
if (rEmpty.status !== "NEEDS_SPEC") {
  console.error(`FAIL: 空信号应 NEEDS_SPEC（不得默认选一条），实得 ${JSON.stringify(rEmpty)}`);
  process.exit(1);
}
// 未知信号：不得静默丢弃
const rUnknown = JSON.parse(await call("pgg_self_evolution_route", { signals: ["zzz_unknown"] }));
if (!Array.isArray(rUnknown.unknown_signals) || rUnknown.unknown_signals.length !== 1) {
  console.error(`FAIL: 未知信号必须原样列出，实得 ${JSON.stringify(rUnknown)}`);
  process.exit(1);
}
if (rUnknown.route !== null) {
  console.error(`FAIL: 未知信号不得给出路径，实得 ${rUnknown.route}`);
  process.exit(1);
}

console.log(`PASS: 插件工具真加载 + 真调用（注册 ${registered.length} 个；` +
  `shortfall defect_rate=${sf.defect_rate}；route→P-REPAIR；空/未知信号正确拒绝）`);
