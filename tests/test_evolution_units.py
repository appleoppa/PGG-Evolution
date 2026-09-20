#!/usr/bin/env python3
"""F1-F7 控制单元行为测试。

运行：python3 tests/test_evolution_units.py

设计原则：只验**行为正确性**（该拦的必须拦、该停的必须停），不验"不崩溃"。
每个断言都对应 D02《统一进化公式与工程解释》的一条硬规则。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "evolution_units.py"

HG_OK = {
    "identity_ok": True, "scope_ok": True, "permission_ok": True,
    "data_policy_ok": True, "security_ok": True, "rollback_ready": True,
}
FULL_STAGES = {
    "RS1": {"requirements": "r", "risk_analysis": "a"},
    "RS2": {"test_plan": "t"},
    "RS3": {"isolated_build": "b"},
    "RS4": {"independent_review": "i"},
    "RS5": {"verification_matrix": "v"},
    "RS6": {"authorization": "au", "rollback_drill": "rd"},
    "RS7": {"readback": "rb"},
}


def run(args: list[str], home: str | None = None) -> dict:
    env = dict(os.environ)
    if home:
        env["HOME"] = home
    r = subprocess.run([sys.executable, str(SCRIPT)] + args,
                       capture_output=True, text=True, timeout=60, env=env)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "PARSE_FAIL", "raw": r.stdout[:300], "stderr": r.stderr[:300]}


def call(unit: str, action: str, payload: dict, home: str) -> dict:
    return run(["--unit", unit, "--action", action,
                "--payload", json.dumps(payload, ensure_ascii=False)], home)


def _gap(home: str, gid: str = "G1") -> None:
    call("F1", "create_gap", {"gap_id": gid, "expected": "待确认", "actual": "空字段",
                              "reproduction": "min.sh", "severity": "med"}, home)


def _candidate(home: str, cid: str = "C1", owner: str = "alice", gate: dict | None = None) -> dict:
    return call("F1", "propose", {
        "change_id": cid, "gap_id": "G1", "diff_or_ref": "d", "test_plan": "t",
        "rollback": "r", "owner": owner, "risk": "R2",
        "legal_na_reason": "非法律事项", "hardgate": gate or HG_OK}, home)


# ══ 硬门 ═══════════════════════════════════════════════════════════════

def test_hardgate_missing_field_is_needs_spec() -> None:
    """缺字段=NEEDS_SPEC，不得用默认值补齐（D02 §1.1）。"""
    with tempfile.TemporaryDirectory() as home:
        _gap(home)
        d = _candidate(home, gate={"identity_ok": True})   # 只填一个
        assert d["status"] == "NEEDS_SPEC", f"缺硬门字段应 NEEDS_SPEC: {d}"
        assert d["hardgate"]["missing"], d
    print("✓ F1 硬门缺字段 → NEEDS_SPEC（不默认通过）")


def test_hardgate_false_is_blocked() -> None:
    """硬门有 false → BLOCKED，且不可被任何分数折抵（D02 §1.2）。"""
    with tempfile.TemporaryDirectory() as home:
        _gap(home)
        bad = {**HG_OK, "permission_ok": False}
        d = _candidate(home, gate=bad)
        assert d["status"] == "BLOCKED", f"硬门失败应 BLOCKED: {d}"
        assert "permission_ok" in d["hardgate"]["failed"], d
    print("✓ F1 硬门 false → BLOCKED（布尔阻断，分数不可折抵）")


def test_legal_na_reason_required() -> None:
    """legal_review_ok 不适用也必须记录理由（D02 §1.2）。"""
    with tempfile.TemporaryDirectory() as home:
        _gap(home)
        d = call("F1", "propose", {
            "change_id": "C1", "gap_id": "G1", "diff_or_ref": "d", "test_plan": "t",
            "rollback": "r", "owner": "alice", "risk": "R2", "hardgate": HG_OK}, home)
        assert d["status"] == "NEEDS_SPEC" and "不适用" in d["reason"], d
    print("✓ F1 legal 不适用必须记理由（不得静默跳过）")


# ══ F1 状态机 ═════════════════════════════════════════════════════════

def test_gap_requires_reproduction() -> None:
    """没有最小复现的条目只能是研究假设（D02 §2 / §10.1）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F1", "create_gap", {"gap_id": "G1", "expected": "a", "actual": "b"}, home)
        assert d["status"] == "NEEDS_EVIDENCE", f"缺复现应 NEEDS_EVIDENCE: {d}"
        assert "reproduction" in d["reason"], d
    print("✓ F1 Gap 缺复现 → NEEDS_EVIDENCE（不得凭感觉登记缺口）")


