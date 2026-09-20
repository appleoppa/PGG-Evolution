#!/usr/bin/env python3
"""F1-F7 可执行控制单元 — 统一进化公式的工程落地。

来源：开智与进化成果包 D02《统一进化公式与工程解释》
定位：把「候选生命周期 / 有界修复 / 差异账本 / 受约束路由 / DAG 审计 /
      资产队列 / 发布门」从规格文本变成**会拒绝的代码**。

与 self_evolve.py 的分工：
  - self_evolve.py：三顺序代入找短板 + 证据等级账本（E0-E9）+ 五层门禁
  - 本文件：F1-F7 七个控制单元的**状态机与布尔硬门**

安全边界：
  - 所有写入限 ~/.pi/agent/evolution/units/（沙箱）
  - kill switch: SELF_EVOLUTION_PLUGIN_DISABLED=1
  - 不写 canonical memory、不改生产配置、不碰权限/路由/安全策略

设计原则（对应 D02 §10 禁止跳步清单）：
  1. 缺字段 = NEEDS_SPEC，不得用默认值补齐
  2. 硬门是布尔合取，分数/投票/置信/fitness 一律不得折抵
  3. 同量纲才能相减；禁止合成"总进化分"（伪精确）
  4. child completion ≠ parent receipt，缺环必须标 WATCH
  5. 任何 gate 不通过 → NOT_READY，而非"默认通过"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SANDBOX = Path.home() / ".pi" / "agent" / "evolution" / "units"
KILL_SWITCH = "SELF_EVOLUTION_PLUGIN_DISABLED"

# 只读模式（吸收自 Apex 资源盘《超级进化21》）：与 self_evolve.py 同一环境变量，
# 两个引擎行为一致——只读时一切写盘被拦（拒绝类 exit=1）。
READONLY_SWITCH = "PGG_EVOLUTION_READONLY"


class ReadOnlyViolation(RuntimeError):
    """只读模式下尝试写盘。"""


def is_readonly() -> bool:
    return os.environ.get(READONLY_SWITCH) == "1"

# ══════════════════════════════════════════════════════════════════════
# 共同契约（D02 §1.1）与硬门（D02 §1.2）
# ══════════════════════════════════════════════════════════════════════

# 统一状态词（D02 §1.1）：登记状态，不表示模型能力等级
STATES = ("NEEDS_SPEC", "NEEDS_EVIDENCE", "PROPOSED", "EXPERIMENTING",
          "REVIEW", "APPROVED", "ACTIVE", "REJECTED", "DEPRECATED", "ROLLED_BACK")

# HardGate(c) = identity_ok ∧ scope_ok ∧ permission_ok ∧ data_policy_ok
#               ∧ security_ok ∧ legal_review_ok(若适用) ∧ rollback_ready
HARD_GATE_ALWAYS = ("identity_ok", "scope_ok", "permission_ok",
                    "data_policy_ok", "security_ok", "rollback_ready")
HARD_GATE_CONDITIONAL = ("legal_review_ok",)


def hard_gate(flags: dict, legal_applicable: bool = False,
              legal_na_reason: str = "") -> dict:
    """布尔硬门合取。缺字段 → NEEDS_SPEC；有 false → BLOCKED；全 true → PASS。

    D02 §1.2 明确：无字段不得以默认值补齐。所以「没填」和「填了 false」是两种
    不同的拒绝理由，都必须拦，且不得被任何分数折抵。
    """
    flags = flags or {}
    missing = [d for d in HARD_GATE_ALWAYS if d not in flags]
    if legal_applicable:
        missing += [d for d in HARD_GATE_CONDITIONAL if d not in flags]
    elif not (legal_na_reason or "").strip():
        # legal_review_ok 不适用也要记录理由（D02 §1.2 原文）
        return {"status": "NEEDS_SPEC", "ok": False,
                "reason": "legal_review_ok 不适用时必须记录理由（不得静默跳过）",
                "missing": ["legal_review_ok", "legal_na_reason"]}
    if missing:
        return {"status": "NEEDS_SPEC", "ok": False, "missing": missing,
                "reason": f"硬门字段缺失（缺字段=停 NEEDS_SPEC，不得默认通过）: {missing}"}

    required = list(HARD_GATE_ALWAYS) + (list(HARD_GATE_CONDITIONAL) if legal_applicable else [])
    failed = [d for d in required if not flags.get(d)]
    if failed:
        return {"status": "BLOCKED", "ok": False, "failed": failed,
                "reason": f"硬门失败（布尔阻断，不可被分数/投票/置信折抵）: {failed}"}
    return {"status": "PASS", "ok": True, "reason": "全部适用硬门通过"}


def _require(payload: dict, keys: list[str]) -> list[str]:
    """检查必填字段；返回缺失列表（空=齐备）。

    注意：按**键存在且非 None/非空串**判断，不按真值判断——否则 `errors=0`、
    `cost=0` 这类合法零值会被误判为缺失。
    """
    return [k for k in keys if k not in payload or payload[k] is None
            or (isinstance(payload[k], str) and not payload[k].strip())]


def _state_path() -> Path:
    return SANDBOX / "state.json"


def _load_state() -> dict:
    p = _state_path()
    if not p.is_file():
        return {"schema": "pgg-evolution/units/v1", "gaps": {}, "candidates": {},
                "deltas": {}, "routes": {}, "dags": {}, "assets": {}, "releases": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": "pgg-evolution/units/v1", "gaps": {}, "candidates": {},
                "deltas": {}, "routes": {}, "dags": {}, "assets": {}, "releases": {},
                "corrupt": True}
    for k in ("gaps", "candidates", "deltas", "routes", "dags", "assets", "releases"):
        d.setdefault(k, {})
    return d


def _save_state(st: dict) -> None:
    # 只读模式（PGG_EVOLUTION_READONLY=1）硬拦：与 self_evolve 同一开关。
    # 吸收自 Apex 资源盘《超级进化21》：Agent_read ∩ ¬Agent_edit = Max(Safety)。
    if os.environ.get(READONLY_SWITCH) == "1":
        raise ReadOnlyViolation(
            f"只读模式（{READONLY_SWITCH}=1）拒绝写状态: {_state_path()}。"
            "如需写盘先显式取消该环境变量。"
        )
    SANDBOX.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _state_path().write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ══════════════════════════════════════════════════════════════════════
# F1：LDR(K) → GapDetect → 候选变更状态机
# ══════════════════════════════════════════════════════════════════════

# 允许的状态转移（D02 §2 状态转移公式）
F1_TRANSITIONS = {
    "NEEDS_SPEC": {"PROPOSED", "REJECTED"},
    "NEEDS_EVIDENCE": {"PROPOSED", "REJECTED"},
    "PROPOSED": {"EXPERIMENTING", "REJECTED"},
    "EXPERIMENTING": {"REVIEW", "REJECTED"},
    "REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"ACTIVE", "ROLLED_BACK"},
    "ACTIVE": {"DEPRECATED", "ROLLED_BACK"},
    "DEPRECATED": {"ROLLED_BACK"},
    "REJECTED": set(),
    "ROLLED_BACK": set(),
}

F1_GAP_REQUIRED = ("gap_id", "expected", "actual", "reproduction", "severity")


def f1_create_gap(payload: dict) -> dict:
    """登记 Gap。缺 expected/actual/reproduction → NEEDS_EVIDENCE（不得凭感觉登记缺口）。"""
    missing = _require(payload, F1_GAP_REQUIRED)
    gid = str(payload.get("gap_id", "") or "").strip()
    if not gid:
        return {"status": "NEEDS_SPEC", "reason": "需 gap_id"}
    if missing:
        return {"status": "NEEDS_EVIDENCE", "gap_id": gid,
                "reason": f"Gap 必须可复现：缺 {missing}（D02 §2「没有最小复现的条目只能是研究假设」）"}
    st = _load_state()
    st["gaps"][gid] = {k: payload.get(k) for k in F1_GAP_REQUIRED} | {"created_at": _now()}
    _save_state(st)
    return {"status": "OK", "gap_id": gid, "state": "PROPOSED",
            "note": "Gap 已登记（可复现）；下一步可提候选"}


def f1_propose(payload: dict) -> dict:
    """GAP(g) ∧ MinimalDiff(c) ∧ HardGate(c) → EXPERIMENTING(c)。

    任一条件不满足即停在 NEEDS_SPEC/NEEDS_EVIDENCE，不得隐式跳过。
    """
    cid = str(payload.get("change_id", "") or "").strip()
    gid = str(payload.get("gap_id", "") or "").strip()
    if not cid or not gid:
        return {"status": "NEEDS_SPEC", "reason": "需 change_id 与 gap_id"}
    st = _load_state()
    if gid not in st["gaps"]:
        return {"status": "NEEDS_EVIDENCE", "reason": f"Gap 不存在: {gid}（缺口须先可复现登记）"}

    missing = _require(payload, ("diff_or_ref", "test_plan", "rollback", "owner", "risk"))
    if missing:
        return {"status": "NEEDS_SPEC", "change_id": cid,
                "reason": f"候选缺最小变更要素: {missing}（最小 diff/测试计划/回滚/owner/风险）"}

    gate = hard_gate(payload.get("hardgate") or {},
                     legal_applicable=bool(payload.get("legal_applicable")),
                     legal_na_reason=str(payload.get("legal_na_reason", "")))
    if not gate["ok"]:
        return {"status": gate["status"], "change_id": cid, "hardgate": gate,
                "reason": f"硬门未过，候选停在隔离状态: {gate['reason']}"}

    st["candidates"][cid] = {
        "change_id": cid, "gap_id": gid, "state": "EXPERIMENTING",
        "diff_or_ref": payload.get("diff_or_ref"), "test_plan": payload.get("test_plan"),
        "rollback": payload.get("rollback"), "owner": payload.get("owner"),
        "risk": payload.get("risk"), "hardgate": gate["status"],
        "history": [{"from": "PROPOSED", "to": "EXPERIMENTING", "at": _now()}],
    }
    _save_state(st)
    return {"status": "OK", "change_id": cid, "state": "EXPERIMENTING",
            "note": "候选进入隔离实验；验证+授权后才可 APPROVED"}


def f1_verify(payload: dict) -> dict:
    """EXPERIMENTING ∧ Verify=pass ∧ A(c)=granted → APPROVED；否则 REJECTED 或保持 REVIEW。"""
    cid = str(payload.get("change_id", "") or "").strip()
    st = _load_state()
    c = st["candidates"].get(cid)
    if not c:
        return {"status": "NEEDS_SPEC", "reason": f"候选不存在: {cid}"}
    if c["state"] not in ("EXPERIMENTING", "REVIEW"):
        return {"status": "BLOCKED", "change_id": cid,
                "reason": f"状态 {c['state']} 不可验证（仅 EXPERIMENTING/REVIEW 可验证）"}

    result = str(payload.get("result", "") or "").strip().lower()
    if result not in ("pass", "fail"):
        return {"status": "NEEDS_SPEC", "reason": "需 result=pass|fail"}

    if result == "fail":
        c["state"] = "REJECTED"
        c["reject_reason"] = str(payload.get("evidence", "") or "验证不通过")
        c["history"].append({"from": c["state"], "to": "REJECTED", "at": _now()})
        _save_state(st)
        return {"status": "OK", "change_id": cid, "state": "REJECTED",
                "note": "保存最小反例并拒绝合并（D02 §2 失败动作）"}

    # 验证通过 ≠ 可发布：还必须有独立授权
    if not payload.get("authorized"):
        prev = c["state"]
        c["state"] = "REVIEW"
        c["history"].append({"from": prev, "to": "REVIEW", "at": _now()})
        _save_state(st)
        return {"status": "OK", "change_id": cid, "state": "REVIEW",
                "reason": "验证通过但授权不足 → 保持 REVIEW（不得自动 APPROVED）；补授权后可再推进"}
    if not str(payload.get("approver", "") or "").strip():
        return {"status": "NEEDS_SPEC", "reason": "授权必须记名（approver）"}
    if str(payload.get("approver")) == str(c.get("owner")):
        return {"status": "BLOCKED", "change_id": cid,
                "reason": "验证独立于生成：approver 不得等于候选 owner（自证无效）"}

    c["state"] = "APPROVED"
    c["approver"] = payload.get("approver")
    c["history"].append({"from": "REVIEW" if payload.get("authorized") else c["state"],
                         "to": "APPROVED", "at": _now()})
    _save_state(st)
    return {"status": "OK", "change_id": cid, "state": "APPROVED",
            "note": "已授权；仍需 ReleaseReadback 才 ACTIVE"}


def f1_release(payload: dict) -> dict:
    """APPROVED ∧ ReleaseReadback=pass → ACTIVE(c), K'。回读失败→ROLLED_BACK。"""
    cid = str(payload.get("change_id", "") or "").strip()
    st = _load_state()
    c = st["candidates"].get(cid)
    if not c:
        return {"status": "NEEDS_SPEC", "reason": f"候选不存在: {cid}"}
    if c["state"] != "APPROVED":
        return {"status": "BLOCKED", "change_id": cid,
                "reason": f"状态 {c['state']} 不可发布（仅 APPROVED 可发布）"}
    if not payload.get("readback_pass"):
        c["state"] = "ROLLED_BACK"
        c["history"].append({"from": "APPROVED", "to": "ROLLED_BACK", "at": _now()})
        _save_state(st)
        return {"status": "OK", "change_id": cid, "state": "ROLLED_BACK",
                "reason": "上线回读异常 → 按回滚计划回退"}

    c["state"] = "ACTIVE"
    c["history"].append({"from": "APPROVED", "to": "ACTIVE", "at": _now()})
    # K'：只沉淀经验证的事实与适用条件，不存未授权隐性推理
    c["knowledge_sunk"] = {
        "claim": c.get("diff_or_ref"), "conditions": c.get("risk"),
        "failure_boundary": c.get("reject_reason", "未记录"),
        "note": "K' 仅保存经验证事实/适用条件/失败边界",
    }
    _save_state(st)
    return {"status": "OK", "change_id": cid, "state": "ACTIVE",
            "knowledge_sunk": True, "note": "已发布并回读；K' 已沉淀"}


