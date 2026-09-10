#!/usr/bin/env python3
"""自进化闭环方案 — 最小可执行引擎（self-evolution plugin v0.1）。

用法见 README.md。默认只读/沙箱；生产写入需人工审批（本脚本不提供生产写权限）。

安全边界：
- 所有写入限 ~/.pi/agent/evolution/（沙箱）
- kill switch: SELF_EVOLUTION_PLUGIN_DISABLED=1 时拒绝运行
- 红线：不写 canonical memory、不改生产配置、不碰权限/路由/安全策略
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
SANDBOX = Path.home() / ".pi" / "agent" / "evolution"

KILL_SWITCH = "SELF_EVOLUTION_PLUGIN_DISABLED"

MODELS_JSON = Path.home() / ".pi" / "agent" / "models.json"

# APEX 14 维模块（三顺序代入用）
DIMENSIONS = {
    "1": {"name": "Θ", "label": "LLM效能", "formula": "λ×μ×σ/(γ+1)"},
    "2": {"name": "K", "label": "技能掌握", "formula": "K_code×(1+Στ)×υ"},
    "3": {"name": "ε", "label": "自修复", "formula": "1+|Gt-Ga|/Ga×δ×ψ×κ"},
    "4": {"name": "Φ", "label": "正反馈", "formula": "e^(ηρ)"},
    "5": {"name": "Ψ", "label": "健康状态", "formula": "Ψm×Ψa×Ψd×Ω"},
    "6": {"name": "Π", "label": "并行增益", "formula": "1+(N-1)×eff"},
    "7": {"name": "PID", "label": "稳定性", "formula": "1/(1+Kp·e+Ki·∫e+Kd·de/dt)"},
    "8": {"name": "率失真", "label": "信息效率", "formula": "log2(1+S/N)"},
    "9": {"name": "Kelly", "label": "风险控制", "formula": "(bp-q)/b×(1-ruin)"},
    "10": {"name": "E_xp", "label": "探索-利用", "formula": "ln(t)/(1+ΔK)×α"},
    "11": {"name": "Γ", "label": "多Agent博弈", "formula": "SWR·β+(1-β)/N"},
    "12": {"name": "M_meta", "label": "元学习", "formula": "1+(ΔK/Δt/K)×ln(1+E)"},
    "13": {"name": "Λ_ctx", "label": "切换损耗", "formula": "e^(-λ·Ns/Nt)"},
    "14": {"name": "Ξ", "label": "创造力", "formula": "Nnov/N·Vnov/V·(1+E·M)"},
}

# 三顺序：模块编号代入路径（核心方法）
THREE_ORDERS = {
    "21354": ["2", "1", "3", "5", "4"],
    "12534": ["1", "2", "5", "3", "4"],
    "14325": ["1", "4", "3", "2", "5"],
}

# 每个模块的短板自查问题（代入用）
DIMENSION_PROBES = {
    "1": "当前任务的 LLM 推理/判断环节是否真实接入？还是只靠规则？",
    "2": "调工具/系统前是否做了状态预检？技能掌握有没有盲区？",
    "3": "系统有没有自修复工具？回路是否自动接通？还是工具存在但没触发？",
    "4": "当前评测分数是否可能过拟合（对着已知样例调参）？有没有 holdout？",
    "5": "系统健康是否可观测？故障有没有报警？",
    "6": "是否有并行/批量增益可用？串行拖后腿了吗？",
    "7": "变更是否可能过调/震荡？有没有 PID 式稳定性控制？",
    "8": "信息传递是否高效？有没有压缩/去冗余空间？",
    "9": "投入是否超出可承受风险？Kelly 最优投入比算了吗？",
    "10": "是在利用已知，还是也在探索新路径？比例合理吗？",
    "11": "多 Agent/多模型协作时有没有博弈损耗？Γ 值低吗？",
    "12": "这次任务的方法能否沉淀为元学习（下次更快）？",
    "13": "频繁切换上下文/工具了吗？切换损耗大吗？",
    "14": "有没有引入新组合/新方法？创造力维度被忽略了吗？",
}


def _correlation_id() -> str:
    return f"se-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"


def _load_llm_provider(provider: str) -> dict:
    """从 models.json 读 provider 配置 + 白名单凭据桥取真密钥。

    密钥只进进程内存，不打印、不落盘、不写仓库。
    provider 映射到白名单 key：sol→GPT_5_6_SOL_API_KEY, terra→GPT_5_6_TERRA_API_KEY,
    deepseek-v4-flash→DEEPSEEK_V4_FLASH_API_KEY, maoge-dp4→MAOGE_DP4_API_KEY, senseaudio→SENSEAUDIO_API_KEY。
    """
    if not MODELS_JSON.exists():
        return {"error": f"models.json 不存在: {MODELS_JSON}"}
    try:
        data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        providers = data.get("providers", {})
    except Exception as e:
        return {"error": f"models.json 解析失败: {e}"}
    cfg = providers.get(provider)
    if not cfg:
        return {"error": f"provider '{provider}' 不存在，可选: {list(providers.keys())}"}
    if not cfg.get("baseUrl"):
        return {"error": f"provider '{provider}' 缺 baseUrl"}

    # 通过白名单凭据桥取真密钥（密钥只在内存，不打印）
    bridge = Path.home() / ".pi" / "agent" / "bin" / "hermes-env-key"
    key_name = {
        "sol": "GPT_5_6_SOL_API_KEY",
        "terra": "GPT_5_6_TERRA_API_KEY",
        "deepseek-v4-flash": "DEEPSEEK_V4_FLASH_API_KEY",
        "maoge-dp4": "MAOGE_DP4_API_KEY",
        "senseaudio": "SENSEAUDIO_API_KEY",
    }.get(provider)
    if not key_name or not bridge.exists():
        return {"error": f"provider '{provider}' 无凭据桥映射（需人工配置）"}
    import subprocess
    try:
        r = subprocess.run([str(bridge), key_name], capture_output=True, text=True, timeout=10)
        if r.returncode != 0 or not r.stdout.strip():
            return {"error": f"凭据桥拒绝: {r.stderr.strip()[:100]}"}
        api_key = r.stdout.strip()
    except Exception as e:
        return {"error": f"凭据桥调用失败: {e}"}

    return {
        "baseUrl": cfg["baseUrl"],
        "apiKey": api_key,
        "api": cfg.get("api", "openai-completions"),
        "models": cfg.get("models", []),
    }


def _llm_chat(provider: str, system: str, user: str, model: str | None = None, timeout: int = 30) -> dict:
    """调用 OpenAI 兼容 LLM（urllib 零依赖）。失败降级：返回 error 由调用方 fallback。"""
    p = _load_llm_provider(provider)
    if "error" in p:
        return p
    if not p["models"]:
        return {"error": f"provider '{provider}' 无可用模型"}
    mdl = model or p["models"][0].get("id", "")
    base = p["baseUrl"].rstrip("/")
    api = p["api"]
    # baseUrl 可能已含 /v1（如 https://5yuantoken.org/v1），避免重复拼接
    v1 = "/v1" if not base.endswith("/v1") else ""
    if api == "openai-responses":
        url = f"{base}{v1}/responses"
        payload = {"model": mdl, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    else:  # openai-completions
        url = f"{base}{v1}/chat/completions"
        payload = {"model": mdl, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {p['apiKey']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        if api == "openai-responses":
            text = body.get("output_text", "")
        else:
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "text": text.strip(), "model": mdl}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": f"调用失败: {e}"}


def llm_substitute(task_name: str, order: str, provider: str, model: str | None = None) -> dict:
    """LLM 三顺序代入：让 LLM 结合任务内容分析每个模块的真实短板。

    fail-closed：LLM 失败时降级回规则模式（DIMENSION_PROBES 模板）。
    """
    if order not in THREE_ORDERS:
        return {"status": "BLOCKED", "reason": f"无效顺序 {order}"}
    path_str = "→".join(DIMENSIONS[d]["name"] for d in THREE_ORDERS[order])
    system = "你是自进化诊断助手。根据任务描述，按给定模块顺序分析每个模块的真实短板。"
    user = (
        f"任务: {task_name}\n"
        f"三顺序代入路径: {order} ({path_str})\n\n"
        "对每个模块输出 JSON: {\"module\": \"X\", \"shortboard\": \"具体短板或'无'\", \"evidence\": \"证据或'无'\"}\n"
        "模块含义: " + "; ".join(f"{d}={DIMENSIONS[d]['label']}" for d in THREE_ORDERS[order])
    )
    resp = _llm_chat(provider, system, user, model)
    if "error" in resp:
        return {"status": "DEGRADED", "reason": f"LLM 不可用({resp['error']})，降级规则模式", "fallback": True, "probes": [
            {"order_pos": d, "module": DIMENSIONS[d]["name"], "label": DIMENSIONS[d]["label"], "formula": DIMENSIONS[d]["formula"], "probe": DIMENSION_PROBES[d]}
            for d in THREE_ORDERS[order]
        ]}
    return {"status": "OK", "provider": provider, "model": resp["model"], "task": task_name, "order": order, "path": path_str, "llm_analysis": resp["text"], "note": "LLM 分析结果需人工复核真实性"}


def health_check() -> dict:
    return {
        "status": "OK",
        "plugin": "self-evolution",
        "version": "0.1.0",
        "sandbox": str(SANDBOX),
        "sandbox_writable": os.access(SANDBOX, os.W_OK),
        "kill_switch": KILL_SWITCH,
        "kill_switch_active": os.environ.get(KILL_SWITCH) == "1",
        "three_orders": list(THREE_ORDERS.keys()),
        "dimensions": len(DIMENSIONS),
        "contract": "pgg-module-plugin/v1",
    }


def _load_feedback() -> dict:
    """加载复用反馈库（Φ 正反馈）。沙箱根 feedback.json，结构含 events 列表。"""
    fb_file = SANDBOX / "feedback.json"
    if not fb_file.exists():
        return {"schema": "pgg-evolution/feedback/v1", "updated_at": "", "events": []}
    try:
        return json.loads(fb_file.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": "pgg-evolution/feedback/v1", "updated_at": "", "events": [], "corrupt": str(fb_file)}


def _save_feedback(fb: dict) -> None:
    fb["schema"] = "pgg-evolution/feedback/v1"
    fb["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (SANDBOX / "feedback.json").write_text(json.dumps(fb, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_deprecated_ids() -> set:
    """加载已淘汰基因 id 集合（Φ 淘汰运行时过滤，不改原始基因文件，可回滚）。"""
    dep_file = SANDBOX / "genes" / "deprecated.json"
    if not dep_file.exists():
        return set()
    try:
        d = json.loads(dep_file.read_text(encoding="utf-8"))
        return {x.get("gene_id") for x in d.get("deprecated", []) if x.get("gene_id")}
    except Exception:
        return set()


def health_deep(check_memory: bool = True) -> dict:
    """Ψ 深度健康监测：基因库完整性、基因 schema 合法性、记忆库连通性、沙箱可写性、反馈状态。

    输出 actionable repair_hint + retest 命令，与 ε 自修复形成 监测→修复→复检 闭环。
    """
    checks = {}
    issues = []

    # 1. 沙箱可写性
    sandbox_ok = os.access(SANDBOX, os.W_OK)
    checks["sandbox_writable"] = {"ok": sandbox_ok, "path": str(SANDBOX)}
    if not sandbox_ok:
        issues.append("沙箱不可写（~/.pi/agent/evolution）")

    # 2. 基因库目录与文件
    genes_dir = SANDBOX / "genes"
    gene_files = sorted(genes_dir.glob("genes-*.json")) if genes_dir.is_dir() else []
    checks["genes_dir"] = {"ok": len(gene_files) > 0, "files": len(gene_files), "path": str(genes_dir)}
    if not gene_files:
        issues.append("基因库为空（先跑 --gene 或 --gene-llm 沉淀）")

    # 3. 基因 schema 完整性
    broken = []
    total_genes = 0
    for f in gene_files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            broken.append(f"{f.name}: JSON 解析失败")
            continue
        if d.get("schema") != "pgg-evolution/gene-bank/v1":
            broken.append(f"{f.name}: schema 非 pgg-evolution/gene-bank/v1")
        genes = d.get("genes")
        if not isinstance(genes, list):
            broken.append(f"{f.name}: genes 非列表")
            continue
        if d.get("gene_count") != len(genes):
            broken.append(f"{f.name}: gene_count({d.get('gene_count')}) 与实际({len(genes)}) 不符")
        for i, g in enumerate(genes):
            if not isinstance(g, dict) or not g:
                broken.append(f"{f.name}: 第{i}个基因为空")
        total_genes += len(genes)
    genes_ok = not broken and len(gene_files) > 0
    checks["genes_integrity"] = {"ok": genes_ok, "files": len(gene_files), "total_genes": total_genes, "broken": broken}
    for b in broken[:5]:
        issues.append(f"基因库损坏: {b}")

    # 4. 记忆库连通性（check_memory=False 时跳过，供 CI/无记忆库环境）
    db_path = Path.home() / "PGG-WIKI" / "brain.sqlite3"
    mem_ok = not check_memory
    approved = 0
    mem_detail = "skipped (check_memory=False)" if not check_memory else f"db_missing:{not db_path.exists()}"
    if check_memory and db_path.exists():
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM assets WHERE status='APPROVED'")
            approved = cur.fetchone()[0]
            con.close()
            mem_ok = True
            mem_detail = f"approved_granules={approved}"
        except Exception as e:
            mem_detail = f"query_failed:{e}"
    checks["memory_db"] = {"ok": mem_ok, "path": str(db_path), "approved_granules": approved, "detail": mem_detail}
    if not mem_ok:
        issues.append(f"记忆库连通性异常: {mem_detail}")

    # 5. kill switch
    ks_active = os.environ.get(KILL_SWITCH) == "1"
    checks["kill_switch"] = {"ok": not ks_active, "active": ks_active, "env": KILL_SWITCH}
    if ks_active:
        issues.append(f"kill switch 已激活（{KILL_SWITCH}=1）")

    # 6. 反馈状态
    fb = _load_feedback()
    events = fb.get("events", [])
    checks["feedback"] = {"ok": not fb.get("corrupt"), "events": len(events), "corrupt": fb.get("corrupt", "")}
    if fb.get("corrupt"):
        issues.append(f"反馈库损坏: {fb['corrupt']}")

    if not issues:
        status = "OK"
        repair_hint = "无异常，无需修复"
    elif len(issues) <= 2:
        status = "DEGRADED"
        repair_hint = "；".join(issues) + "。可执行修复：修复对应文件/目录后重跑 --health-deep 复检"
    else:
        status = "CRITICAL"
        repair_hint = "；".join(issues) + "。需人工介入，修复后重跑 --health-deep 复检"

    return {
        "status": status,
        "plugin": "self-evolution",
        "checks": checks,
        "issues": issues,
        "repair_hint": repair_hint,
        "retest": "python3 scripts/self_evolve.py --health-deep",
        "note": "Ψ 健康监测：任何 ok=false 需修复后复检；监测→修复→复检闭环（ε）",
    }


def feedback_record(task_desc: str, outcome: str, gene_id: str | None = None, note: str = "") -> dict:
    """Φ 记录基因复用反馈：success（复用有效）或 failure（复用无效/误导）。"""
    if outcome not in ("success", "failure"):
        return {"status": "BLOCKED", "reason": f"无效 outcome: {outcome}，可选 success/failure"}
    fb = _load_feedback()
    events = fb.get("events", [])
    events.append({
        "id": f"fb-{time.strftime('%Y%m%d%H%M%S')}-{len(events) + 1}",
        "task_desc": task_desc,
        "gene_id": gene_id or "",
        "outcome": outcome,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": note,
    })
    fb["events"] = events
    _save_feedback(fb)
    return {"status": "OK", "recorded": events[-1], "event_count": len(events), "file": str(SANDBOX / "feedback.json")}


def feedback_stats() -> dict:
    """Φ 反馈统计：按基因/总体统计复用成功率，供淘汰决策。"""
    fb = _load_feedback()
    events = fb.get("events", [])
    if fb.get("corrupt"):
        return {"status": "DEGRADED", "reason": f"反馈库损坏: {fb['corrupt']}", "events": events}
    total = len(events)
    success = sum(1 for e in events if e.get("outcome") == "success")
    failure = sum(1 for e in events if e.get("outcome") == "failure")
    by_gene: dict = {}
    for e in events:
        gid = e.get("gene_id") or "(未关联)"
        d = by_gene.setdefault(gid, {"success": 0, "failure": 0})
        d[e.get("outcome")] = d.get(e.get("outcome"), 0) + 1
    per_gene = [
        {"gene_id": gid, "success": v["success"], "failure": v["failure"],
         "failure_rate": round(v["failure"] / (v["success"] + v["failure"]), 3) if (v["success"] + v["failure"]) else 0.0}
        for gid, v in sorted(by_gene.items(), key=lambda x: -(x[1]["failure"] / max(1, x[1]["success"] + x[1]["failure"])))
    ]
    return {
        "status": "OK",
        "total_events": total,
        "success": success,
        "failure": failure,
        "success_rate": round(success / total, 3) if total else 0.0,
        "per_gene": per_gene,
        "file": str(SANDBOX / "feedback.json"),
        "note": "failure_rate 超阈值可 --prune-genes 淘汰",
    }


def prune_genes(threshold: float = 0.5, min_samples: int = 2) -> dict:
    """Φ 淘汰无效基因：复用失败率 ≥ threshold 且样本 ≥ min_samples 的基因标记 deprecated。

    淘汰只写 deprecated.json（运行时过滤），不改原始基因文件，可回滚。
    """
    fb = _load_feedback()
    events = fb.get("events", [])
    if fb.get("corrupt"):
        return {"status": "DEGRADED", "reason": f"反馈库损坏: {fb['corrupt']}", "deprecated": []}
    by_gene: dict = {}
    for e in events:
        gid = e.get("gene_id")
        if not gid:
            continue
        d = by_gene.setdefault(gid, {"success": 0, "failure": 0})
        d[e.get("outcome")] = d.get(e.get("outcome"), 0) + 1
    candidates = []
    for gid, v in by_gene.items():
        total = v["success"] + v["failure"]
        if total < min_samples:
            continue
        failure_rate = v["failure"] / total
        if failure_rate >= threshold:
            candidates.append({"gene_id": gid, "failure_rate": round(failure_rate, 3), "samples": total})

    dep_file = SANDBOX / "genes" / "deprecated.json"
    existing = _load_deprecated_ids()
    new_deprecated = [c for c in candidates if c["gene_id"] not in existing]
    dep_payload = {
        "schema": "pgg-evolution/deprecated/v1",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "deprecated": [
            {"gene_id": c["gene_id"], "failure_rate": c["failure_rate"], "samples": c["samples"],
             "reason": f"复用失败率 {c['failure_rate']} ≥ 阈值 {threshold}（{c['samples']} 样本）", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            for c in candidates
        ]
        + [{"gene_id": gid, "reason": "历史标记", "at": ""} for gid in existing],
    }
    dep_file.parent.mkdir(parents=True, exist_ok=True)
    dep_file.write_text(json.dumps(dep_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "OK",
        "threshold": threshold,
        "min_samples": min_samples,
        "newly_deprecated": [c["gene_id"] for c in new_deprecated],
        "total_deprecated": len(dep_payload["deprecated"]),
        "file": str(dep_file),
        "note": "淘汰为运行时过滤（deprecated.json），原始基因文件保留，可回滚",
    }


def status_summary() -> dict:
    """Λ_ctx 统一状态入口：健康 + 基因库 + 反馈统计 一处汇总。"""
    h = health_deep()
    fb = _load_feedback()
    events = fb.get("events", [])
    success = sum(1 for e in events if e.get("outcome") == "success")
    failure = sum(1 for e in events if e.get("outcome") == "failure")
    genes_dir = SANDBOX / "genes"
    gene_files = sorted(genes_dir.glob("genes-*.json")) if genes_dir.is_dir() else []
    total_genes = 0
    for f in gene_files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            total_genes += len(d.get("genes", []))
        except Exception:
            pass
    return {
        "status": h["status"],
        "plugin": "self-evolution",
        "health": {k: v.get("ok") for k, v in h["checks"].items()},
        "genes": {"files": len(gene_files), "total": total_genes, "deprecated": len(_load_deprecated_ids())},
        "feedback": {"events": len(events), "success": success, "failure": failure,
                      "success_rate": round(success / len(events), 3) if events else 0.0},
        "repair_hint": h["repair_hint"],
        "note": "统一状态入口：健康/基因/反馈一处可查（Λ_ctx 切换损耗收敛）",
    }


def init_workspace(task_name: str) -> dict:
    """初始化一个进化循环工作区（Observe/Diagnose/Propose/Gate/Apply/Evaluate/Record）。"""
    base = SANDBOX / f"loop-{task_name}"
    for sub in ("evidence", "gaps", "candidates", "decisions", "runs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    meta = {
        "task": task_name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stages": ["Observe", "Diagnose", "Propose", "Gate", "Apply", "Evaluate", "Record"],
        "dirs": ["evidence", "gaps", "candidates", "decisions", "runs"],
    }
    (base / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "OK", "workspace": str(base), "meta": meta}


def substitute(task_name: str, order: str) -> dict:
    """三顺序代入：按指定顺序遍历模块，输出每个模块的自查问题（短板探查）。"""
    if order not in THREE_ORDERS:
        return {"status": "BLOCKED", "reason": f"无效顺序 {order}，可选: {list(THREE_ORDERS)}"}
    probes = []
    for dim_id in THREE_ORDERS[order]:
        dim = DIMENSIONS[dim_id]
        probes.append({
            "order_pos": dim_id,
            "module": dim["name"],
            "label": dim["label"],
            "formula": dim["formula"],
            "probe": DIMENSION_PROBES[dim_id],
        })
    # 记录到沙箱（只读探查结果，可写沙箱）
    rec = {
        "correlation_id": _correlation_id(),
        "task": task_name,
        "order": order,
        "path": "→".join(DIMENSIONS[d]["name"] for d in THREE_ORDERS[order]),
        "probes": probes,
        "note": "每个 probe 需在真实任务上回答，暴露短板；短板记录到 gaps/ 供后续补齐",
    }
    return {"status": "OK", "substitute": rec}


def eval_runs(task_name: str, set_name: str | None = None) -> dict:
    """评测汇总：读取 loop-<task>/runs/ 下的评测运行文件，统计命中率。

    set_name 可选 warmup/holdout/holdout2/all（默认 all）。
    从 run 文件中的 results[].sample_id / summary 统计。
    """
    runs_dir = SANDBOX / f"loop-{task_name}" / "runs"
    if not runs_dir.is_dir():
        return {"status": "BLOCKED", "reason": f"无评测目录: {runs_dir}"}
    files = sorted(runs_dir.glob("*.json"))
    if not files:
        return {"status": "BLOCKED", "reason": "runs/ 下无评测文件（先跑 run_frozen_eval.py 生成）"}

    summary = {}
    samples = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_set = data.get("set", data.get("eval_set", ""))
        if set_name and set_name != "all":
            if run_set not in (set_name, "all") and not (set_name == "warmup" and run_set in ("", "warmup")):
                continue
        s = data.get("summary", {})
        if not s:
            continue
        for k, v in s.items():
            if not isinstance(v, dict) or "rate" not in v:
                continue
            key = f"{run_set or 'all'}:{k}"
            cur = summary.get(key, {"hit": 0, "total": 0, "best_rate": 0.0})
            cur["hit"] = max(cur["hit"], v.get("hit", 0))
            cur["total"] = max(cur["total"], v.get("total", 0))
            cur["best_rate"] = max(cur["best_rate"], v.get("rate", 0.0))
            summary[key] = cur
        samples.append({"file": f.name, "set": run_set})

    return {
        "status": "OK",
        "task": task_name,
        "set": set_name or "all",
        "runs": len(samples),
        "files": [s["file"] for s in samples],
        "summary": {k: {"hit": v["hit"], "total": v["total"], "best_rate": round(v["best_rate"], 3)} for k, v in summary.items()},
        "note": "best_rate 为各 run 文件中的最佳命中率；warmup 可调优，holdout 提升才算真泛化",
    }


def collect_genes(task_name: str) -> dict:
    """进化基因沉淀（D12 元学习）：从 loop-<task>/ 的 evidence/gaps 提取经验基因。

    基因 = 短板模式 + 修复动作 + 可复用规则。写入沙箱 genes/ 供跨任务复用。
    """
    loop_dir = SANDBOX / f"loop-{task_name}"
    if not loop_dir.is_dir():
        return {"status": "BLOCKED", "reason": f"无工作区: {loop_dir}"}

    genes = []
    # 从 gaps/ 提取短板
    gaps_dir = loop_dir / "gaps"
    if gaps_dir.is_dir():
        for f in sorted(gaps_dir.glob("*.json")):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            # 兼容单对象或数组（防御性：历史数据可能是列表）
            items = g if isinstance(g, list) else [g]
            for gi in items:
                if gi.get("shortboard"):
                    genes.append({
                        "type": "gap",
                        "source": f.name,
                        "module": gi.get("module", ""),
                        "mechanism": gi["shortboard"],
                        "resolution": gi.get("resolution", ""),
                    })

    # 从 candidates/ 提取修复动作（EXECUTED）
    cand_dir = loop_dir / "candidates"
    if cand_dir.is_dir():
        for f in sorted(cand_dir.glob("*.json")):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = c if isinstance(c, list) else [c]
            for ci in items:
                if ci.get("status") == "EXECUTED" and ci.get("change"):
                    changes = ci["change"] if isinstance(ci["change"], list) else [ci["change"]]
                    genes.append({
                        "type": "fix",
                        "source": f.name,
                        "mechanism": "; ".join(str(x) for x in changes),
                        "risk": ci.get("risk", ""),
                    })

    # 从 evidence/ 提取已核验事实（闭环的核心经验）
    ev_dir = loop_dir / "evidence"
    if ev_dir.is_dir():
        for f in sorted(ev_dir.glob("*.json")):
            try:
                ev = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = ev if isinstance(ev, list) else [ev]
            for evi in items:
                for fact in evi.get("facts", []):
                    if fact.get("verified") and fact.get("claim"):
                        genes.append({
                            "type": "lesson",
                            "source": f.name,
                            "mechanism": fact["claim"],
                            "inference": bool(fact.get("inference", False)),
                        })

    # 写入基因库（沙箱 genes/，按任务聚合）
    genes_dir = SANDBOX / "genes"
    genes_dir.mkdir(parents=True, exist_ok=True)
    gene_file = genes_dir / f"genes-{task_name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "schema": "pgg-evolution/gene-bank/v1",
        "task": task_name,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gene_count": len(genes),
        "genes": genes,
        "note": "基因=短板模式+修复动作，供跨任务复用（D12 元学习）",
    }
    gene_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "OK", "task": task_name, "gene_count": len(genes), "gene_file": str(gene_file), "genes": genes}


def gene_llm(task_name: str, provider: str, model: str | None = None) -> dict:
    """LLM 基因生成：把 collect_genes 提取的原始基因交给 LLM，总结成结构化可复用基因。

    输出含 signals_match/strategy/preconditions（对齐 hermes 基因格式），写入沙箱 genes/。
    fail-closed：LLM 失败时返回 collect_genes 结果（原始基因），不阻塞。
    """
    raw = collect_genes(task_name)
    if raw.get("status") != "OK" or not raw.get("genes"):
        return raw

    raw_text = json.dumps([{k: g[k] for k in ("type", "mechanism", "source") if k in g} for g in raw["genes"]], ensure_ascii=False, indent=2)
    system = "你是进化基因提炼器。把原始短板/教训总结成结构化可复用基因。"
    user = (
        f"任务: {task_name}\n原始基因: {raw_text}\n\n"
        "对每条输出 JSON 基因: {\"id\": \"gene_<category>_<slug>\", \"category\": \"repair/prevent/optimize/learn\", \"signals_match\": [\"触发信号\"], \"preconditions\": [\"前置条件\"], \"strategy\": [\"修复步骤\"], \"mechanism\": \"一句话可复用规则\"}\n"
        "要求：具体可执行、跨任务可复用、中文、每条 ≤ 6 个信号/步骤。"
    )
    resp = _llm_chat(provider, system, user, model)
    if "error" in resp:
        return {**raw, "status": "DEGRADED", "reason": f"LLM 不可用({resp['error']})，保留原始基因", "llm": False}

    import re
    text = resp["text"]
    llm_genes = []
    try:
        # 提取 JSON 数组（LLM 可能包在 ```json 里）
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            arr = json.loads(m.group(0))
            llm_genes = arr if isinstance(arr, list) else [arr]
    except Exception:
        llm_genes = []
    if not llm_genes:
        return {**raw, "status": "DEGRADED", "reason": "LLM 输出无法解析为基因，保留原始基因", "llm": False}

    genes_dir = SANDBOX / "genes"
    genes_dir.mkdir(parents=True, exist_ok=True)
    gene_file = genes_dir / f"genes-llm-{task_name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "schema": "pgg-evolution/gene-bank/v1",
        "generated_by": "llm",
        "provider": provider,
        "model": resp["model"],
        "task": task_name,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gene_count": len(llm_genes),
        "genes": llm_genes,
        "note": "LLM 生成的可复用基因（需人工复核后入库）",
    }
    gene_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "OK", "task": task_name, "gene_count": len(llm_genes), "gene_file": str(gene_file), "genes": llm_genes, "llm": True, "provider": provider, "model": resp["model"]}


def _load_gene_bank() -> list[dict]:
    """加载沙箱基因库全部基因（含 LLM 生成与规则提取）。

    已淘汰基因（deprecated.json 记录）标记 _deprecated=True，仍保留在库中供追溯。
    """
    genes_dir = SANDBOX / "genes"
    if not genes_dir.is_dir():
        return []
    deprecated = _load_deprecated_ids()
    all_genes = []
    for f in sorted(genes_dir.glob("genes-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for g in d.get("genes", []):
            if isinstance(g, dict) and (g.get("mechanism") or g.get("strategy")):
                g["_source"] = f.name
                g["_deprecated"] = g.get("id") in deprecated
                all_genes.append(g)
    return all_genes


def match_genes(task_desc: str, top_n: int = 3, include_deprecated: bool = False) -> dict:
    """基因匹配复用（D12 元学习闭环）：新任务描述匹配历史基因。

    简单关键词/维度名匹配（signals_match + mechanism + id），返回 top 可复用基因。
    默认排除已淘汰基因（Φ 反馈淘汰的运行时过滤）。
    """
    genes = _load_gene_bank()
    if not genes:
        return {"status": "BLOCKED", "reason": "基因库为空（先跑 --gene 或 --gene-llm）"}
    if not include_deprecated:
        genes = [g for g in genes if not g.get("_deprecated")]
        if not genes:
            return {"status": "OK", "task": task_desc, "matched": 0, "genes": [], "note": "可用基因全被淘汰，可新建闭环或 --include-deprecated 查看历史"}
    scored = []
    for g in genes:
        score = 0
        haystack = " ".join([g.get("id", ""), g.get("category", ""), g.get("mechanism", ""), " ".join(g.get("signals_match", [])), " ".join(g.get("strategy", []))])
        # 任务描述分词（简单切词，中文按 2-gram）
        words = set()
        for w in re.findall(r"[\u4e00-\u9fff]{2,}", task_desc):
            words.add(w)
            if len(w) >= 4:
                for i in range(len(w) - 1):
                    words.add(w[i:i+2])
        for w in words:
            if w in haystack:
                score += 1
        scored.append((score, g))
    scored.sort(key=lambda x: -x[0])
    top = [g for s, g in scored if s > 0][:top_n]
    if not top:
        return {"status": "OK", "task": task_desc, "matched": 0, "genes": [], "note": "无匹配基因，可新建闭环"}
    return {"status": "OK", "task": task_desc, "matched": len(top), "genes": [
        {k: g[k] for k in ("id", "category", "mechanism", "signals_match", "strategy", "_source") if k in g} for g in top
    ], "note": "匹配基因供复用：参考 strategy 修复步骤，勿机械照搬（需人工复核）"}


def gene_sync(task_name: str) -> dict:
    """双向写回 A 向：基因→记忆颗粒。

    把 loop-<task>/ 的基因沉淀为结算文档（~/.pi/agent/archives/），
    供 pgg-brain-stage-archives.py 颗粒化→审批→向量化（基因经验进记忆系统可检索）。
    """
    loop_dir = SANDBOX / f"loop-{task_name}"
    if not loop_dir.is_dir():
        return {"status": "BLOCKED", "reason": f"无工作区: {loop_dir}"}
    genes = _load_gene_bank()
    task_genes = [g for g in genes if task_name in g.get("_source", "")]
    if not task_genes:
        return {"status": "BLOCKED", "reason": f"任务 {task_name} 无基因可同步（先跑 --gene 或 --gene-llm）"}

    archives_dir = Path.home() / ".pi" / "agent" / "archives"
    archives_dir.mkdir(parents=True, exist_ok=True)
    doc_path = archives_dir / f"settlement-gene-sync-{task_name}-{time.strftime('%Y%m%d')}.md"
    lines = [
        f"# 基因同步结算：{task_name}（{time.strftime('%Y-%m-%d')}）",
        "",
        "## 一句话总结",
        f"进化闭环 {task_name} 沉淀的可复用基因，同步入记忆系统（双向写回 A 向）。",
        "",
        "## 基因清单",
        "",
    ]
    for i, g in enumerate(task_genes, 1):
        lines.append(f"{i}. **{g.get('id', f'gene-{i}')}**（{g.get('category', 'learn')}）：{g.get('mechanism', '')}")
        sig = g.get("signals_match", [])
        if sig:
            lines.append(f"   - 触发信号：{'；'.join(sig[:3])}")
        strat = g.get("strategy", [])
        if strat:
            lines.append(f"   - 修复策略：{'；'.join(strat[:3])}")
        lines.append("")
    lines.append("## 记忆回流说明")
    lines.append("本文档由 pgg_self_evolution 基因同步生成，走 STAGING→审批→向量化标准管线。")
    doc_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "OK", "task": task_name, "gene_count": len(task_genes), "sync_doc": str(doc_path), "next": "跑 pgg-brain-stage-archives.py 颗粒化→operator 审批→embed 向量化"}


def gene_from_memory(topic: str, provider: str = "deepseek-v4-flash", model: str | None = None) -> dict:
    """双向写回 B 向：记忆颗粒→基因。

    从 brain.sqlite3 检索与主题相关的 APPROVED 记忆颗粒，交给 LLM 提炼为可复用基因。
    只读记忆库（不写 canonical），fail-closed：LLM 失败返回原始记忆摘要。
    """
    db_path = Path.home() / "PGG-WIKI" / "brain.sqlite3"
    if not db_path.exists():
        return {"status": "BLOCKED", "reason": f"记忆库不存在: {db_path}"}
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        # 搜 APPROVED 颗粒（assets 表）
        cur.execute(
            "SELECT stable_id, title, body FROM assets WHERE status='APPROVED' AND (title LIKE ? OR body LIKE ?) LIMIT 10",
            (f"%{topic}%", f"%{topic}%")
        )
        rows = cur.fetchall()
        con.close()
    except Exception as e:
        return {"status": "BLOCKED", "reason": f"记忆库查询失败: {e}"}
    if not rows:
        return {"status": "OK", "matched_memory": 0, "genes": [], "note": f"记忆库无 '{topic}' 相关 APPROVED 颗粒"}

    mem_text = "\n".join(f"[{r[0]}] {r[1]}: {r[2][:200]}" for r in rows)
    system = "你是进化基因提炼器。从记忆颗粒（经验总结）提炼可复用基因。"
    user = (
        f"主题: {topic}\n记忆颗粒: {mem_text}\n\n"
        "输出 JSON 基因数组: [{\"id\": \"gene_<category>_<slug>\", \"category\": \"repair/prevent/optimize/learn\", \"signals_match\": [\"触发信号\"], \"strategy\": [\"修复步骤\"], \"mechanism\": \"一句话规则\"}]\n"
        "要求：从这些经验中提炼可复用规则，具体可执行，中文。"
    )
    resp = _llm_chat(provider, system, user, model)
    if "error" in resp:
        return {"status": "DEGRADED", "reason": f"LLM 不可用({resp['error']})，返回记忆摘要", "matched_memory": len(rows), "memory": [r[2][:200] for r in rows], "llm": False}
    text = resp["text"]
    llm_genes = []
    try:
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            arr = json.loads(m.group(0))
            llm_genes = arr if isinstance(arr, list) else [arr]
    except Exception:
        llm_genes = []
    if not llm_genes:
        return {"status": "DEGRADED", "reason": "LLM 输出无法解析为基因", "matched_memory": len(rows), "llm": False}

    genes_dir = SANDBOX / "genes"
    genes_dir.mkdir(parents=True, exist_ok=True)
    gene_file = genes_dir / f"genes-from-memory-{topic[:20]}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "schema": "pgg-evolution/gene-bank/v1",
        "generated_by": "llm-from-memory",
        "topic": topic,
        "matched_memory": len(rows),
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gene_count": len(llm_genes),
        "genes": llm_genes,
        "note": "从记忆颗粒提炼的基因（B 向写回，需人工复核）",
    }
    gene_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "OK", "task": topic, "matched_memory": len(rows), "gene_count": len(llm_genes), "gene_file": str(gene_file), "genes": llm_genes, "llm": True}


def main() -> int:
    if os.environ.get(KILL_SWITCH) == "1":
        print(json.dumps({"status": "DISABLED", "reason": f"{KILL_SWITCH}=1"}, ensure_ascii=False))
        return 3

    ap = argparse.ArgumentParser(description="自进化闭环方案引擎")
    ap.add_argument("--health", action="store_true", help="健康检查")
    ap.add_argument("--init", metavar="TASK", help="初始化进化循环工作区")
    ap.add_argument("--substitute", metavar="TASK", help="三顺序代入（需 --order）")
    ap.add_argument("--llm", metavar="TASK", help="LLM 三顺序代入（需 --order --llm-provider；LLM 分析任务短板，失败降级规则）")
    ap.add_argument("--llm-provider", help="LLM provider（models.json 中的名称，如 sol/deepseek-v4-flash）")
    ap.add_argument("--llm-model", help="LLM 模型 id（默认用 provider 第一个）")
    ap.add_argument("--order", choices=list(THREE_ORDERS), help="三顺序之一 21354/12534/14325")
    ap.add_argument("--list-orders", action="store_true", help="列出三顺序")
    ap.add_argument("--list-dimensions", action="store_true", help="列出 14 维模块")
    ap.add_argument("--eval", metavar="TASK", help="评测汇总：读 loop-<task>/runs/ 统计命中率")
    ap.add_argument("--gene", metavar="TASK", help="进化基因沉淀：从 evidence/gaps/candidates 提取经验基因（D12 元学习）")
    ap.add_argument("--gene-llm", metavar="TASK", help="LLM 基因生成：把短板教训总结成结构化可复用基因（需 --llm-provider）")
    ap.add_argument("--match", metavar="TASK_DESC", help="基因匹配复用：新任务描述匹配历史基因（D12 元学习闭环）")
    ap.add_argument("--gene-sync", metavar="TASK", help="双向写回 A 向：基因→记忆颗粒（生成结算文档，走标准记忆管线）")
    ap.add_argument("--gene-from-memory", metavar="TOPIC", help="双向写回 B 向：记忆颗粒→基因（从记忆库检索经验，LLM 提炼基因）")
    ap.add_argument("--list-genes", action="store_true", help="列出基因库")
    ap.add_argument("--health-deep", action="store_true", help="Ψ 深度健康监测：基因库/记忆库/沙箱/反馈完整性")
    ap.add_argument("--no-memory", action="store_true", help="health-deep 跳过记忆库检查（CI/无记忆库环境用）")
    ap.add_argument("--feedback", nargs=3, metavar=("TASK_DESC", "OUTCOME", "GENE_ID"), help="Φ 记录基因复用反馈：success/failure（GENE_ID 可省略填 -）")
    ap.add_argument("--feedback-stats", action="store_true", help="Φ 反馈统计：按基因/总体复用成功率")
    ap.add_argument("--prune-genes", nargs="?", const="0.5", metavar="THRESHOLD", help="Φ 淘汰无效基因：失败率≥阈值(默认0.5)且样本≥2 标记 deprecated")
    ap.add_argument("--include-deprecated", action="store_true", help="匹配/统计时包含已淘汰基因")
    ap.add_argument("--status", action="store_true", help="Λ_ctx 统一状态入口：健康+基因+反馈一处汇总")
    ap.add_argument("--set", choices=["warmup", "holdout", "holdout2", "all"], default="all", help="评测集合（默认 all）")
    args = ap.parse_args()

    if args.health:
        print(json.dumps(health_check(), ensure_ascii=False, indent=2))
        return 0
    if args.health_deep:
        print(json.dumps(health_deep(check_memory=not args.no_memory), ensure_ascii=False, indent=2))
        return 0
    if args.status:
        print(json.dumps(status_summary(), ensure_ascii=False, indent=2))
        return 0
    if args.feedback:
        task_desc, outcome, gene_id = args.feedback
        if gene_id == "-":
            gene_id = ""
        print(json.dumps(feedback_record(task_desc, outcome, gene_id or None), ensure_ascii=False, indent=2))
        return 0
    if args.feedback_stats:
        print(json.dumps(feedback_stats(), ensure_ascii=False, indent=2))
        return 0
    if args.prune_genes is not None:
        try:
            thr = float(args.prune_genes)
        except ValueError:
            print(json.dumps({"status": "BLOCKED", "reason": f"无效阈值: {args.prune_genes}"}, ensure_ascii=False))
            return 2
        print(json.dumps(prune_genes(threshold=thr), ensure_ascii=False, indent=2))
        return 0
    if args.init:
        print(json.dumps(init_workspace(args.init), ensure_ascii=False, indent=2))
        return 0
    if args.list_orders:
        for k, v in THREE_ORDERS.items():
            print(f"{k}: " + "→".join(DIMENSIONS[d]["name"] for d in v))
        return 0
    if args.list_dimensions:
        for k, v in DIMENSIONS.items():
            print(f"D{k} {v['name']:>4} {v['label']}  = {v['formula']}")
        return 0
    if args.eval:
        print(json.dumps(eval_runs(args.eval, args.set), ensure_ascii=False, indent=2))
        return 0
    if args.gene:
        print(json.dumps(collect_genes(args.gene), ensure_ascii=False, indent=2))
        return 0
    if args.gene_llm:
        if not args.llm_provider:
            print(json.dumps({"status": "BLOCKED", "reason": "LLM 基因生成需 --llm-provider"}, ensure_ascii=False))
            return 2
        print(json.dumps(gene_llm(args.gene_llm, args.llm_provider, args.llm_model), ensure_ascii=False, indent=2))
        return 0
    if args.match:
        print(json.dumps(match_genes(args.match, include_deprecated=args.include_deprecated), ensure_ascii=False, indent=2))
        return 0
    if args.gene_sync:
        print(json.dumps(gene_sync(args.gene_sync), ensure_ascii=False, indent=2))
        return 0
    if args.gene_from_memory:
        print(json.dumps(gene_from_memory(args.gene_from_memory, args.llm_provider or "deepseek-v4-flash", args.llm_model), ensure_ascii=False, indent=2))
        return 0
    if args.list_genes:
        genes_dir = SANDBOX / "genes"
        if not genes_dir.is_dir():
            print(json.dumps({"status": "BLOCKED", "reason": "基因库为空（先跑 --gene <task> 沉淀）"}, ensure_ascii=False))
            return 2
        for f in sorted(genes_dir.glob("genes-*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            label = d.get("task") or d.get("topic") or d.get("generated_by", "?")
            gen = d.get("generated_by", "rule")
            print(f"{f.name}: {d.get('gene_count', 0)} 基因 (task={label}, gen={gen})")
        return 0
    if args.substitute:
        if not args.order:
            print(json.dumps({"status": "BLOCKED", "reason": "需指定 --order"}, ensure_ascii=False))
            return 2
        print(json.dumps(substitute(args.substitute, args.order), ensure_ascii=False, indent=2))
        return 0
    if args.llm:
        if not args.order or not args.llm_provider:
            print(json.dumps({"status": "BLOCKED", "reason": "LLM 代入需 --order 和 --llm-provider"}, ensure_ascii=False))
            return 2
        print(json.dumps(llm_substitute(args.llm, args.order, args.llm_provider, args.llm_model), ensure_ascii=False, indent=2))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