def test_verify_without_authorization_stays_review() -> None:
    """验证通过 ≠ 可发布；无授权必须停在 REVIEW（D02 §2）。"""
    with tempfile.TemporaryDirectory() as home:
        _gap(home)
        _candidate(home)
        d = call("F1", "verify", {"change_id": "C1", "result": "pass"}, home)
        assert d["state"] == "REVIEW", f"无授权应 REVIEW: {d}"
        assert "不得自动 APPROVED" in d["reason"], d
    print("✓ F1 验证通过但无授权 → 保持 REVIEW")


def test_self_approval_blocked() -> None:
    """验证独立于生成：approver 不得等于 owner（D02 §2 / A4）。"""
    with tempfile.TemporaryDirectory() as home:
        _gap(home)
        _candidate(home, owner="alice")
        call("F1", "verify", {"change_id": "C1", "result": "pass"}, home)   # → REVIEW
        d = call("F1", "verify", {"change_id": "C1", "result": "pass",
                                  "authorized": True, "approver": "alice"}, home)
        assert d["status"] == "BLOCKED" and "自证" in d["reason"], f"自证应被拒: {d}"
        # 独立授权者可通过
        d = call("F1", "verify", {"change_id": "C1", "result": "pass",
                                  "authorized": True, "approver": "bob"}, home)
        assert d["state"] == "APPROVED", f"独立授权应 APPROVED: {d}"
    print("✓ F1 自证被拒；独立授权者可 APPROVED")


def test_release_readback_failure_rolls_back() -> None:
    """上线回读异常 → ROLLED_BACK（D02 §2 失败动作）。"""
    with tempfile.TemporaryDirectory() as home:
        _gap(home)
        _candidate(home)
        call("F1", "verify", {"change_id": "C1", "result": "pass"}, home)
        call("F1", "verify", {"change_id": "C1", "result": "pass",
                              "authorized": True, "approver": "bob"}, home)
        d = call("F1", "release", {"change_id": "C1", "readback_pass": False}, home)
        assert d["state"] == "ROLLED_BACK", f"回读失败应回滚: {d}"
    print("✓ F1 回读失败 → ROLLED_BACK")


# ══ F2 有界修复 ═══════════════════════════════════════════════════════

def test_bounded_requires_caps() -> None:
    """必须有轮次/时间/预算/风险上限，不得无限自迭代（D02 §3 / A7）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F2", "plan", {"failure_class": "logic", "caps": {"n_max": 3}}, home)
        assert d["status"] == "NEEDS_SPEC", f"缺上限应 NEEDS_SPEC: {d}"
        for k in ("t_max_s", "b_max", "r_max"):
            assert k in d["reason"], d
    print("✓ F2 缺上限 → NEEDS_SPEC（不得无限迭代）")


def test_unknown_failure_class_stops() -> None:
    """failure_class=unknown → 停止自动修复（低置信不得自动改）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F2", "plan", {"failure_class": "unknown",
                                "caps": {"n_max": 3, "t_max_s": 60, "b_max": 10, "r_max": 3}}, home)
        assert d["status"] == "BLOCKED", f"unknown 应停: {d}"
    print("✓ F2 failure_class=unknown → 停止自动修复")


def test_blocking_failure_terminates_immediately() -> None:
    """阻断级失败直接终止，不被上限延迟（D02 §3）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F2", "run", {
            "failure_class": "security",
            "caps": {"n_max": 5, "t_max_s": 600, "b_max": 100, "r_max": 4},
            "rounds": [{"verification": [{"name": "sec", "outcome": "fail", "blocking": True}]}]}, home)
        assert d["outcome"] == "STOP_AND_ESCALATE" and d["round"] == 1, d
    print("✓ F2 阻断级失败 → 立即 STOP（不等到上限）")


def test_not_run_never_counts_as_pass() -> None:
    """未运行不得写成通过（D02 §3 明令）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F2", "run", {
            "failure_class": "logic",
            "caps": {"n_max": 1, "t_max_s": 600, "b_max": 100, "r_max": 4},
            "rounds": [{"verification": [{"name": "a", "outcome": "pass"}, {"name": "b"}]}]}, home)
        assert d["outcome"] == "STOP_AND_ESCALATE", f"缺 outcome 应视为 not_run 而非 pass: {d}"
        assert d["log"][0]["matrix"][1]["outcome"] == "not_run", d
    print("✓ F2 not_run 不算 pass（关键防伪）")