def f1_show(payload: dict) -> dict:
    st = _load_state()
    return {"status": "OK", "gaps": st["gaps"], "candidates": st["candidates"],
            "transitions_allowed": {k: sorted(v) for k, v in F1_TRANSITIONS.items()}}


# ══════════════════════════════════════════════════════════════════════
# F2：Ralph/Harness 有界修复与验证状态机
# ══════════════════════════════════════════════════════════════════════

# 验证矩阵每项只能取这四个值（D02 §3：禁止将未运行写成通过）
VERIFY_OUTCOMES = ("pass", "fail", "not_applicable", "not_run")
FAILURE_CLASSES = ("schema", "logic", "dependency", "security",
                   "performance", "semantic", "unknown")


def f2_plan(payload: dict) -> dict:
    """创建有界修复计划：必须有轮次/时间/预算/风险上限，且 failure_class 已知。

    上限缺任一 → NEEDS_SPEC（不得默认无限迭代）。
    """
    caps = payload.get("caps") or {}
    missing = [k for k in ("n_max", "t_max_s", "b_max", "r_max") if k not in caps]
    if missing:
        return {"status": "NEEDS_SPEC",
                "reason": f"必须有界：缺上限 {missing}（D02 §3 不得无限自迭代）"}
    fc = str(payload.get("failure_class", "") or "").strip()
    if fc not in FAILURE_CLASSES:
        return {"status": "NEEDS_SPEC", "reason": f"failure_class 须为 {FAILURE_CLASSES} 之一"}
    if fc == "unknown":
        return {"status": "BLOCKED",
                "reason": "failure_class=unknown → 停止自动修复（低置信不得自动改）"}
    return {"status": "OK", "caps": caps, "failure_class": fc,
            "note": "计划就绪；每轮前须预留该轮最大成本，任一计数达上限即停"}