def test_caps_stop_and_restore_snapshot() -> None:
    """达上限 → 恢复快照并升级人工（D02 §3）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F2", "run", {
            "failure_class": "logic",
            "caps": {"n_max": 2, "t_max_s": 600, "b_max": 100, "r_max": 4},
            "snapshot": "Ω_0",
            "rounds": [{"verification": [{"name": "u", "outcome": "fail"}]},
                       {"verification": [{"name": "u", "outcome": "fail"}]}]}, home)
        assert d["outcome"] == "STOP_AND_ESCALATE" and d["restore_snapshot"] == "Ω_0", d
    print("✓ F2 达上限 → 恢复快照 + 升级人工")


def test_exec_matrix_requires_authorization() -> None:
    """跑验证矩阵需显式授权，不得静默执行命令。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F2", "exec_matrix", {"checks": [{"name": "x", "cmd": "true"}]}, home)
        assert d["status"] == "BLOCKED", f"未授权应 BLOCKED: {d}"
        d = call("F2", "exec_matrix", {"allow_exec": True,
                                       "checks": [{"name": "x", "cmd": "true"}]}, home)
        assert d["all_pass"] is True, d
    print("✓ F2 验证矩阵需显式授权（不静默跑命令）")


# ══ F3 差异账本 ═══════════════════════════════════════════════════════

def test_no_synthetic_total_score() -> None:
    """不同量纲不得相加 → 拒绝「总进化分」（D02 §4 / §10.3）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F3", "total_score", {}, home)
        assert d["status"] == "BLOCKED", f"必须拒绝伪总分: {d}"
        assert "总进化分" in d["reason"], d
    print("✓ F3 拒绝合成「总进化分」（禁止伪精确）")


def test_delta_same_dimension_only() -> None:
    """只比较同量纲；方向按 better 归一为改善量（D02 §4）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F3", "record", {"name": "c1", "dimension": "latency",
                                  "baseline": 10, "candidate": 7}, home)
        assert d["delta"] == -3.0 and d["improvement"] == 3.0, d
        bad = call("F3", "record", {"name": "c1", "dimension": "质量加秒",
                                    "baseline": 1, "candidate": 2}, home)
        assert bad["status"] == "NEEDS_SPEC", f"跨量纲应拒: {bad}"
    print("✓ F3 同量纲差分（跨量纲拒绝）")


def test_compare_requires_hardgate_and_quality() -> None:
    """硬门与质量不可劣化是前置布尔资格，不参与打分（D02 §5）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F3", "compare", {
            "candidates": [{"change_id": "a", "risk_rank": 1},
                           {"change_id": "b", "risk_rank": 0}],
            "hardgate_ok": {"a": True, "b": False},
            "quality_noninferiority": {"a": True, "b": True}}, home)
        assert d["selected"] == "a", f"硬门未过的 b 不得入选: {d}"
        rows = {r["change_id"]: r for r in d["rows"]}
        assert rows["b"]["eligible"] is False, d
    print("✓ F3 硬门/质量不可劣化是前置资格（不参与打分）")


# ══ F4 路由与记忆 ═════════════════════════════════════════════════════

def test_route_constraints_and_high_risk() -> None:
    """约束不全不入选；高风险不自动路由（D02 §5）。"""
    with tempfile.TemporaryDirectory() as home:
        ok = {"schema_ok": True, "permission_ok": True, "data_domain_ok": True,
              "quota_ok": True, "budget_ok": True, "risk_ok": True}
        d = call("F4", "route", {"paths": [{"path_id": "p1", "checks": {"schema_ok": True}}]}, home)
        assert d["status"] == "BLOCKED", f"约束不全应 BLOCKED: {d}"
        d = call("F4", "route", {"high_risk": True, "paths": [
            {"path_id": "p1", "quality_noninferiority": True, "checks": ok}]}, home)
        assert d["status"] == "BLOCKED" and "高风险" in d["reason"], d
        # 正常：按 (risk, cost, latency) 字典序
        d = call("F4", "route", {"paths": [
            {"path_id": "p1", "quality_noninferiority": True, "risk_rank": 2,
             "estimated_cost": 1, "estimated_latency": 1, "checks": ok},
            {"path_id": "p2", "quality_noninferiority": True, "risk_rank": 1,
             "estimated_cost": 5, "estimated_latency": 5, "checks": ok}]}, home)
        assert d["selected"] == "p2", f"应选 risk 最低的: {d}"
    print("✓ F4 约束不全拒绝 / 高风险转人工 / 字典序选择")


def test_memory_requires_governance_fields() -> None:
    """长期记忆必须有来源/分类/ACL/TTL/删除路径（D02 §5）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F4", "memory_check", {"source_ref": "x", "data_class": "internal"}, home)
        assert d["status"] == "BLOCKED", f"缺治理字段应 BLOCKED: {d}"
        for k in ("acl", "ttl", "delete_path"):
            assert k in d["reason"], d
        d = call("F4", "memory_check", {"source_ref": "x", "data_class": "internal",
                                        "acl": "team", "ttl": "30d",
                                        "delete_path": "/x", "approved": True}, home)
        assert d["status"] == "OK", d
    print("✓ F4 长期记忆缺治理字段 → BLOCKED")