def f2_run(payload: dict) -> dict:
    """执行有界修复循环：跑验证矩阵，逐轮判定，达上限即 STOP_AND_ESCALATE。

    verification 为 [{name, outcome, blocking}]；outcome 必须显式给出，
    未给的按 not_run 处理（不得当成 pass）。
    """
    plan = f2_plan(payload)
    if plan["status"] != "OK":
        return plan

    caps = payload["caps"]
    n_max, t_max = int(caps["n_max"]), float(caps["t_max_s"])
    b_max, r_max = float(caps["b_max"]), int(caps["r_max"])
    rounds = payload.get("rounds") or []
    if not isinstance(rounds, list):
        return {"status": "NEEDS_SPEC", "reason": "rounds 须为列表"}

    started = time.time()
    spent = 0.0
    log = []
    verdict = None

    for i, rd in enumerate(rounds, start=1):
        elapsed = time.time() - started
        cost = float(rd.get("cost", 0) or 0)
        risk_rank = int(rd.get("risk_rank", 0) or 0)
        matrix = []
        for item in (rd.get("verification") or []):
            outcome = str(item.get("outcome", "not_run") or "not_run")
            if outcome not in VERIFY_OUTCOMES:
                outcome = "not_run"          # 非法值一律按未运行，不得算通过
            matrix.append({"name": item.get("name", "?"), "outcome": outcome,
                           "blocking": bool(item.get("blocking"))})
        log.append({"round": i, "matrix": matrix, "cost": cost, "risk_rank": risk_rank})

        blocking_fail = any(m["outcome"] == "fail" and m["blocking"] for m in matrix)
        any_fail = any(m["outcome"] == "fail" for m in matrix)
        any_not_run = any(m["outcome"] == "not_run" for m in matrix)

        # 阻断级失败直接终止当轮（不等上限）
        if blocking_fail:
            verdict = {"status": "OK", "outcome": "STOP_AND_ESCALATE", "round": i,
                       "reason": "阻断级验证失败 → 直接终止（安全/数据/法律失败不可重试掩盖）"}
            break
        if not any_fail and not any_not_run and matrix:
            verdict = {"status": "OK", "outcome": "KEEP", "round": i,
                       "reason": "验证矩阵全通过 → 进入 REVIEW（仍不等于可发布）"}
            break

        spent += cost
        # 达任一上限即停，不再多执行一轮
        if i >= n_max:
            verdict = {"status": "OK", "outcome": "STOP_AND_ESCALATE", "round": i,
                       "reason": f"达轮次上限 n_max={n_max}"}
            break
        if elapsed >= t_max:
            verdict = {"status": "OK", "outcome": "STOP_AND_ESCALATE", "round": i,
                       "reason": f"达墙钟上限 t_max={t_max}s（实测 {elapsed:.1f}s）"}
            break
        if spent >= b_max:
            verdict = {"status": "OK", "outcome": "STOP_AND_ESCALATE", "round": i,
                       "reason": f"达预算上限 b_max={b_max}（已耗 {spent}）"}
            break
        if risk_rank >= r_max:
            verdict = {"status": "OK", "outcome": "STOP_AND_ESCALATE", "round": i,
                       "reason": f"达风险上限 r_max={r_max}"}
            break

    if verdict is None:
        verdict = {"status": "OK", "outcome": "STOP_AND_ESCALATE",
                   "reason": "轮次用尽，未收敛 → 恢复快照并升级人工"}

    if verdict["outcome"] == "STOP_AND_ESCALATE":
        verdict["restore_snapshot"] = payload.get("snapshot", "<Ω_t>")
        verdict["note"] = "恢复 Ω_t，登记失败轨迹与最小复现，停止同类盲目重试"
    return {**verdict, "caps": caps, "spent": spent, "log": log,
            "note_extra": "ΔG=0/负势/单测通过 均不是收敛或放行门"}


def f2_exec_matrix(payload: dict) -> dict:
    """真跑验证矩阵命令（需 --execute 显式授权）。区分 pass/fail/not_run，不混淆。"""
    checks = payload.get("checks") or []
    if not payload.get("allow_exec"):
        return {"status": "BLOCKED",
                "reason": "跑验证矩阵需显式授权（allow_exec）——不得静默执行命令"}
    results = []
    for c in checks:
        cmd = str(c.get("cmd", "") or "").strip()
        if not cmd:
            results.append({"name": c.get("name", "?"), "outcome": "not_run",
                            "detail": "未提供命令"})
            continue
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            results.append({"name": c.get("name", "?"),
                            "outcome": "pass" if p.returncode == 0 else "fail",
                            "exit": p.returncode, "blocking": bool(c.get("blocking")),
                            "stderr_tail": (p.stderr or "")[-200:]})
        except subprocess.TimeoutExpired:
            results.append({"name": c.get("name", "?"), "outcome": "fail",
                            "detail": "超时(>120s)", "blocking": bool(c.get("blocking"))})
        except OSError as exc:
            results.append({"name": c.get("name", "?"), "outcome": "fail",
                            "detail": str(exc), "blocking": bool(c.get("blocking"))})
    blocking = [r for r in results if r["outcome"] == "fail" and r.get("blocking")]
    return {"status": "OK", "matrix": results,
            "all_pass": all(r["outcome"] == "pass" for r in results) if results else False,
            "blocking_failures": [r["name"] for r in blocking],
            "note": "not_run 永远不等于 pass"}


# ══════════════════════════════════════════════════════════════════════
# F3：ΔG / EVM 的缺陷账本与候选排序
# ══════════════════════════════════════════════════════════════════════

# 同量纲差分维度（D02 §4：只比较同量纲/同量表数据）
DELTA_DIMS = {
    "quality": {"unit": "score", "better": "higher"},
    "cost": {"unit": "currency", "better": "lower"},
    "latency": {"unit": "seconds", "better": "lower"},
    "risk_events": {"unit": "count", "better": "lower"},
    "failures": {"unit": "count", "better": "lower"},
}


def f3_record(payload: dict) -> dict:
    """记录基线与候选的同量纲差分。跨量纲一律拒绝。"""
    name = str(payload.get("name", "") or "").strip()
    dim = str(payload.get("dimension", "") or "").strip()
    if not name or dim not in DELTA_DIMS:
        return {"status": "NEEDS_SPEC",
                "reason": f"需 name 与 dimension ∈ {list(DELTA_DIMS)}"}
    try:
        base = float(payload["baseline"])
        cand = float(payload["candidate"])
    except (KeyError, TypeError, ValueError):
        return {"status": "NEEDS_SPEC", "reason": "需数值 baseline 与 candidate"}
    st = _load_state()
    d = st["deltas"].setdefault(name, {"name": name, "dims": {}})
    better = DELTA_DIMS[dim]["better"]
    delta = cand - base
    # 同量纲相减；方向按 better 归一为"改善量"
    improvement = delta if better == "higher" else -delta
    d["dims"][dim] = {"baseline": base, "candidate": cand, "delta": delta,
                      "improvement": improvement, "unit": DELTA_DIMS[dim]["unit"],
                      "recorded_at": _now()}
    _save_state(st)
    return {"status": "OK", "name": name, "dimension": dim, "delta": delta,
            "improvement": improvement, "unit": DELTA_DIMS[dim]["unit"]}


def f3_total_score(payload: dict) -> dict:
    """**拒绝**合成单一总分。这是刻意的反模式闸门（D02 §4 / §10.3）。"""
    return {"status": "BLOCKED",
            "reason": "不同量纲不得相加或相乘 → 拒绝生成「总进化分」（D02 §4 禁止伪总分）",
            "instead": "请用 f3_compare 的字典序/帕累托关系，或分别报告五类差分",
            "dims": list(DELTA_DIMS)}


def f3_compare(payload: dict) -> dict:
    """字典序 + 帕累托筛选：先硬门与质量不可劣化，再比风险/质量/成本/时延。"""
    cands = payload.get("candidates") or []
    if not cands:
        return {"status": "NEEDS_SPEC", "reason": "需 candidates 列表"}
    q_ni = payload.get("quality_noninferiority") or {}
    gate = payload.get("hardgate_ok") or {}

    rows = []
    for c in cands:
        cid = str(c.get("change_id", "") or "").strip()
        if not cid:
            return {"status": "NEEDS_SPEC", "reason": "每个候选需 change_id"}
        # 硬门与质量不可劣化是**前置布尔资格**，不参与打分排序
        if not gate.get(cid, False):
            rows.append({"change_id": cid, "eligible": False, "reason": "硬门未过"})
            continue
        # 缺值按 false（D02 §5）
        if not q_ni.get(cid, False):
            rows.append({"change_id": cid, "eligible": False, "reason": "质量不可劣化未证"})
            continue
        rows.append({"change_id": cid, "eligible": True,
                     "risk_rank": int(c.get("risk_rank", 0) or 0),
                     "quality": float(c.get("quality", 0) or 0),
                     "cost": float(c.get("cost", 0) or 0),
                     "latency": float(c.get("latency", 0) or 0)})

    elig = [r for r in rows if r["eligible"]]
    if not elig:
        return {"status": "OK", "selected": None, "rows": rows,
                "reason": "无合格候选 → 不强行选择，转人工/保守固定路径"}

    # 字典序：(risk_rank, -quality, cost, latency)
    ordered = sorted(elig, key=lambda r: (r["risk_rank"], -r["quality"], r["cost"], r["latency"]))
    # 帕累托前沿
    def dominates(a, b):
        ge = (a["risk_rank"] <= b["risk_rank"] and a["quality"] >= b["quality"]
              and a["cost"] <= b["cost"] and a["latency"] <= b["latency"])
        gt = (a["risk_rank"] < b["risk_rank"] or a["quality"] > b["quality"]
              or a["cost"] < b["cost"] or a["latency"] < b["latency"])
        return ge and gt
    front = [a["change_id"] for a in elig
             if not any(dominates(b, a) for b in elig if b is not a)]
    return {"status": "OK", "selected": ordered[0]["change_id"],
            "pareto_front": front, "rows": rows,
            "note": "排序仅供参考，入围项仍须交 F7 判决；ΔQ>0 不等于可发布"}