# ══ F5 DAG 与回执 ═════════════════════════════════════════════════════

def test_dag_cycle_and_incomplete_nodes() -> None:
    """图必须无环；节点缺 owner/contract/timeout/retry/budget 不可调度（D02 §6）。"""
    with tempfile.TemporaryDirectory() as home:
        cyc = {"nodes": {
            "a": {"owner": "x", "contract": "c", "timeout": 1, "retry": 1,
                  "budget": 1, "state": "queued", "preds": ["b"]},
            "b": {"owner": "x", "contract": "c", "timeout": 1, "retry": 1,
                  "budget": 1, "state": "queued", "preds": ["a"]}}}
        d = call("F5", "dag_validate", cyc, home)
        assert d["status"] == "BLOCKED" and d["cycle"], f"有环应 BLOCKED: {d}"
        d = call("F5", "dag_validate", {"nodes": {"a": {"owner": "x", "state": "queued"}}}, home)
        assert d["status"] == "NEEDS_SPEC" and d["incomplete"], d
    print("✓ F5 有环 BLOCKED / 节点字段不全 NEEDS_SPEC")


def test_serial_kind_needs_approval_dep() -> None:
    """共享写入/发布/权限/高风险工具节点必须显式依赖审批节点（D02 §6）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F5", "dag_validate", {"nodes": {
            "w": {"owner": "x", "contract": "c", "timeout": 1, "retry": 1,
                  "budget": 1, "state": "queued", "kind": "publish"}}}, home)
        assert d["status"] == "NEEDS_SPEC", f"串行类节点缺审批依赖应拒: {d}"
        assert "approval_dep" in json.dumps(d, ensure_ascii=False), d
    print("✓ F5 串行类节点必须显式依赖审批节点")


def test_child_completion_is_not_parent_receipt() -> None:
    """child completion ≠ parent receipt → 必须标 WATCH（D02 §6 / §10.8）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F5", "correlation", {"correlation_id": "c1", "events": [
            {"correlation_id": "c1", "event": "dispatch"},
            {"correlation_id": "c1", "event": "start"},
            {"correlation_id": "c1", "event": "completion"}]}, home)
        assert d["verdict"] == "WATCH" and d["complete"] is False, f"缺 receipt 必须 WATCH: {d}"
        assert "不得称任务已完成" in d["reason"], d
        # 无 child start
        d = call("F5", "correlation", {"correlation_id": "c2", "events": [
            {"correlation_id": "c2", "event": "dispatch"}]}, home)
        assert d["verdict"] == "WATCH", d
        # 完整链
        d = call("F5", "correlation", {"correlation_id": "c3", "events": [
            {"correlation_id": "c3", "event": e} for e in
            ("dispatch", "start", "tool", "completion", "receipt")]}, home)
        assert d["complete"] is True, d
    print("✓ F5 child completion ≠ parent receipt → WATCH（缺环不冒充完成）")