# ══════════════════════════════════════════════════════════════════════
# F4：Select–Read–Act、记忆与路由的受约束决策
# ══════════════════════════════════════════════════════════════════════

F4_FEASIBILITY = ("schema_ok", "permission_ok", "data_domain_ok",
                  "quota_ok", "budget_ok", "risk_ok")


def f4_route(payload: dict) -> dict:
    """Feasible → Eligible → a* = lexicographic_min(risk_rank, cost, latency)。"""
    paths = payload.get("paths") or []
    if not paths:
        return {"status": "NEEDS_SPEC", "reason": "需 paths 列表"}
    min_conf = payload.get("confidence_threshold")
    conf = payload.get("calibrated_confidence")
    high_risk = bool(payload.get("high_risk"))

    feasible, rejected = [], []
    for p in paths:
        pid = str(p.get("path_id", "") or "").strip()
        if not pid:
            return {"status": "NEEDS_SPEC", "reason": "每条路径需 path_id"}
        checks = p.get("checks") or {}
        missing = [k for k in F4_FEASIBILITY if k not in checks]
        if missing:
            rejected.append({"path_id": pid, "reason": f"约束字段缺失: {missing}"})
            continue
        bad = [k for k in F4_FEASIBILITY if not checks.get(k)]
        if bad:
            rejected.append({"path_id": pid, "reason": f"不满足约束: {bad}"})
            continue
        feasible.append(p)

    if not feasible:
        return {"status": "BLOCKED", "feasible": [], "rejected": rejected,
                "reason": "Feasible(s) 为空 → 不强行选择，转人工/保守固定路径"}

    # 缺值按 false 处理（D02 §5）
    eligible = [p for p in feasible if p.get("quality_noninferiority") is True]
    if not eligible:
        return {"status": "BLOCKED", "feasible": [p["path_id"] for p in feasible],
                "rejected": rejected,
                "reason": "Eligible(s) 为空（质量不可劣化未证，缺值按 false）→ 转人工"}

    if high_risk:
        return {"status": "BLOCKED", "feasible": [p["path_id"] for p in feasible],
                "eligible": [p["path_id"] for p in eligible], "rejected": rejected,
                "reason": "高风险任务不得自动路由 → 转人工"}
    if min_conf is not None:
        if conf is None:
            return {"status": "NEEDS_SPEC", "reason": "给了阈值就必须给 calibrated_confidence"}
        if float(conf) < float(min_conf):
            return {"status": "BLOCKED", "eligible": [p["path_id"] for p in eligible],
                    "reason": f"校准置信 {conf} < 阈值 {min_conf} → 转人工"}

    chosen = sorted(eligible, key=lambda p: (int(p.get("risk_rank", 0) or 0),
                                             float(p.get("estimated_cost", 0) or 0),
                                             float(p.get("estimated_latency", 0) or 0)))[0]
    return {"status": "OK", "selected": chosen["path_id"],
            "feasible": [p["path_id"] for p in feasible],
            "eligible": [p["path_id"] for p in eligible], "rejected": rejected,
            "next": "Select(a*) → Read(versioned_rule, cited_memory) → Act → Verify → Record(M_e)"}


def f4_memory_check(payload: dict) -> dict:
    """长期记忆写入前检查：来源/分类/ACL/TTL/删除路径缺一不可。"""
    missing = _require(payload, ("source_ref", "data_class", "acl", "ttl", "delete_path"))
    if missing:
        return {"status": "BLOCKED",
                "reason": f"长期记忆缺治理字段 {missing}（D02 §5 判断门槛）"}
    if not payload.get("approved"):
        return {"status": "BLOCKED", "reason": "写入 M_k 前需 F1/F7 的证据与审批"}
    return {"status": "OK", "note": "可写入审核知识 M_k；普通轨迹只进 M_e 并按 TTL 清理"}


# ══════════════════════════════════════════════════════════════════════
# F5：多智能体 DAG 与多模型生成—审计—裁决
# ══════════════════════════════════════════════════════════════════════

NODE_STATES = ("queued", "ready", "running", "blocked", "review", "done", "failed", "cancelled")
CORRELATION_EVENTS = ("dispatch", "start", "tool", "completion", "receipt")
SERIAL_KINDS = ("shared_write", "publish", "permission_change", "high_risk_tool")