def test_divergence_escalates_to_human() -> None:
    """高分歧/低有效数/高风险 → 人工，不投票掩盖（D02 §6 / §10.4）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F5", "adjudicate", {"labels": ["A", "B"],
                                      "thresholds": {"m_min": 3, "d_threshold": 0.3}}, home)
        assert d["verdict"] == "HUMAN", f"高分歧应人工: {d}"
        assert any("m_eff" in r for r in d["reasons"]), d
        d = call("F5", "adjudicate", {"labels": ["A", "A", "A"],
                                      "thresholds": {"m_min": 3, "d_threshold": 0.3}}, home)
        assert d["verdict"] == "REVIEW", d
    print("✓ F5 高分歧 → 人工（保留分歧，不多数表决）")


# ══ F6 资产生命周期 ═══════════════════════════════════════════════════

def test_asset_cannot_jump_to_active() -> None:
    """PROPOSED 不得直跳 ACTIVE；缺回滚/到期不得激活（D02 §7）。"""
    with tempfile.TemporaryDirectory() as home:
        call("F6", "propose", {"asset_id": "A1", "type": "skill"}, home)
        d = call("F6", "activate", {"asset_id": "A1"}, home)
        assert d["status"] == "BLOCKED" and "直跳" in d["reason"], f"直跳应拒: {d}"
    print("✓ F6 PROPOSED 直跳 ACTIVE → 拒绝")


def test_asset_screen_requires_source_license() -> None:
    """外部材料不得直接吞噬：须过来源/版本/许可/去重筛查（D02 §7 / §10.7）。"""
    with tempfile.TemporaryDirectory() as home:
        call("F6", "propose", {"asset_id": "A1", "type": "skill"}, home)
        d = call("F6", "screen", {"asset_id": "A1", "source": "github", "version": "v1"}, home)
        assert d["status"] == "BLOCKED", f"缺许可应拒: {d}"
        assert "license" in d["reason"], d
        # 重复 → 关联主条目
        d = call("F6", "screen", {"asset_id": "A1", "source": "g", "version": "v1",
                                  "license": "MIT", "dedup_key": "k", "duplicate_of": "MAIN"}, home)
        assert d["state"] == "REJECTED" and d["duplicate_of"] == "MAIN", d
    print("✓ F6 来源/许可/去重筛查（不得直接吞噬）")


def test_asset_active_requires_rollback_and_expiry() -> None:
    """缺基线/测试/owner/回滚/到期策略不得 ACTIVE（D02 §7）。"""
    with tempfile.TemporaryDirectory() as home:
        call("F6", "propose", {"asset_id": "A2", "type": "skill"}, home)
        call("F6", "screen", {"asset_id": "A2", "source": "g", "version": "v1",
                              "license": "MIT", "dedup_key": "k2"}, home)
        call("F6", "approve", {"asset_id": "A2", "independent_review": True,
                               "legal_na_reason": "非法律", "hardgate": HG_OK}, home)
        d = call("F6", "activate", {"asset_id": "A2", "baseline": "b",
                                    "tests": "t", "owner": "o"}, home)
        assert d["status"] == "BLOCKED", f"缺回滚/到期应拒: {d}"
        assert "rollback" in d["reason"] and "expiry" in d["reason"], d
        d = call("F6", "activate", {"asset_id": "A2", "baseline": "b", "tests": "t",
                                    "owner": "o", "rollback": "r", "expiry": "2027-01-01"}, home)
        assert d["state"] == "ACTIVE", d
    print("✓ F6 ACTIVE 需基线/测试/owner/回滚/到期策略")


# ══ F7 发布门 ═════════════════════════════════════════════════════════

def test_release_gates_not_ready() -> None:
    """阶段门不全 → NOT_READY，不是默认通过（D02 §8）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F7", "evaluate", {"stages": {"RS1": {"requirements": "r"}}}, home)
        assert d["verdict"] == "NOT_READY", f"门不全应 NOT_READY: {d}"
        assert len(d["failed"]) == 7, d
    print("✓ F7 阶段门不全 → NOT_READY（非默认通过）")