def _dag_find_cycle(nodes: dict) -> list[str] | None:
    """DFS 找环；返回环路径或 None。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    stack = []

    def visit(n):
        color[n] = GRAY
        stack.append(n)
        for p in nodes.get(n, {}).get("preds", []) or []:
            if p not in color:
                continue
            if color[p] == GRAY:
                return stack[stack.index(p):] + [p]
            if color[p] == WHITE:
                cyc = visit(p)
                if cyc:
                    return cyc
        color[n] = BLACK
        stack.pop()
        return None

    for n in nodes:
        if color[n] == WHITE:
            cyc = visit(n)
            if cyc:
                return cyc
    return None


def f5_dag_validate(payload: dict) -> dict:
    """校验 DAG：必须无环，节点字段齐全，串行类节点须有审批依赖。"""
    nodes = payload.get("nodes") or {}
    if not nodes:
        return {"status": "NEEDS_SPEC", "reason": "需 nodes"}
    required = ("owner", "contract", "timeout", "retry", "budget", "state")
    incomplete = {}
    for nid, n in nodes.items():
        miss = [k for k in required if not n.get(k)]
        if n.get("kind") in SERIAL_KINDS and not n.get("approval_dep"):
            miss.append("approval_dep(串行类节点必须显式依赖审批节点)")
        if miss:
            incomplete[nid] = miss
    if incomplete:
        return {"status": "NEEDS_SPEC", "incomplete": incomplete,
                "reason": "节点缺 owner/contract/timeout/retry/budget 不可调度"}
    cyc = _dag_find_cycle(nodes)
    if cyc:
        return {"status": "BLOCKED", "cycle": cyc,
                "reason": f"存在未批准的环: {' → '.join(cyc)}"}
    return {"status": "OK", "nodes": len(nodes), "note": "图无环且节点可调度"}


def f5_ready(payload: dict) -> dict:
    """Ready(n) ⇔ queued ∧ 全部前驱 done ∧ 无租约冲突。"""
    nodes = payload.get("nodes") or {}
    leases = payload.get("leases") or {}
    ready, not_ready = [], []
    for nid, n in nodes.items():
        if n.get("state") != "queued":
            not_ready.append({"node": nid, "reason": f"state={n.get('state')}"})
            continue
        undone = [p for p in (n.get("preds") or []) if nodes.get(p, {}).get("state") != "done"]
        if undone:
            not_ready.append({"node": nid, "reason": f"前驱未完成: {undone}"})
            continue
        if leases.get(nid):
            not_ready.append({"node": nid, "reason": "租约冲突"})
            continue
        ready.append(nid)
    return {"status": "OK", "ready": ready, "not_ready": not_ready}


def f5_correlation(payload: dict) -> dict:
    """按同一 correlation_id 对账 dispatch→start→tool→completion→receipt。

    child completion ≠ parent receipt：缺 receipt 必须标 WATCH（D02 §6 / §10.8）。
    """
    events = payload.get("events") or []
    cid = str(payload.get("correlation_id", "") or "").strip()
    if not cid:
        return {"status": "NEEDS_SPEC", "reason": "需 correlation_id"}
    seen = {}
    for e in events:
        if str(e.get("correlation_id")) != cid:
            continue
        seen.setdefault(str(e.get("event")), []).append(e)
    missing = [x for x in CORRELATION_EVENTS if x not in seen]
    if not missing:
        return {"status": "OK", "correlation_id": cid, "complete": True,
                "reason": "回执链完整"}
    # 区分"最坏情况"与"中间态"
    if "completion" in seen and "receipt" not in seen:
        verdict = "WATCH"
        reason = "child 已完成但无 parent receipt → WATCH（不得称任务已完成）"
    elif "start" not in seen:
        verdict = "WATCH"
        reason = "无 child start → 路由/容量/派发失败；拒绝或重派，不无限等待"
    else:
        verdict = "WATCH"
        reason = f"回执链缺环 {missing} → WATCH/NEEDS_EVIDENCE"
    return {"status": "OK", "correlation_id": cid, "complete": False,
            "verdict": verdict, "missing": missing, "reason": reason}


def f5_adjudicate(payload: dict) -> dict:
    """多模型分歧裁决：m_eff < m_min 或 d ≥ d_threshold 或高风险 → 人工。"""
    labels = payload.get("labels") or []
    if not labels:
        return {"status": "NEEDS_SPEC", "reason": "需 labels（各审计者的版本化标签）"}
    thresholds = payload.get("thresholds") or {}
    if not thresholds.get("m_min") or thresholds.get("d_threshold") is None:
        return {"status": "NEEDS_SPEC",
                "reason": "m_min/d_threshold 须任务前冻结（缺任一项即 NEEDS_SPEC）"}
    m_eff = int(payload.get("m_eff", len(labels)) or len(labels))
    counts = {}
    for l in labels:
        counts[str(l)] = counts.get(str(l), 0) + 1
    d = 1 - (max(counts.values()) / m_eff) if m_eff else 1.0
    reasons = []
    if m_eff < int(thresholds["m_min"]):
        reasons.append(f"m_eff={m_eff} < m_min={thresholds['m_min']}")
    if d >= float(thresholds["d_threshold"]):
        reasons.append(f"分歧度 d={d:.2f} ≥ d_threshold={thresholds['d_threshold']}")
    if payload.get("evidence_missing"):
        reasons.append("证据缺失")
    if payload.get("high_risk"):
        reasons.append("高风险")
    if reasons:
        return {"status": "OK", "verdict": "HUMAN", "divergence": round(d, 3),
                "counts": counts, "reasons": reasons,
                "note": "保留分歧与出处，不得多数表决掩盖"}
    return {"status": "OK", "verdict": "REVIEW", "divergence": round(d, 3),
            "counts": counts, "note": "仍需 F7 判决"}


# ══════════════════════════════════════════════════════════════════════
# F6：进化队列与资产生命周期
# ══════════════════════════════════════════════════════════════════════

# 生命周期（D02 §7）：PROPOSED → SCREENED → EXPERIMENTING/APPROVED → ACTIVE → DEPRECATED
F6_TRANSITIONS = {
    "PROPOSED": {"SCREENED", "REJECTED"},
    "SCREENED": {"APPROVED", "REJECTED"},
    "APPROVED": {"ACTIVE", "REJECTED"},
    "ACTIVE": {"DEPRECATED"},
    "DEPRECATED": {"REJECTED"},
    "REJECTED": set(),
}
F6_SCREEN_FIELDS = ("source", "version", "license", "dedup_key")
F6_ACTIVE_FIELDS = ("baseline", "tests", "owner", "rollback", "expiry")


def f6_propose(payload: dict) -> dict:
    """登记候选资产。缺 source/version/license/dedup_key → 不得进 SCREENED。"""
    aid = str(payload.get("asset_id", "") or "").strip()
    if not aid:
        return {"status": "NEEDS_SPEC", "reason": "需 asset_id"}
    st = _load_state()
    st["assets"][aid] = {"asset_id": aid, "type": payload.get("type", "unknown"),
                         "state": "PROPOSED", "history": [],
                         "payload": {k: payload.get(k) for k in
                                     F6_SCREEN_FIELDS + F6_ACTIVE_FIELDS + ("claim",)},
                         "created_at": _now()}
    _save_state(st)
    return {"status": "OK", "asset_id": aid, "state": "PROPOSED"}


def f6_screen(payload: dict) -> dict:
    """来源/版本/许可证/去重筛查。外部材料不得直接「吞噬」。"""
    aid = str(payload.get("asset_id", "") or "").strip()
    st = _load_state()
    a = st["assets"].get(aid)
    if not a:
        return {"status": "NEEDS_SPEC", "reason": f"资产不存在: {aid}"}
    merged = {**a.get("payload", {}), **{k: payload.get(k) for k in F6_SCREEN_FIELDS if payload.get(k)}}
    missing = _require(merged, F6_SCREEN_FIELDS)
    if missing:
        return {"status": "BLOCKED", "asset_id": aid,
                "reason": f"来源/许可/去重筛查未过，缺 {missing}（不得直接吞噬）"}
    if payload.get("duplicate_of"):
        a["state"] = "REJECTED"
        a["reason"] = f"与 {payload['duplicate_of']} 重复 → 只关联主条目，不重复计权"
        a["payload"] = merged
        _save_state(st)
        return {"status": "OK", "asset_id": aid, "state": "REJECTED",
                "duplicate_of": payload["duplicate_of"], "reason": a["reason"]}
    a["state"] = "SCREENED"
    a["payload"] = merged
    a["history"].append({"to": "SCREENED", "at": _now()})
    _save_state(st)
    return {"status": "OK", "asset_id": aid, "state": "SCREENED",
            "note": "下一步隔离评测 + 独立审查 + HardGate + 授权 才可 APPROVED"}


def f6_approve(payload: dict) -> dict:
    aid = str(payload.get("asset_id", "") or "").strip()
    st = _load_state()
    a = st["assets"].get(aid)
    if not a:
        return {"status": "NEEDS_SPEC", "reason": f"资产不存在: {aid}"}
    if a["state"] != "SCREENED":
        return {"status": "BLOCKED", "asset_id": aid,
                "reason": f"状态 {a['state']} 不可批准（仅 SCREENED）"}
    if not payload.get("independent_review"):
        return {"status": "BLOCKED", "asset_id": aid, "reason": "需独立审查通过"}
    gate = hard_gate(payload.get("hardgate") or {},
                     legal_applicable=bool(payload.get("legal_applicable")),
                     legal_na_reason=str(payload.get("legal_na_reason", "")))
    if not gate["ok"]:
        return {"status": gate["status"], "asset_id": aid, "hardgate": gate,
                "reason": f"硬门未过: {gate['reason']}"}
    a["state"] = "APPROVED"
    a["history"].append({"to": "APPROVED", "at": _now()})
    _save_state(st)
    return {"status": "OK", "asset_id": aid, "state": "APPROVED"}


def f6_activate(payload: dict) -> dict:
    """ACTIVE 前必须有基线/测试/owner/回滚/到期策略，缺一即拒。"""
    aid = str(payload.get("asset_id", "") or "").strip()
    st = _load_state()
    a = st["assets"].get(aid)
    if not a:
        return {"status": "NEEDS_SPEC", "reason": f"资产不存在: {aid}"}
    if a["state"] != "APPROVED":
        return {"status": "BLOCKED", "asset_id": aid,
                "reason": f"状态 {a['state']} 不可激活（仅 APPROVED）；PROPOSED 直跳 ACTIVE 一律拒绝"}
    merged = {**a.get("payload", {}), **{k: payload.get(k) for k in F6_ACTIVE_FIELDS if payload.get(k)}}
    missing = _require(merged, F6_ACTIVE_FIELDS)
    if missing:
        return {"status": "BLOCKED", "asset_id": aid,
                "reason": f"缺 {missing} 不得 ACTIVE（基线/测试/owner/回滚/到期策略）"}
    a["state"] = "ACTIVE"
    a["payload"] = merged
    a["history"].append({"to": "ACTIVE", "at": _now()})
    _save_state(st)
    return {"status": "OK", "asset_id": aid, "state": "ACTIVE"}


def f6_deprecate(payload: dict) -> dict:
    aid = str(payload.get("asset_id", "") or "").strip()
    st = _load_state()
    a = st["assets"].get(aid)
    if not a:
        return {"status": "NEEDS_SPEC", "reason": f"资产不存在: {aid}"}
    if a["state"] != "ACTIVE":
        return {"status": "BLOCKED", "asset_id": aid, "reason": f"状态 {a['state']} 不可弃用"}
    a["state"] = "DEPRECATED"
    a["deprecate_reason"] = str(payload.get("reason", "未记录"))
    a["history"].append({"to": "DEPRECATED", "at": _now()})
    _save_state(st)
    return {"status": "OK", "asset_id": aid, "state": "DEPRECATED",
            "note": "触发 F7 回滚；保留原因与证据"}


# ══════════════════════════════════════════════════════════════════════
# F7：质量门、发布判决与运行回读
# ══════════════════════════════════════════════════════════════════════

# 七阶段发布门（RS1-RS7）
RELEASE_STAGES = {
    "RS1": ("需求与风险", ("requirements", "risk_analysis")),
    "RS2": ("可测试计划", ("test_plan",)),
    "RS3": ("隔离实现", ("isolated_build",)),
    "RS4": ("独立审查", ("independent_review",)),
    "RS5": ("验证/重放", ("verification_matrix",)),
    "RS6": ("发布判决与授权", ("authorization", "rollback_drill")),
    "RS7": ("运行回读/复盘", ("readback",)),
}


def f7_evaluate(payload: dict) -> dict:
    """逐阶段判门。任一门不过 → NOT_READY（不是「默认通过」）。"""
    stages = payload.get("stages") or {}
    results, failed = {}, []
    for sid, (label, reqs) in RELEASE_STAGES.items():
        given = stages.get(sid) or {}
        miss = [r for r in reqs if not given.get(r)]
        results[sid] = {"label": label, "pass": not miss, "missing": miss}
        if miss:
            failed.append(f"{sid}({label})")
    if failed:
        return {"status": "OK", "verdict": "NOT_READY", "stages": results,
                "failed": failed,
                "reason": f"阶段门未过: {failed} → NOT_READY，而非默认通过"}
    return {"status": "OK", "verdict": "STAGES_PASS", "stages": results,
            "note": "阶段门通过；仍须 HardGate + 授权 + 回滚演练"}


def f7_release(payload: dict) -> dict:
    """Release(c) ⇔ ∧G_i=pass ∧ HardGate(c) ∧ A_u ∧ RollbackDrill(R)=pass。"""
    ev = f7_evaluate(payload)
    if ev["verdict"] != "STAGES_PASS":
        return ev

    # 验证者不得与生成者自证（D02 §8 判断门槛）
    gen = str(payload.get("generator", "") or "").strip()
    ver = str(payload.get("verifier", "") or "").strip()
    if not gen or not ver:
        return {"status": "NEEDS_SPEC", "verdict": "NOT_READY",
                "reason": "需 generator 与 verifier 记名（禁止自证）"}
    if gen == ver:
        return {"status": "BLOCKED", "verdict": "NOT_READY",
                "reason": f"验证者与生成者同为 {gen} → 自证无效"}

    gate = hard_gate(payload.get("hardgate") or {},
                     legal_applicable=bool(payload.get("legal_applicable")),
                     legal_na_reason=str(payload.get("legal_na_reason", "")))
    if not gate["ok"]:
        return {"status": gate["status"], "verdict": "NOT_READY", "hardgate": gate,
                "reason": f"硬门未过: {gate['reason']}"}
    if not payload.get("authorization"):
        return {"status": "BLOCKED", "verdict": "NOT_READY", "reason": "缺明确授权 A_u"}
    if not payload.get("rollback_drill_pass"):
        return {"status": "BLOCKED", "verdict": "NOT_READY",
                "reason": "回滚演练未通过 → 不得发布（R 必须经演练）"}

    cid = str(payload.get("change_id", "") or "unnamed")
    st = _load_state()
    st["releases"][cid] = {"change_id": cid, "verdict": "RELEASED",
                           "generator": gen, "verifier": ver,
                           "hardgate": gate["status"], "at": _now()}
    _save_state(st)
    return {"status": "OK", "verdict": "RELEASED", "change_id": cid,
            "stages": ev["stages"], "hardgate": gate["status"],
            "note": "运行回读本身不自动扩大权限或激活新资产"}


def f7_readback(payload: dict) -> dict:
    """RS7 运行回读：写回 F3/F6，异常即降级/回滚。

    errors=0 / cost=0 是合法零值，不得当成字段缺失。
    """
    missing = [k for k in ("slo", "errors", "cost", "user_impact")
               if k not in payload or payload[k] is None]
    if missing:
        return {"status": "NEEDS_SPEC", "reason": f"运行回读缺 {missing}"}
    degraded = bool(payload.get("degraded"))
    cid = str(payload.get("change_id", "") or "unnamed")
    st = _load_state()
    st["releases"].setdefault(cid, {"change_id": cid})["readback"] = {
        "slo": payload.get("slo"), "errors": payload.get("errors"),
        "cost": payload.get("cost"), "user_impact": payload.get("user_impact"),
        "degraded": degraded, "at": _now()}
    _save_state(st)
    if degraded:
        return {"status": "OK", "verdict": "ROLLBACK_REQUIRED",
                "reason": "运行异常 → 降级/回滚、通知 owner、冻结同类发布并启动复盘"}
    return {"status": "OK", "verdict": "HEALTHY", "note": "已写回 F3/F6"}


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

ACTIONS = {
    "F1": {"create_gap": f1_create_gap, "propose": f1_propose, "verify": f1_verify,
           "release": f1_release, "show": f1_show},
    "F2": {"plan": f2_plan, "run": f2_run, "exec_matrix": f2_exec_matrix},
    "F3": {"record": f3_record, "total_score": f3_total_score, "compare": f3_compare},
    "F4": {"route": f4_route, "memory_check": f4_memory_check},
    "F5": {"dag_validate": f5_dag_validate, "ready": f5_ready,
           "correlation": f5_correlation, "adjudicate": f5_adjudicate},
    "F6": {"propose": f6_propose, "screen": f6_screen, "approve": f6_approve,
           "activate": f6_activate, "deprecate": f6_deprecate},
    "F7": {"evaluate": f7_evaluate, "release": f7_release, "readback": f7_readback},
}

UNIT_MEANING = {
    "F1": "LDR(K)→GapDetect→候选变更状态机",
    "F2": "Ralph/Harness 有界修复与验证状态机",
    "F3": "ΔG/EVM 缺陷账本与候选排序（拒绝伪总分）",
    "F4": "Select-Read-Act/记忆/路由受约束决策",
    "F5": "多智能体 DAG 与多模型生成-审计-裁决",
    "F6": "进化队列与资产生命周期",
    "F7": "质量门、发布判决与运行回读",
}


def main() -> int:
    if os.environ.get(KILL_SWITCH) == "1":
        print(json.dumps({"status": "DISABLED", "reason": f"{KILL_SWITCH}=1"},
                         ensure_ascii=False))
        return 3

    ap = argparse.ArgumentParser(description="F1-F7 可执行控制单元")
    ap.add_argument("--unit", choices=list(ACTIONS), help="控制单元 F1-F7")
    ap.add_argument("--action", help="单元内动作")
    ap.add_argument("--payload", default="{}", help="JSON 参数")
    ap.add_argument("--payload-file", help="从文件读 JSON 参数")
    ap.add_argument("--list", action="store_true", help="列出全部单元与动作")
    ap.add_argument("--show-state", action="store_true", help="读回沙箱状态")
    args = ap.parse_args()

    if args.list:
        for u, meaning in UNIT_MEANING.items():
            print(f"{u} {meaning}")
            print(f"    动作: {', '.join(sorted(ACTIONS[u]))}")
        return 0
    if args.show_state:
        st = _load_state()
        print(json.dumps({k: (len(v) if isinstance(v, dict) else v)
                          for k, v in st.items()}, ensure_ascii=False, indent=2))
        return 0
    if not args.unit:
        ap.print_help()
        return 2
    if not args.action:
        print(json.dumps({"status": "NEEDS_SPEC",
                          "reason": f"需 --action ∈ {sorted(ACTIONS[args.unit])}"},
                         ensure_ascii=False))
        return 2
    fn = ACTIONS[args.unit].get(args.action)
    if not fn:
        print(json.dumps({"status": "NEEDS_SPEC",
                          "reason": f"未知动作 {args.action}，可选 {sorted(ACTIONS[args.unit])}"},
                         ensure_ascii=False))
        return 2

    raw = args.payload
    if args.payload_file:
        try:
            raw = Path(args.payload_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"status": "NEEDS_SPEC", "reason": str(exc)}, ensure_ascii=False))
            return 2
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "NEEDS_SPEC", "reason": f"payload 非法 JSON: {exc}"},
                         ensure_ascii=False))
        return 2
    if not isinstance(payload, dict):
        print(json.dumps({"status": "NEEDS_SPEC", "reason": "payload 须为对象"},
                         ensure_ascii=False))
        return 2

    try:
        result = fn(payload)
    except ReadOnlyViolation as exc:
        print(json.dumps({"status": "READONLY_BLOCKED", "readonly": True,
                          "env": READONLY_SWITCH, "reason": str(exc),
                          "hint": f"如需写盘：unset {READONLY_SWITCH}"},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 拒绝类结果返回非零，便于 CI/脚本判真拦
    return 0 if result.get("status") in ("OK", "PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