def test_release_requires_independence_authorization_drill() -> None:
    """Release ⇔ ∧G_i ∧ HardGate ∧ A_u ∧ RollbackDrill（D02 §8）。"""
    with tempfile.TemporaryDirectory() as home:
        base = {"change_id": "C9", "stages": FULL_STAGES}
        # 自证
        d = call("F7", "release", {**base, "generator": "alice", "verifier": "alice"}, home)
        assert d["verdict"] == "NOT_READY" and "自证" in d["reason"], d
        # 缺授权
        d = call("F7", "release", {**base, "generator": "alice", "verifier": "bob",
                                   "legal_na_reason": "非法律", "hardgate": HG_OK}, home)
        assert d["verdict"] == "NOT_READY" and "授权" in d["reason"], d
        # 回滚演练未过
        d = call("F7", "release", {**base, "generator": "alice", "verifier": "bob",
                                   "authorization": "A1", "legal_na_reason": "非法律",
                                   "hardgate": HG_OK}, home)
        assert d["verdict"] == "NOT_READY" and "回滚演练" in d["reason"], d
        # 全过
        d = call("F7", "release", {**base, "generator": "alice", "verifier": "bob",
                                   "authorization": "A1", "rollback_drill_pass": True,
                                   "legal_na_reason": "非法律", "hardgate": HG_OK}, home)
        assert d["verdict"] == "RELEASED", d
    print("✓ F7 发布需独立验证 + 授权 + 回滚演练（四关缺一不可）")


def test_readback_degradation_triggers_rollback() -> None:
    """运行异常 → 降级/回滚（D02 §8 失败动作）。"""
    with tempfile.TemporaryDirectory() as home:
        d = call("F7", "readback", {"change_id": "C9", "slo": "p99<1s", "errors": 3,
                                    "cost": 1, "user_impact": "low", "degraded": True}, home)
        assert d["verdict"] == "ROLLBACK_REQUIRED", d
        d = call("F7", "readback", {"change_id": "C9", "slo": "p99<1s", "errors": 0,
                                    "cost": 1, "user_impact": "none"}, home)
        assert d["verdict"] == "HEALTHY", d
    print("✓ F7 运行回读异常 → ROLLBACK_REQUIRED")


# ══ 通用 ══════════════════════════════════════════════════════════════

def test_kill_switch() -> None:
    env = dict(os.environ, SELF_EVOLUTION_PLUGIN_DISABLED="1")
    r = subprocess.run([sys.executable, str(SCRIPT), "--list"],
                       capture_output=True, text=True, timeout=30, env=env)
    d = json.loads(r.stdout)
    assert d["status"] == "DISABLED" and r.returncode == 3, d
    print("✓ kill switch（SELF_EVOLUTION_PLUGIN_DISABLED=1）")


def test_list_units() -> None:
    with tempfile.TemporaryDirectory() as home:
        r = subprocess.run([sys.executable, str(SCRIPT), "--list"],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, HOME=home))
        out = r.stdout
        for u in ("F1", "F2", "F3", "F4", "F5", "F6", "F7"):
            assert u in out, f"缺单元 {u}"
    print("✓ 列出 F1-F7 全部单元")


def test_rejects_nonzero_exit_for_refusals() -> None:
    """拒绝类结果必须返回非零退出码，便于 CI/脚本判真拦。"""
    with tempfile.TemporaryDirectory() as home:
        r = subprocess.run([sys.executable, str(SCRIPT), "--unit", "F3",
                            "--action", "total_score", "--payload", "{}"],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, HOME=home))
        assert r.returncode == 1, f"拒绝应 exit=1: {r.returncode}"
    print("✓ 拒绝类结果 exit=1（可脚本判真拦）")


def main() -> None:
    test_hardgate_missing_field_is_needs_spec()
    test_hardgate_false_is_blocked()
    test_legal_na_reason_required()
    test_gap_requires_reproduction()
    test_verify_without_authorization_stays_review()
    test_self_approval_blocked()
    test_release_readback_failure_rolls_back()
    test_bounded_requires_caps()
    test_unknown_failure_class_stops()
    test_blocking_failure_terminates_immediately()
    test_not_run_never_counts_as_pass()
    test_caps_stop_and_restore_snapshot()
    test_exec_matrix_requires_authorization()
    test_no_synthetic_total_score()
    test_delta_same_dimension_only()
    test_compare_requires_hardgate_and_quality()
    test_route_constraints_and_high_risk()
    test_memory_requires_governance_fields()
    test_dag_cycle_and_incomplete_nodes()
    test_serial_kind_needs_approval_dep()
    test_child_completion_is_not_parent_receipt()
    test_divergence_escalates_to_human()
    test_asset_cannot_jump_to_active()
    test_asset_screen_requires_source_license()
    test_asset_active_requires_rollback_and_expiry()
    test_release_gates_not_ready()
    test_release_requires_independence_authorization_drill()
    test_readback_degradation_triggers_rollback()
    test_kill_switch()
    test_list_units()
    test_rejects_nonzero_exit_for_refusals()
    print("\n全部测试通过 ✅")


if __name__ == "__main__":
    main()
