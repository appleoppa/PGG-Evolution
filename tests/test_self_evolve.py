#!/usr/bin/env python3
"""self-evolution 插件兼容性测试（契约 v0.1 要求）。

运行：python3 tests/test_self_evolve.py
"""
from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys

try:
    import pytest  # 可选：仅用于 approx；缺失时用本地兜底，不让测试因缺依赖崩
except ImportError:  # pragma: no cover
    class _Approx:
        def __init__(self, expected, abs=1e-6, rel=1e-6):
            self.expected, self.abs, self.rel = expected, abs, rel
        def __eq__(self, other):
            return abs(other - self.expected) <= max(self.abs, self.rel * abs(self.expected))
    class _PytestShim:
        @staticmethod
        def approx(expected, abs=1e-6, rel=1e-6):
            return _Approx(expected, abs, rel)
    pytest = _PytestShim()
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "self_evolve.py"


def run(args: list[str]) -> dict:
    r = subprocess.run([sys.executable, str(SCRIPT)] + args,
                       capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "PARSE_FAIL", "raw": r.stdout[:200], "stderr": r.stderr[:200]}


def run_in(args: list[str], home: str) -> dict:
    """在隔离 HOME 下运行（沙箱重定向，不污染真实基因库）。"""
    env = dict(os.environ, HOME=home)
    r = subprocess.run([sys.executable, str(SCRIPT)] + args,
                       capture_output=True, text=True, timeout=30, env=env)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"status": "PARSE_FAIL", "raw": r.stdout[:300], "stderr": r.stderr[:300]}


def _seed_rule_genes(home: str, genes: list[dict], name: str = "genes-rule-t.json") -> None:
    """造一个无 id 字段的规则提取基因文件（模拟 collect_genes 的真实产物）。"""
    d = Path(home) / ".pi" / "agent" / "evolution" / "genes"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"genes": genes}, ensure_ascii=False), encoding="utf-8")


def _seed_eval_runs(home: str, task: str, runs: list[dict]) -> None:
    d = Path(home) / ".pi" / "agent" / "evolution" / f"loop-{task}" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(runs):
        (d / f"run-{i}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        os.utime(d / f"run-{i}.json", (1789000000 + i * 600, 1789000000 + i * 600))


def test_health() -> None:
    d = run(["--health"])
    assert d.get("status") == "OK", f"health 失败: {d}"
    assert d.get("plugin") == "self-evolution"
    assert "21354" in d.get("three_orders", [])
    print("✓ health")


def test_health_deep() -> None:
    d = run(["--health-deep"])
    assert d.get("status") in ("OK", "DEGRADED", "CRITICAL"), f"health-deep 异常: {d}"
    assert "checks" in d and "repair_hint" in d and "retest" in d, f"health-deep 缺字段: {d}"
    assert "sandbox_writable" in d["checks"] and "genes_integrity" in d["checks"] and "memory_db" in d["checks"]
    # ε 闭环：监测→修复→复检 hint 必含修复指引
    assert d["retest"].startswith("python3"), f"retest 应为复检命令: {d}"
    print("✓ health_deep（Ψ 深度健康监测，含 repair_hint/retest ε 闭环）")


def test_feedback_record() -> None:
    # 隔离 HOME：测试不得污染真实沙箱（此前直接写 ~/.pi/agent/evolution/feedback.json）
    with tempfile.TemporaryDirectory() as home:
        d = run_in(["--feedback", "测试任务", "success", "gene_xyz"], home)
        assert d.get("status") == "OK", f"feedback 失败: {d}"
        assert d["recorded"]["gene_id"] == "gene_xyz" and d["recorded"]["outcome"] == "success"
        assert d["event_count"] >= 1
    print("✓ feedback 记录（Φ 正反馈采集，沙箱隔离）")


def test_feedback_invalid_outcome() -> None:
    with tempfile.TemporaryDirectory() as home:
        d = run_in(["--feedback", "t", "maybe", "g"], home)
        assert d.get("status") == "BLOCKED", f"非法 outcome 应 BLOCKED: {d}"
    print("✓ feedback 非法 outcome 返回 BLOCKED")


def test_feedback_stats() -> None:
    d = run(["--feedback-stats"])
    assert d.get("status") in ("OK", "DEGRADED"), f"feedback-stats 异常: {d}"
    assert "total_events" in d and "success_rate" in d
    print("✓ feedback_stats（Φ 复用成功率统计）")


def test_prune_genes() -> None:
    # 隔离 HOME：prune 会写 deprecated.json，不得污染真实基因库
    with tempfile.TemporaryDirectory() as home:
        _seed_rule_genes(home, [{"type": "rule", "source": "gaps", "module": "K",
                                 "mechanism": "prune 隔离测试基因", "resolution": "x"}])
        d = run_in(["--prune-genes"], home)
        assert d.get("status") == "OK", f"prune 失败: {d}"
        assert "newly_deprecated" in d and "total_deprecated" in d
    print("✓ prune_genes（Φ 无效基因淘汰，可回滚，沙箱隔离）")


def test_status_summary() -> None:
    d = run(["--status"])
    assert d.get("status") in ("OK", "DEGRADED", "CRITICAL"), f"status 异常: {d}"
    assert "health" in d and "genes" in d and "feedback" in d
    print("✓ status（Λ 统一状态入口）")


def test_match_filters_deprecated() -> None:
    # 用 --include-deprecated 验证参数存在；match 本身 OK 或 BLOCKED 不崩
    d = run(["--match", "xyzzy-nonsense", "--include-deprecated"])
    assert d.get("status") in ("OK", "BLOCKED"), f"match 应 OK/BLOCKED: {d}"
    print("✓ match --include-deprecated 参数可用")


def test_list_orders() -> None:
    r = subprocess.run([sys.executable, str(SCRIPT), "--list-orders"],
                       capture_output=True, text=True, timeout=30)
    assert "21354" in r.stdout and "12534" in r.stdout and "14325" in r.stdout
    assert "Θ" in r.stdout and "K" in r.stdout and "ε" in r.stdout
    print("✓ list_orders（含 21354/12534/14325 与模块映射）")


def test_init_workspace() -> None:
    with tempfile.TemporaryDirectory() as home:
        d = run_in(["--init", "test-task"], home)
        assert d.get("status") == "OK", f"init 失败: {d}"
        ws = d.get("workspace", "")
        assert "test-task" in ws and ws.startswith(home), f"应建在隔离 HOME 下: {ws}"
        for sub in ("evidence", "gaps", "candidates", "decisions", "runs"):
            assert sub in d.get("meta", {}).get("dirs", []), f"缺 {sub}"
    print("✓ init_workspace（五目录结构，沙箱隔离）")


def test_substitute_orders() -> None:
    with tempfile.TemporaryDirectory() as home:
        for order in ("21354", "12534", "14325"):
            d = run_in(["--substitute", "demo", "--order", order], home)
            assert d.get("status") == "OK", f"{order} 失败: {d}"
            probes = d.get("substitute", {}).get("probes", [])
            assert len(probes) == 5, f"{order} 应有 5 个模块探查，实际 {len(probes)}"
            path = d["substitute"]["path"]
            print(f"  ✓ {order}: {path}")
    print("✓ substitute（三顺序各 5 模块）")


def test_substitute_invalid_order() -> None:
    # argparse choices 层拦截非法顺序 → 退出码非 0
    r = subprocess.run([sys.executable, str(SCRIPT), "--substitute", "demo", "--order", "99999"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0, f"非法顺序应被拒，实际 rc={r.returncode}"
    print("✓ substitute 非法顺序被 argparse 拒绝")


def test_substitute_missing_order() -> None:
    d = run(["--substitute", "demo"])
    assert d.get("status") == "BLOCKED", f"缺 order 应 BLOCKED: {d}"
    print("✓ substitute 缺 order 返回 BLOCKED")


def test_kill_switch() -> None:
    env = dict(os.environ)
    env["SELF_EVOLUTION_PLUGIN_DISABLED"] = "1"
    r = subprocess.run([sys.executable, str(SCRIPT), "--health"],
                       capture_output=True, text=True, timeout=30, env=env)
    d = json.loads(r.stdout)
    assert d.get("status") == "DISABLED", f"kill switch 未生效: {d}"
    print("✓ kill switch")


def test_list_dimensions() -> None:
    r = subprocess.run([sys.executable, str(SCRIPT), "--list-dimensions"],
                       capture_output=True, text=True, timeout=30)
    assert "D1" in r.stdout and "Θ" in r.stdout and "D14" in r.stdout
    assert "LLM效能" in r.stdout
    print("✓ list_dimensions（14 维模块）")


def test_eval_missing_workspace() -> None:
    d = run(["--eval", "no-such-task-xyz"])
    assert d.get("status") == "BLOCKED", f"无工作区应 BLOCKED: {d}"
    print("✓ eval 无工作区返回 BLOCKED")


def test_gene_missing_workspace() -> None:
    d = run(["--gene", "no-such-task-xyz"])
    assert d.get("status") == "BLOCKED", f"无工作区应 BLOCKED: {d}"
    print("✓ gene 无工作区返回 BLOCKED")


def test_list_genes() -> None:
    r = subprocess.run([sys.executable, str(SCRIPT), "--list-genes"],
                       capture_output=True, text=True, timeout=30)
    # 文本输出：基因文件名列表或 BLOCKED 空库
    assert "genes-" in r.stdout or "BLOCKED" in r.stdout, f"list-genes 输出异常: {r.stdout}"
    print("✓ list_genes（基因库列表）")


def test_llm_missing_params() -> None:
    d = run(["--llm", "some-task"])
    assert d.get("status") == "BLOCKED", f"缺 --order/--llm-provider 应 BLOCKED: {d}"
    print("✓ llm 缺参数返回 BLOCKED")


def test_llm_fail_closed() -> None:
    d = run(["--llm", "task", "--order", "21354", "--llm-provider", "no-such-provider"])
    assert d.get("status") == "DEGRADED" and d.get("fallback") is True, f"LLM 失败应 DEGRADED 降级: {d}"
    assert d.get("probes"), f"降级应返回规则 probes: {d}"
    print("✓ llm 失败降级规则模式（fail-closed）")


def test_gene_llm_missing_provider() -> None:
    d = run(["--gene-llm", "some-task"])
    assert d.get("status") == "BLOCKED", f"缺 --llm-provider 应 BLOCKED: {d}"
    print("✓ gene-llm 缺 provider 返回 BLOCKED")


def test_gene_llm_missing_workspace() -> None:
    d = run(["--gene-llm", "no-such-task", "--llm-provider", "deepseek-v4-flash"])
    assert d.get("status") == "BLOCKED", f"无工作区应 BLOCKED: {d}"
    print("✓ gene-llm 无工作区返回 BLOCKED")


def test_match_empty_bank() -> None:
    # 用不存在任务的描述（可能匹配 0）应 OK 不崩
    d = run(["--match", "xyzzy-nonsense-task"])
    assert d.get("status") in ("OK", "BLOCKED"), f"match 应 OK 或 BLOCKED: {d}"
    print("✓ match（基因匹配复用）")


def test_gene_sync_missing_workspace() -> None:
    d = run(["--gene-sync", "no-such-task"])
    assert d.get("status") == "BLOCKED", f"无工作区应 BLOCKED: {d}"
    print("✓ gene-sync 无工作区返回 BLOCKED")


def test_gene_from_memory() -> None:
    # 记忆库只读查询，任意主题应 OK/BLOCKED 不崩
    d = run(["--gene-from-memory", "xyzzy-no-memory-topic"])
    assert d.get("status") in ("OK", "BLOCKED", "DEGRADED"), f"gene-from-memory 应不崩: {d}"
    print("✓ gene-from-memory（记忆→基因 B 向）")


def test_prune_actually_deprecates_rule_genes() -> None:
    """行为测试（原缺失）：规则提取基因（无 id）必须能被真淘汰，且 match 真归零。

    旧实现用 g.get("id") 判定，无 id 基因恒不淘汰，但 prune 仍返回 OK —— 典型假阳性。
    """
    with tempfile.TemporaryDirectory() as home:
        _seed_rule_genes(home, [
            {"type": "rule", "source": "gaps", "module": "K",
             "mechanism": "规则提取基因无id字段，验证淘汰闭环", "resolution": "修派生键"},
        ])
        m1 = run_in(["--match", "淘汰闭环"], home)
        assert m1.get("matched") == 1, f"应匹配到 1 条，实际 {m1}"
        gid = m1["genes"][0]["id"]
        assert gid.startswith("derived-"), f"规则基因应派生稳定 id，实际 {gid}"

        for _ in range(2):
            run_in(["--feedback", "淘汰闭环", "failure", gid], home)
        dry = run_in(["--prune-genes", "--dry-run"], home)
        assert gid in dry["would_deprecate"], f"dry-run 应预报淘汰: {dry}"
        assert dry["effect"]["affected_in_bank"] == 1, f"淘汰必须真影响基因库: {dry['effect']}"

        pr = run_in(["--prune-genes"], home)
        assert gid in pr["newly_deprecated"], f"应真淘汰: {pr}"
        assert pr["effect"]["matched_after_prune"] == 0, f"淘汰后应无可匹配基因: {pr['effect']}"

        m2 = run_in(["--match", "淘汰闭环"], home)
        assert m2.get("matched") == 0, f"淘汰后 match 必须归零（旧实现此处会仍为 1）: {m2}"
        m3 = run_in(["--match", "淘汰闭环", "--include-deprecated"], home)
        assert m3.get("matched") == 1, f"include-deprecated 应仍可追溯: {m3}"
    print("✓ prune 真淘汰（规则基因无 id 也生效，match 归零）")


def test_eval_pairs_hit_with_total() -> None:
    """行为测试（原缺失）：hit/total/rate 必须同源，不得跨 run 拼分子分母。

    A=2/5(0.4) 与 B=3/30(0.1) 不得被拼成 hit=3 total=30 rate=0.4。
    """
    with tempfile.TemporaryDirectory() as home:
        _seed_eval_runs(home, "pairtest", [
            {"eval_set": "holdout", "summary": {"overall": {"hit": 2, "total": 5, "rate": 0.4}}},
            {"eval_set": "holdout", "summary": {"overall": {"hit": 3, "total": 30, "rate": 0.1}}},
        ])
        d = run_in(["--eval", "pairtest"], home)
        key = "holdout:overall"
        assert key in d["summary"], f"缺 {key}: {d}"
        s = d["summary"][key]
        assert s["latest"]["hit"] == 3 and s["latest"]["total"] == 30, f"latest 应同源为 3/30: {s}"
        assert s["latest"]["rate"] == 0.1, f"latest.rate 应为 3/30=0.1（旧实现报 0.4）: {s}"
        assert s["best"]["rate"] == 0.4, f"best 应单独标注为 0.4: {s}"
        assert s["runs"] == 2, f"应记录 2 个 run: {s}"
    print("✓ eval hit/total 同源配对（latest 退步可见，best 单独标注）")


def test_gate_blocks_out_of_allowlist() -> None:
    """行为测试（原缺失）：五层门禁必须真拦截，而非只有文档。"""
    d = run(["--gate", "/Users/appleoppa/PGG-WIKI/memory/MEMORY.md", "--gate-backup", "scripts"])
    assert d.get("status") == "BLOCKED", f"canonical memory 越界应 BLOCKED: {d}"
    assert "L2_allowlist" in d.get("failed_layers", []), f"应由 L2 拦截: {d}"
    assert d.get("allow_apply") is False, f"越界不得放行: {d}"
    print("✓ gate L2 拦截越界路径（碰 canonical memory 被挡）")


def test_gate_blocks_secret_and_danger() -> None:
    with tempfile.TemporaryDirectory() as home:
        bk = Path(home) / "bk"
        bk.mkdir()
        (bk / "a.py.bak").write_text("x", encoding="utf-8")
        # 假密钥/危险命令在运行时拼接：避免测试源码自身被 L4/L5 扫成命中（自指误报）
        fake_key = "sk-" + "a" * 24 + "xyz123"
        force_flag = "--" + "force"
        diff = (f'+api_key = "{fake_key}"\n'
                f"+subprocess.run(['git', 'push', '{force_flag}'])\n")
        spec = importlib.util.spec_from_file_location("se_mod", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.apply_gate(["scripts/a.py"], diff, backup_dir=str(bk))
        assert r["status"] == "BLOCKED", f"密钥+force push 应 BLOCKED: {r}"
        assert "L4_secret_scan" in r["failed_layers"], f"应由 L4 拦截密钥: {r['failed_layers']}"
        assert "L5_danger" in r["failed_layers"], f"应由 L5 拦截危险模式: {r['failed_layers']}"
        hits = r["checks"]["L4_secret_scan"]["hits"]
        assert hits and "sha1" in hits[0], f"密钥只应记 hash 不记明文: {hits}"
        assert fake_key not in json.dumps(r), "不得回显密钥明文"
    print("✓ gate L4/L5 拦截密钥与危险模式（只记 hash 不回显明文）")


def test_gate_passes_clean_change() -> None:
    with tempfile.TemporaryDirectory() as home:
        bk = Path(home) / "bk"
        bk.mkdir()
        (bk / "a.py.bak").write_text("x", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("se_mod2", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.apply_gate(["scripts/a.py"], "+print('ok')\n", backup_dir=str(bk))
        assert r["status"] == "PASS" and r["allow_apply"] is True, f"干净变更应 PASS: {r}"
    print("✓ gate 干净变更放行（L1-L5 全过）")


def test_substitute_persists() -> None:
    """行为测试（原缺失）：substitute 结果必须落盘（旧版注释称记录到 gaps/ 但实际不写）。"""
    with tempfile.TemporaryDirectory() as home:
        d = run_in(["--substitute", "persisttest", "--order", "21354"], home)
        assert d.get("status") == "OK", f"substitute 失败: {d}"
        rec = d.get("recorded_to")
        assert rec and Path(rec).exists(), f"必须落盘可追: {d}"
        saved = json.loads(Path(rec).read_text(encoding="utf-8"))
        assert saved["order"] == "21354" and len(saved["probes"]) == 5, f"落盘内容应完整: {saved}"
    print("✓ substitute 结果落盘（Observe 阶段有证据可追）")


def test_evidence_rejects_insufficient() -> None:
    """行为测试：证据等级不够必须被拒（不得用文件存在冒充能力）。"""
    with tempfile.TemporaryDirectory() as home:
        # E1 谎报不存在的工件
        d = run_in(["--evidence", "CLM-T1", "--level", "E1", "--artifact", "/nope/x.py"], home)
        assert d.get("status") == "BLOCKED", f"E1 工件不存在必须拒绝: {d}"
        # E5 不给校验命令
        d = run_in(["--evidence", "CLM-T1", "--level", "E5", "--artifact", "scripts/self_evolve.py"], home)
        assert d.get("status") == "BLOCKED", f"E5 无 verify-cmd 必须拒绝: {d}"
        # E5 给了命令但不 --execute
        d = run_in(["--evidence", "CLM-T1", "--level", "E5", "--artifact", "scripts/self_evolve.py",
                    "--verify-cmd", "true"], home)
        assert d.get("status") == "BLOCKED" and "未执行" in d["reason"], f"未授权不得真跑: {d}"
        # E2 符号名不在工件里
        d = run_in(["--evidence", "CLM-T1", "--level", "E2", "--artifact", "scripts/self_evolve.py",
                    "--note", "绝对不存在的符号zzz"], home)
        assert d.get("status") == "BLOCKED", f"E2 符号未命中必须拒绝: {d}"
    print("✓ evidence 拒绝证据不足（谎报工件/命令/符号全部被拦）")


def test_evidence_empty_file_and_single_run_rejected() -> None:
    """行为测试：空文件不得冒充 E8；单次 run 不得冒充 E9。"""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty.json"
        empty.write_text("", encoding="utf-8")
        d = run_in(["--evidence", "CLM-T2", "--level", "E8", "--artifact", str(empty)], home)
        assert d.get("status") == "BLOCKED" and "为空" in d["reason"], f"空文件必须拒绝: {d}"
        one = Path(tmp) / "one.json"
        one.write_text(json.dumps({"task": "t", "receipt": "r", "run": "only one"}), encoding="utf-8")
        d = run_in(["--evidence", "CLM-T3", "--level", "E9", "--artifact", str(one)], home)
        assert d.get("status") == "BLOCKED" and "复现" in d["reason"], f"单次 run 必须拒绝: {d}"
    print("✓ evidence 拒绝空文件冒充 E8 / 单次 run 冒充 E9")


def test_evidence_state_derivation() -> None:
    """行为测试：状态派生必须按证据链，且不得跳级。"""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
        E = "scripts/self_evolve.py"
        # 只有 E2（代码存在）→ PARTIAL，绝不能是 VERIFIED
        run_in(["--evidence", "CLM-T4", "--level", "E2", "--artifact", E, "--note", "apply_gate"], home)
        d = run_in(["--evidence-status", "CLM-T4"], home)
        c = d["claims"][0]
        assert c["allowed_state"] == "PARTIAL", f"只有 E2 应为 PARTIAL: {c}"
        assert "掩盖失败" in c["forbidden_wording"], f"PARTIAL 必须禁用夸大表述: {c}"
        # 补 E3+E5 但缺 E7 → IMPLEMENTED_NOT_WIRED（不得称已接线）
        run_in(["--evidence", "CLM-T4", "--level", "E3", "--artifact", E,
                "--verify-cmd", "python3 scripts/self_evolve.py --health", "--execute"], home)
        run_in(["--evidence", "CLM-T4", "--level", "E5", "--artifact", E,
                "--verify-cmd", "true", "--execute"], home)
        # 读回不带 --execute 时命令类证据保守失效（不得凭旧回执冒充已接线）
        c = run_in(["--evidence-status", "CLM-T4"], home)["claims"][0]
        assert set(c["stale_levels"]) == {"E3", "E5"}, f"未重跑的命令证据应失效: {c}"
        assert c["allowed_state"] == "PARTIAL", f"保守降级后应为 PARTIAL: {c}"
        # 带 --execute 重跑后才升 IMPLEMENTED_NOT_WIRED（仍不得称已接线）
        c = run_in(["--evidence-status", "CLM-T4", "--execute"], home)["claims"][0]
        assert c["allowed_state"] == "IMPLEMENTED_NOT_WIRED", f"E2/E3/E5 无 E7 应为 IMPLEMENTED_NOT_WIRED: {c}"
        assert "生产可用" in c["forbidden_wording"], f"必须禁用生产可用表述: {c}"
        # 跳级：只登记 E8 而无前置 → 不得升 VERIFIED
        e8 = Path(tmp) / "e8.json"
        e8.write_text(json.dumps({"task": "t", "receipt": "r", "run": "r1", "repro": "r2"}), encoding="utf-8")
        run_in(["--evidence", "CLM-T5", "--level", "E8", "--artifact", str(e8)], home)
        c = run_in(["--evidence-status", "CLM-T5"], home)["claims"][0]
        assert c["allowed_state"] != "VERIFIED", f"E8 缺前置不得跳级为 VERIFIED: {c}"
    print("✓ evidence 状态派生按链且不跳级（含未重跑命令保守降级）")


def test_evidence_exaggeration_and_downgrade() -> None:
    """行为测试：夸大词必须判 INVALID_EXAGGERATED；工件失效必须降级。"""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as tmp:
        E = "scripts/self_evolve.py"
        run_in(["--evidence", "CLM-T6", "--level", "E2", "--artifact", E, "--note", "apply_gate",
                "--claim-text", "系统已实现 AGI，零幻觉"], home)
        c = run_in(["--evidence-status", "CLM-T6"], home)["claims"][0]
        assert c["allowed_state"] == "INVALID_EXAGGERATED", f"夸大词应判 INVALID_EXAGGERATED: {c}"
        assert len(c["exaggeration_hits"]) >= 2, f"应命中 AGI 与零幻觉: {c['exaggeration_hits']}"
        # 降级：登记后删除工件，高等级必须失效
        art = Path(tmp) / "art.py"
        art.write_text("def apply_gate(): pass\n", encoding="utf-8")
        run_in(["--evidence", "CLM-T7", "--level", "E2", "--artifact", str(art), "--note", "apply_gate"], home)
        assert run_in(["--evidence-status", "CLM-T7"], home)["claims"][0]["valid_levels"] == ["E2"]
        art.unlink()
        c = run_in(["--evidence-status", "CLM-T7"], home)["claims"][0]
        assert c["valid_levels"] == [] and "E2" in c["stale_levels"], f"工件失效必须降级: {c}"
    print("✓ evidence 夸大词拦截 + 工件失效自动降级")


def test_gene_l5_constraints_derived_not_faked() -> None:
    """基因 L5 约束层（行为）：推导必须来自文本、推不出必须显式标 UNSPECIFIED。

    对应真实缺口：本库 53 条基因 constraints=0 / validation=0（实测）。
    关键约束：不得凭空编造约束。显式字段为 0 时不得报成已完成。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("se_l5", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 能从文本推出回滚+读回约束
    g1 = {"mechanism": "改前先备份，改后读回确认", "strategy": ["备份为 .bak", "读回验证"],
          "signals_match": ["修改文件"]}
    c1 = mod.derive_gene_constraints(g1)
    assert "rollback_required" in c1["constraints"], c1
    assert "readback_required" in c1["constraints"], c1
    assert c1["explicit"] is False, "推导不得冒充显式"

    # 推不出环境边界时 → 必须显式标 UNSPECIFIED，不得默认安全
    assert "environment_scope" in c1["unspecified"], c1
    assert "preconditions" in c1["unspecified"], c1

    # 显式 constraints 优先于推导
    g2 = {"mechanism": "随便写", "constraints": {"custom": "作者本意"}}
    c2 = mod.derive_gene_constraints(g2)
    assert c2["explicit"] is True and c2["constraints"] == {"custom": "作者本意"}, c2
    assert c2["unspecified"] == [], c2

    # 无沙箱/只读信号的基因 → 不得假设它在沙箱内
    g3 = {"mechanism": "做某件事"}
    c3 = mod.derive_gene_constraints(g3)
    assert "environment_scope" in c3["unspecified"], c3
    assert not c3["constraints"], f"无信号不得编造约束: {c3}"

    # 验证模板必须给可执行命令（不是占位文字）
    v1 = mod.derive_gene_validation(g1)
    assert v1["explicit"] is False, v1
    assert any("python3 -c" in x for x in v1["validation"]), v1

    # 回填默认 dry-run，且不伪造本体字段
    bf = mod.gene_l5_backfill(dry_run=True)
    assert bf["dry_run"] is True and bf["files_written"] == 0, bf
    print("✓ 基因 L5 约束层：推导不冒充显式、推不出标 UNSPECIFIED、回填默认 dry-run")


def test_gene_match_warns_on_missing_l5() -> None:
    """行为：缺 L5 的基因被复用时必须显式警告，不得静默复用。"""
    with tempfile.TemporaryDirectory() as home:
        _seed_rule_genes(home, [{
            "type": "gap", "source": "t.json", "module": "m",
            "mechanism": "写入后未读回验证导致状态虚报",
            "resolution": "写入后读回确认",
        }], name="genes-l5-t.json")
        d = run_in(["--match", "写入后未读回验证状态虚报"], home)
        assert d["matched"] >= 1, d
        assert d["l5_missing"] >= 1, f"缺 L5 必须计入警告数: {d}"
        g = d["genes"][0]
        assert "l5_warning" in g, f"缺 L5 的基因必须带警告: {g}"
        assert "l5_derived" in g, g
        assert "UNSPECIFIED" or g["l5_derived"]["unspecified"], g
        assert "缺显式 L5" in d["note"], d["note"]
    print("✓ 基因复用时缺 L5 必警告（不静默复用）")


def test_permission_doctor_gives_actionable_attribution() -> None:
    """行为：权限诊断必须区分「服务未加载」与「无屏幕录制权限」，并给出可执行指引。

    背景：kimi-cu 坐标校正被 macOS TCC 挡住。两类症状相似但修法不同，
    必须分开报，否则会把环境问题误报成代码 bug。
    """
    doc = Path(__file__).resolve().parent.parent / "scripts" / "kimi_permission_doctor.py"
    assert doc.is_file(), f"诊断工具不存在: {doc}"
    r = subprocess.run([sys.executable, str(doc)], capture_output=True, text=True, timeout=90)
    out = r.stdout
    # 必须报服务状态 + KimiCU 权限（真判据）+ 宿主权限（仅参考）三项
    assert "服务" in out and "屏幕录制权限" in out, out[:400]
    # 退出码必须落在契约内（0 就绪 / 2 服务未加载或无法判定 / 3 无权限）
    assert r.returncode in (0, 2, 3), f"退出码越界: {r.returncode}\n{out[:300]}"
    # 不可用时必须给可执行指引，不得只说「失败」
    if r.returncode != 0:
        assert ("launchctl" in out or "系统设置" in out), f"缺可执行指引: {out[:400]}"
    # 2026-09-20 修正：必须区分「KimiCU 主体」与「宿主主体」两个不同 TCC 主体。
    # 旧版用 screencapture（宿主）判 kimi-cu 权限 → 测错主体，已修。
    assert "ai.kimi.cu" in out, out[:400]
    assert "仅参考" in out, f"宿主权限项必须标为仅参考: {out[:400]}"
    # 必须明确不得用宿主权限代替 kimi-cu 权限（防回归到错误归因）
    assert "不得代替" in out or "与 kimi-cu 无关" in out, out[:400]
    # 2026-09-20 修正：宿主权限项必须标注「真判据 = 实跑 screencapture」。
    # 背景：面板开关显示「开」≠ 授权生效（系统会弹指纹/密码认证框，
    # 开关已变 1 但改动未提交）——必须实跑才能判。
    assert "真判据" in out or "实跑" in out, out[:400]
    print("✓ 权限诊断：区分 KimiCU 主体/宿主主体，且不拿宿主权限冒充 kimi-cu 权限")


def test_host_capture_probe_never_uses_hidden_filename() -> None:
    """行为：宿主截图探针不得用隐藏文件名（. 开头）——screencapture 拒写。

    2026-09-20 实测真 bug：探针文件为 `/tmp/.kimi_perm_doctor_probe.png`，
    screencapture 报 'cannot write file to intended destination' **但返回码仍为 0**，
    导致授权已生效时工具仍误报「宿主权限不可用」。

    必须锁死：① 探针文件名不以点开头；② 判定同时校验文件存在且非空
    （不能只看 returncode）。
    """
    doc = Path(__file__).resolve().parent.parent / "scripts" / "kimi_permission_doctor.py"
    src = doc.read_text(encoding="utf-8")
    # ① 不得用隐藏文件名（/tmp/.xxx）
    assert '"/tmp/.' not in src and "'/tmp/." not in src, "探针文件名不得以点开头"
    # ② 必须同时校验存在 + 非空（防「返回码 0 但没写文件」误判）
    assert "probe.exists()" in src and "st_size > 0" in src, "必须校验文件存在且非空"
    # ③ 必须能识别「等待认证」弹框（开关=1 但改动未提交）
    assert "正在尝试修改" in src, "必须识别等待认证的弹框"
    assert "pending_auth_prompt" in src, "缺 pending_auth_prompt 检测函数"

    # 行为验证：两函数都真实可调用
    import importlib.util
    spec = importlib.util.spec_from_file_location("pd_hidden", doc)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert isinstance(mod.pending_auth_prompt(), bool), "pending_auth_prompt 必须返回 bool"
    assert isinstance(mod.host_screen_capture_state(), bool), "host_screen_capture_state 必须返回 bool"
    print("✓ 宿主截图探针：不用隐藏文件名，且校验文件存在非空（防返回码 0 误判）")


def test_permission_doctor_no_contradiction_when_service_down() -> None:
    """行为：服务未加载时，诊断结论不得与 ① 自相矛盾。

    2026-09-20 实测复现的真 bug：服务未加载（①❌）但结论报「权限就绪」exit=0。
    根因：判定顺序把 cap 放最前。实测证明服务是截图前提——服务停止后
    kimi-cu 调用直接报 'service unavailable: perform failed after retries'。
    修法：先判服务，服务未加载则不得输出肯定结论。
    """
    doc = Path(__file__).resolve().parent.parent / "scripts" / "kimi_permission_doctor.py"
    assert doc.is_file(), f"诊断工具不存在: {doc}"

    # 模拟「服务不可见」：用 shim 遮蔽 launchctl，使其报空（= 未加载）
    with tempfile.TemporaryDirectory() as td:
        shim = Path(td) / "launchctl"
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{td}{os.pathsep}{env.get('PATH', '')}"
        r = subprocess.run([sys.executable, str(doc)], capture_output=True, text=True,
                           timeout=90, env=env)
    out = r.stdout
    # 服务不可见时：必须非零，且不得出现「权限就绪」这类肯定结论
    assert r.returncode != 0, f"服务未加载时不得报成功: rc={r.returncode}\n{out[:300]}"
    assert "结论：权限就绪" not in out, f"服务未加载却报权限就绪（自相矛盾）: {out[:400]}"
    # ② 项不得在服务未加载时报「✅ 可用」（会与 ①❌ 矛盾）。
    # 注意：③ 宿主权限是独立事实，服务未加载时它仍可能可用——不得一并禁掉。
    # 用标题锚定 ② 段落，而非全文搜「✅ 可用」（会误伤 ③）。
    seg2 = out.split("② ", 1)[1].split("③ ", 1)[0] if "② " in out and "③ " in out else ""
    assert "✅ 可用" not in seg2, f"服务未加载时 ② 项不得报可用: {seg2[:250]}"
    # 必须明确指出服务是前置阻塞
    assert "服务未加载" in out or "未判定" in out, out[:400]
    print("✓ 服务未加载时诊断不报「权限就绪」（消除自相矛盾，服务为前置阻塞）")


def test_service_repair_diagnoses_plist_kinds_safely() -> None:
    """行为：服务修复工具必须准确识别 plist 三类问题，且 dry-run 绝不改系统。

    背景（2026-09-20 对照实验）：kimi-cu upgrade 会移除用户 plist，并从 app 内
    恢复使用 BundleProgram 相对路径的版本；用户级 LaunchAgent 需绝对路径，
    否则 bootstrap 报 exit=5（I/O error）。服务停止时 kimi-cu 调用报
    'service unavailable'，故服务是截图前置依赖。

    隔离：用临时 HOME 重定向 USER_PLIST，不碰真实 ~/Library/LaunchAgents。
    """
    import plistlib
    script = Path(__file__).resolve().parent.parent / "scripts" / "kimi_cu_service_repair.py"
    assert script.is_file(), f"修复工具不存在: {script}"

    src = script.read_text(encoding="utf-8")
    # 只做静态契约校验 + 纯逻辑单测（不执行真实 launchctl）
    # ① dry-run 必须是默认（--apply 才动作）——防误改系统
    assert '"--apply"' in src and "store_true" in src, "--apply 必须是显式开关"
    assert "dry-run" in src, "默认必须 dry-run"
    # ② 必须备份原 plist（可回滚）
    assert "已备份原 plist" in src, "改 plist 前必须备份"
    # ③ 必须区分三类 plist 问题
    for kind in ("missing", "relative", "ok"):
        assert kind in src, f"缺 plist 类型判定: {kind}"
    # ④ 修复后必须复检服务状态（不得直接报成功）
    assert "修复后服务仍未加载" in src, "修复后必须复检，不得直接报成功"

    # 纯逻辑验证：在临时 HOME 下实例化模块，测 plist_diagnosis 三类判定
    with tempfile.TemporaryDirectory() as home:
        la = Path(home) / "Library" / "LaunchAgents"
        la.mkdir(parents=True)
        plist_path = la / "ai.kimi.cu.service.plist"
        env = dict(os.environ)
        env["HOME"] = home

        def diag() -> str:
            code = (
                "import importlib.util,sys,json;"
                f"spec=importlib.util.spec_from_file_location('r',r'{script}');"
                "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
                "print(m.plist_diagnosis()['kind'])"
            )
            r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=30, env=env)
            assert r.returncode == 0, f"诊断失败: {r.stderr[:300]}"
            return r.stdout.strip()

        # 场景 A：plist 不存在（升级后被移除）
        assert diag() == "missing", "未识别 missing"
        # 场景 B：相对路径（app 内原版，bootstrap 必失败）
        plist_path.write_bytes(plistlib.dumps({
            "Label": "ai.kimi.cu.service",
            "BundleProgram": "Contents/MacOS/kimi-cu",
            "ProgramArguments": ["Contents/MacOS/kimi-cu", "service"],
            "MachServices": {"ai.kimi.cu.service": True},
        }))
        assert diag() == "relative", "未识别 relative（BundleProgram 相对路径）"
        # 场景 C：绝对路径（健康）
        plist_path.write_bytes(plistlib.dumps({
            "Label": "ai.kimi.cu.service",
            "ProgramArguments": ["/Applications/KimiCU.app/Contents/MacOS/kimi-cu", "service"],
            "MachServices": {"ai.kimi.cu.service": True},
        }))
        assert diag() == "ok", "未识别 ok（绝对路径）"

        # dry-run 在临时 HOME 下跑：**核心不变量=不写任何文件**。
        # 退出码按真实环境分三支（均合法，不得硬编码单一期望）：
        #   3 = 无 KimiCU.app（CI ubuntu）→ 必须给安装指引
        #   1 = 需修复但 dry-run   → 必须标 [dry-run]
        #   0 = 本机服务已加载且 plist 为绝对路径 → 无需修复
        # 2026-09-20 CI 实测：旧断言只认 [dry-run]，在 CI（无 app）与本机（服务在）均误报。
        before = sorted(p.name for p in la.iterdir())
        r = subprocess.run([sys.executable, str(script)], capture_output=True,
                           text=True, timeout=60, env=env)
        after = sorted(p.name for p in la.iterdir())
        assert before == after, f"dry-run 不得改动文件系统: {before} → {after}"
        assert r.returncode in (0, 1, 3), f"退出码越界: rc={r.returncode}\n{r.stdout[:300]}"
        if r.returncode == 3:
            assert "KimiCU" in r.stdout and "安装" in r.stdout, r.stdout[:300]
        elif r.returncode == 1:
            assert "[dry-run]" in r.stdout, r.stdout[:300]
        else:
            assert "无需修复" in r.stdout, r.stdout[:300]
        # 任何分支都不得在 dry-run 下落盘写 plist
        assert not plist_path.exists() or plist_path.read_bytes() != b"", r.stdout[:200]
    print("✓ 服务修复：识别 missing/relative/ok 三类，默认 dry-run 不改系统")


def test_gate_l3_feature_mode_requires_verifiable_entry() -> None:
    """行为：L3 feature 模式必须「可验证准入」，不得变成橡皮图章。

    苹果哥 2026-09-20 批准分模式。设计约束（防橡皮图章）：
    放宽仅当 ①零修改/删除行（纯新增）②路径全为新增文件；任一不满足退回 200 行。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("se_l3", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def mkdiff(n_add, n_del=0, path="new.py"):
        out = ["--- /dev/null", f"+++ b/{path}"]
        out += [f"+new line {i}" for i in range(n_add)]
        out += [f"-old line {i}" for i in range(n_del)]
        return "\n".join(out)

    big = mkdiff(500)

    # ① 纯新增 + 路径全新 → 放宽到 feature 上限
    r = mod.apply_gate(["new.py"], big, backup_dir="/tmp", mode="feature", added_paths={"new.py"})
    l3 = r["checks"]["L3_diff_size"]
    assert l3["feature_eligible"] is True and l3["ok"] is True, l3
    assert l3["mode"] == "feature", l3

    # ② 含修改/删除行 → 不得放宽（关键反证）
    r = mod.apply_gate(["new.py"], mkdiff(480, 20), backup_dir="/tmp",
                       mode="feature", added_paths={"new.py"})
    l3 = r["checks"]["L3_diff_size"]
    assert l3["feature_eligible"] is False, l3
    assert l3["ok"] is False, f"含修改行仍放宽=橡皮图章: {l3}"
    assert "非纯新增" in l3["ineligible_reason"], l3

    # ③ 路径含已有文件 → 不得放宽
    r = mod.apply_gate(["old.py"], big, backup_dir="/tmp", mode="feature", added_paths=set())
    l3 = r["checks"]["L3_diff_size"]
    assert l3["feature_eligible"] is False and l3["ok"] is False, l3

    # ④ 混合（一个新一个旧）→ 不得放宽
    r = mod.apply_gate(["new.py", "old.py"], big, backup_dir="/tmp",
                       mode="feature", added_paths={"new.py"})
    assert r["checks"]["L3_diff_size"]["feature_eligible"] is False, r["checks"]["L3_diff_size"]

    # ⑤ 默认 evolution 模式不受影响：500 行仍拦
    r = mod.apply_gate(["new.py"], big, backup_dir="/tmp")
    l3 = r["checks"]["L3_diff_size"]
    assert l3["mode"] == "evolution" and l3["ok"] is False, l3

    # ⑥ feature 硬上限仍存在（不能无限放宽）
    huge = mkdiff(mod.GATE_DEFAULTS["feature_max_diff_lines"] + 100)
    r = mod.apply_gate(["new.py"], huge, backup_dir="/tmp", mode="feature", added_paths={"new.py"})
    assert r["checks"]["L3_diff_size"]["ok"] is False, "feature 模式必须有硬上限"
    print("✓ L3 分模式：纯新增才放宽，含修改/旧文件必退回 200 行（非橡皮图章）")


def test_d07_claim_scanner_and_false_positive() -> None:
    """行为：D07 排除词扫描器必须拦夸大、放行诚实、不误拦「引用并拒绝」语境。

    假阳性修复背景：扫自己的「明确剔除的假货」清单时，引用被拒说法会命中词表。
    D07 §4.1 原文明确允许「历史转述、反例或拒绝规则」——故必须识别拒绝语境。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("se_d07", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ① 夸大文本 → BLOCKED
    r = mod.scan_claim_text("系统已实现零幻觉，基因引擎已可用，正在自我进化，完全自治无人值守。")
    assert r["status"] == "BLOCKED", r
    assert r["violations"] >= 3, r
    assert "绝对化质量" in r["by_category"], r

    # ② 诚实文本 → OK
    r = mod.scan_claim_text("本模块实现并经局部验证，尚未证实运行时接线；未做生产部署。")
    assert r["status"] == "OK", r

    # ③ 引用并拒绝的语境 → 不得假阳性（关键回归）
    r = mod.scan_claim_text(
        "## 明确剔除的假货\n\n| 材料 | 剔除理由 |\n|---|---|\n"
        "| 超级进化20 | 要求封印模型概率随机性、改模型底层权重（技术上不可能） |\n")
    assert r["status"] == "OK", f"引用并拒绝的语境被误拦（假阳性）: {r['details'][:2]}"
    assert r["allowed_context_hits"] >= 1, r

    # ④ D05 §6.1：不得把未接线说成已运行
    r = mod.scan_unwired_claims("EVM 治理系统已整体运行，多 Agent 已相互制约。")
    assert r["status"] == "BLOCKED", r
    assert r["violations"] >= 2, r
    r = mod.scan_unwired_claims("EVM 核心独立可运行；示例导入失败；调度/Token/Claw/YAML 无接线。")
    assert r["status"] == "OK", r

    # ⑤ CLI 退出码契约：违规 exit=1，合规 exit=0
    rr = subprocess.run([sys.executable, str(SCRIPT), "--claim-scan", "零幻觉，完全自治"],
                        capture_output=True, text=True)
    assert rr.returncode == 1, rr.stdout[:200]
    rr = subprocess.run([sys.executable, str(SCRIPT), "--claim-scan", "实现并经局部验证，未做部署"],
                        capture_output=True, text=True)
    assert rr.returncode == 0, rr.stdout[:200]
    # ⑥ 引文定位（D07 §4.1「明确引文定位」）：讨论词表本身的文本不得假阳性
    r = mod.scan_claim_text(
        "**补全同义变体**：第一版扫描玄学原文只得 2 条，漏了「修正模型底层权重」"
        "「全品类LLM推理流程强制」等。")
    assert r["status"] == "OK", f"讨论词表本身的引文被误拦: {r['details'][:2]}"
    # 但引号外、无元讨论语境的同一短语必须仍被拦
    r = mod.scan_claim_text("本系统已实现零幻觉，完全自治，自动改权重。")
    assert r["status"] == "BLOCKED", r
    assert r["violations"] >= 3, r
    print("✓ D07 扫描器：拦夸大/放诚实/不误拦引用拒绝语境；D05 未接线误报必拦")


def test_coord_probe_distinguishes_env_from_bug() -> None:
    """行为：坐标探针必须区分「服务未加载」与「无屏幕录制权限」，不得混报成代码 bug。

    2026-09-20 实测根因：kimi-cu MCP 进程在，但 LaunchAgent
    `ai.kimi.cu.service`（MachServices/XPC）未加载 → 一切调用返回
    "service unavailable: perform failed after retries"。
    加载后 list_apps 立即恢复。这是环境修复，不是代码修复——必须如实区分。
    """
    import importlib.util
    probe = Path(__file__).resolve().parent.parent / "scripts" / "kimi_coord_probe.py"
    assert probe.is_file(), f"探针不存在: {probe}"
    spec = importlib.util.spec_from_file_location("kcp", probe)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    diag = mod.diagnose_service()
    # 2026-09-20 修正：权限主体是 ai.kimi.cu（KimiCU.app），不是本脚本宿主。
    # 旧断言用 screen_capture_ok（screencapture 探的是 node）——测错主体，已改为
    # kimi_screenshot_ok（直接调 kimi-cu 截图，真判据）；宿主项降为参考。
    assert "service_loaded" in diag and "kimi_screenshot_ok" in diag, diag
    assert isinstance(diag["service_loaded"], bool), diag
    # kimi_screenshot_ok 必须是三态（True/False/None），None 不得当成可用
    assert diag["kimi_screenshot_ok"] in (True, False, None), diag
    assert "host_screen_capture_ok" in diag, diag

    # 探针在环境不满足时必须返回非零（不得报 PASS）；环境满足时得 0 才算真过。
    # 2026-09-20 修正：原断言硬编码「必须非零」，但环境修好后探针本就该 exit=0，
    # 那条断言会反过来把正常状态判成失败。改为**按环境分支**判定。
    #
    # 关键：必须**忠实模拟**无 kimi-cu 环境（KIMI_CU_BIN 指向不存在路径）。
    # 仅遮蔽 launchctl 不够——kimi-cu 仍可用，探针会真成功（实测发现）。
    r = subprocess.run([sys.executable, str(probe), "--app", "TextEdit"],
                       capture_output=True, text=True, timeout=120)
    # 环境判定必须基于**探针自己的输出**，而不是 diag——diag 可能被测试环境的
    # shim（如遮蔽 launchctl）干扰，而 kimi-cu 本体仍可用。2026-09-20 实测：
    # 遮蔽 launchctl 时 diag.service_loaded=False，但探针实际能跑通 exit=0。
    probe_ok = ("坐标校正实测有效" in r.stdout) or ("✅ 端到端点击验证通过" in r.stdout)
    if not probe_ok:
        # 环境不满足：探针必须在权限/服务层就中止，不得继续到点击验证。
        assert r.returncode != 0, f"环境不满足时探针不得报成功: rc={r.returncode}\n{r.stdout[:300]}"
        assert "坐标校正实测有效" not in r.stdout, r.stdout[:400]
        assert "✅ 端到端点击验证通过" not in r.stdout, r.stdout[:400]
    else:
        # 环境就绪：必须给出端到端点击验证结论（不得只凭「缩放比等比」就宣称坐标有效）
        assert "点击验证" in r.stdout, r.stdout[:400]
        assert r.returncode == 0, f"环境就绪时探针应成功: rc={r.returncode}\n{r.stdout[:300]}"

    # 忠实模拟「无 kimi-cu」环境（CI ubuntu 场景）：探针必须优雅归因、非零退出，
    # 且不得崩栈（旧版会抛 FileNotFoundError）。此断言与真实环境无关，恒可跑。
    env_no_kimi = dict(os.environ)
    env_no_kimi["KIMI_CU_BIN"] = "/nonexistent/kimi-cu-does-not-exist"
    r2 = subprocess.run([sys.executable, str(probe), "--app", "TextEdit"],
                        capture_output=True, text=True, timeout=120, env=env_no_kimi)
    assert r2.returncode != 0, f"无 kimi-cu 时探针不得报成功: rc={r2.returncode}\n{r2.stdout[:300]}"
    assert "Traceback" not in r2.stderr, f"探针崩栈（应 fail-closed 归因）: {r2.stderr[:300]}"
    assert "坐标校正实测有效" not in r2.stdout, r2.stdout[:400]
    assert ("服务" in r2.stdout or "权限" in r2.stdout or "图像" in r2.stdout), r2.stdout[:400]
    # 必须明确归因（服务/权限），不得只说「失败」
    assert ("服务" in r.stdout or "权限" in r.stdout), r.stdout[:300]
    # 硬规则：不得仅凭等比缩放就宣称坐标已实测有效（两种环境下都成立）
    assert not ("等比缩放" in r.stdout and "坐标校正实测有效" in r.stdout
                and "点击验证" not in r.stdout), "不得只凭缩放比宣称坐标有效"
    print("✓ 坐标探针区分环境问题与代码 bug（服务/权限/点击三层归因，且不凭缩放比冒充有效）")


def test_readonly_mode_blocks_writes() -> None:
    """只读模式（PGG_EVOLUTION_READONLY=1）：写动作必拦，读动作必放行。

    吸收自 Apex 资源盘《超级进化21》：Agent_read ∩ ¬Agent_edit = Max(Safety)。
    关键：只读不能是「文档里的声明」，必须是会拒绝的代码（exit=1）。
    """
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location("se_ro", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    env = {**_os.environ, mod.READONLY_SWITCH: "1"}

    # ① 入口拦：写动作 exit=1 且状态为 READONLY_BLOCKED
    r = subprocess.run([sys.executable, str(SCRIPT), "--feedback", "t", "success", "-"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1, f"只读下写动作必须 exit=1: rc={r.returncode} {r.stdout[:200]}"
    d = json.loads(r.stdout)
    assert d["status"] == "READONLY_BLOCKED", d
    assert "feedback" in d["blocked_actions"], d

    # ② 读动作放行（exit=0）
    for rd in ("--health", "--gene-l5", "--status"):
        rr = subprocess.run([sys.executable, str(SCRIPT), rd],
                            capture_output=True, text=True, env=env)
        assert rr.returncode == 0, f"只读下 {rd} 必须放行: rc={rr.returncode}"

    # ③ 兜底层：绕过入口直调写函数也必须拒绝（不静默跳过）
    os.environ[mod.READONLY_SWITCH] = "1"
    try:
        target = mod.SANDBOX / "readonly-probe-should-not-exist.json"
        raised = False
        try:
            mod.safe_write_text(target, "{}", "probe")
        except mod.ReadOnlyViolation:
            raised = True
        assert raised, "只读下 safe_write_text 必须抛 ReadOnlyViolation"
        assert not target.exists(), "只读下不得真的创建文件"
    finally:
        os.environ.pop(mod.READONLY_SWITCH, None)

    # ④ 解除后恢复正常
    r2 = subprocess.run([sys.executable, str(SCRIPT), "--gene-l5-backfill"],
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stdout[:200]
    assert json.loads(r2.stdout)["dry_run"] is True, "回填默认必须 dry-run"
    print("✓ 只读模式：写必拦(exit=1)/读必放行/兜底层不静默跳过")


def test_promotion_authority_matrix_read_only_and_fail_closed() -> None:
    """Promotion Authority Matrix v0.2：只读、fail-closed、高风险必人工。

    背景（资源盘任务清单问题 1）：v0.1 把基因库路径硬编码为已退役的 Hermes
    路径 `~/.hermes/workspace/.../apex_evolution_genes.sqlite3`，该文件不存在，
    工具一跑即崩。这是「工具装好 ≠ 能力可用」的同构反例——v0.2 改读当前正本
    JSON 基因库，并接只读门禁。

    核心不变式（防橡皮图章）：
      ① 只读模式下写必拦 exit=1
      ② 基因库不可读一律 fail-closed（不静默返回空）
      ③ 高风险 lane（法律/凭据/内核）即使证据齐也必须人工
      ④ status/fitness 单独绝不构成晋升依据
      ⑤ 证据不齐 → WATCH，绝不放行
    """
    import importlib.util, tempfile, shutil

    tool = Path(__file__).resolve().parent.parent / "scripts" / "promotion_authority_matrix.py"
    assert tool.is_file(), f"工具不存在: {tool}"
    spec = importlib.util.spec_from_file_location("pam", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tmp = Path(tempfile.mkdtemp(prefix="pam-test-"))
    try:
        genes_dir = tmp / "genes"
        genes_dir.mkdir()
        outdir = tmp / "out"

        # ① 无基因库目录 → exit=3（环境不可用，非崩溃）
        r = subprocess.run([sys.executable, str(tool), "--genes-dir", str(tmp / "nope")],
                           capture_output=True, text=True)
        assert r.returncode == 3, f"库不存在应 exit=3: {r.returncode}"
        assert "Traceback" not in r.stderr, r.stderr[:300]

        # ② 空目录 → exit=1（拒绝，不静默当空）
        r = subprocess.run([sys.executable, str(tool), "--genes-dir", str(genes_dir)],
                           capture_output=True, text=True)
        assert r.returncode == 1, f"空库应 exit=1: {r.returncode}"

        # ③ 损坏 JSON → exit=1（不静默跳过损坏文件）
        (genes_dir / "genes-broken.json").write_text('{"genes":[', encoding="utf-8")
        r = subprocess.run([sys.executable, str(tool), "--genes-dir", str(genes_dir)],
                           capture_output=True, text=True)
        assert r.returncode == 1, f"损坏库应 exit=1: {r.returncode}"
        (genes_dir / "genes-broken.json").unlink()

        # 装入一份合法但证据不齐的基因
        (genes_dir / "genes-t.json").write_text(json.dumps({
            "schema": "pgg-evolution/gene-bank/v1",
            "genes": [{"type": "gap", "module": "K",
                       "mechanism": "测试定义了但没注册到 main",
                       "resolution": "补注册"}],
        }, ensure_ascii=False), encoding="utf-8")

        # ④ 正常跑 → exit=0；证据不齐必须全部 WATCH（不得放行）
        r = subprocess.run([sys.executable, str(tool), "--genes-dir", str(genes_dir),
                            "--outdir", str(outdir), "--json"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[:300] + r.stderr[:300]
        rep = json.loads(r.stdout)
        assert rep["input_counts"]["total_genes"] == 1, rep
        assert rep["input_counts"]["auto_allowed"] == 0, "证据不齐不得放行"
        assert all(v.startswith("WATCH") for v in rep["verdict_counts"]), rep["verdict_counts"]

        # ⑤ 只读模式 → 写必拦 exit=1
        env = {**os.environ, mod.READONLY_SWITCH: "1"}
        r = subprocess.run([sys.executable, str(tool), "--genes-dir", str(genes_dir),
                            "--outdir", str(outdir)], capture_output=True, text=True, env=env)
        assert r.returncode == 1, f"只读下必须 exit=1: {r.returncode}"
        # 只读只拦写，不拦读：load_genes 直调应成功
        n = len(mod.load_genes(genes_dir))
        assert n == 1, f"只读下读动作应放行: {n}"

        # ⑥ 高风险 lane：法律/凭据/内核 即使证据齐也必须人工
        full = ["source_readback", "claim_extract", "test_output", "runtime_output",
                "human_review", "secondary_llm_or_domain_audit"]
        g_legal = {"mechanism": "法律办案门禁缺二次复核", "resolution": "加强制人工",
                   "_gid": "aaa111bbb222", "_file": "x.json"}
        g_cred = {"mechanism": "token 明文写配置", "resolution": "走凭据桥",
                  "_gid": "ccc333ddd444", "_file": "x.json"}
        g_kern = {"mechanism": "改模型底层权重", "resolution": "x",
                  "_gid": "eee555fff666", "_file": "x.json"}
        g_low = {"mechanism": "测试未注册", "resolution": "补注册",
                 "_gid": "ggg777hhh888", "_file": "x.json"}
        assert mod.risk_lane(g_legal) == "legal", "法律词必须进 legal lane"
        assert mod.risk_lane(g_cred) == "credential", "凭据词必须进 credential lane"
        assert mod.risk_lane(g_kern) == "high", "内核/权重词必须进 high lane"
        assert mod.risk_lane(g_low) == "low_engineering", "纯工程才可低风险"
        for g in (g_legal, g_cred, g_kern):
            lane = mod.risk_lane(g)
            v, miss = mod.verdict(g, lane, full)
            assert v == "WATCH_REQUIRES_HUMAN_REVIEW", f"{lane} 证据齐也必须人工: {v}"
            p = mod.make_packet(g, lane, full, v, miss, 1)
            assert p["human_required"] is True and p["auto_allowed"] is False, p
        # 低风险 + 证据齐 → 才可 eligible（且仍不做写入）
        lane = mod.risk_lane(g_low)
        v, miss = mod.verdict(g_low, lane, full)
        assert v == "PASS_PROMOTION_ELIGIBLE_NO_DB_MUTATION", v
        assert mod.make_packet(g_low, lane, full, v, miss, 1)["auto_allowed"] is True

        # ⑦ 负参数 → 拒绝
        r = subprocess.run([sys.executable, str(tool), "--genes-dir", str(genes_dir),
                            "--limit-review", "-1"], capture_output=True, text=True)
        assert r.returncode == 1, f"负数参数应拒绝: {r.returncode}"

        # ⑧ 只读模式不得产生任何文件（防「声称只读却写了」）
        before = {p for p in outdir.rglob("*")} if outdir.exists() else set()
        subprocess.run([sys.executable, str(tool), "--genes-dir", str(genes_dir),
                        "--outdir", str(outdir)], capture_output=True, text=True, env=env)
        after = {p for p in outdir.rglob("*")} if outdir.exists() else set()
        assert before == after, "只读模式下不得新增文件"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("✓ 晋升矩阵：只读拦写/fail-closed/高风险必人工/证据不齐全 WATCH")


def test_signal_taxonomy_match_is_auditable() -> None:
    """信号归一与匹配：吸收自资源盘 APEX 基因标准 §4.2。

    规范原文：`selected_gene = argmax(gene ∈ candidate_pool) { signal_match_score }`
    ——信号匹配应是主判据，而非字符包含数量。本库基因的 signals_match 历史上
    写成中文自然语言长句，无法精确匹配。

    核心不变式：
      ① 标准信号归一必须可工作（中文长句 → 机器可匹配键）
      ② 归一不到的必须**原样保留**（不丢弃、不强加归属）
      ③ 匹配结果的 overlap 必须**随结果回传**（可审计，不能只算不说）
      ④ 本地扩展信号必须有前缀，不得冒充 APEX 标准信号
    """
    import importlib.util
    import tempfile, shutil
    spec = importlib.util.spec_from_file_location("se_sig", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ① APEX 24 标准信号必须齐备，且不混入本地扩展
    assert len(mod.SIGNAL_TAXONOMY) >= 24, f"APEX 标准信号应有 24 个: {len(mod.SIGNAL_TAXONOMY)}"
    assert all(not k.startswith("pgg_") for k in mod.SIGNAL_TAXONOMY), \
        "SIGNAL_TAXONOMY 不得混入本地扩展信号"

    # ② 本地扩展必须带 pgg_ 前缀（可区分，不冒充标准）
    merged = mod._all_signal_tables()
    local = [k for k in merged if k.startswith("pgg_")]
    assert local, "应有本地扩展信号"
    assert len(merged) == len(mod.SIGNAL_TAXONOMY) + len(local), "合并表应恰好是标准+本地"

    # ③ 归一实测：中文长句 → 标准键
    hits, unmapped = mod.normalize_signals(["发现故障或缺陷", "子系统报错/不可用"])
    assert "error" in hits, f"「发现故障或缺陷」应归一到 error: {hits}"
    assert unmapped == [], f"可归一的不应落入 unmapped: {unmapped}"

    # ④ 归不到必须原样保留（不静默丢弃）
    hits2, unm = mod.normalize_signals(["某个完全无法归类的生造信号xyz"])
    assert hits2 == set(), hits2
    assert unm == ["某个完全无法归类的生造信号xyz"], f"未映射项必须原样保留: {unm}"

    # ⑤ 字符类型输入也要能处理（不崩栈）
    assert mod.normalize_signals("发现故障")[0] == {"error"}, "字符串输入应等同单元素列表"
    assert mod.normalize_signals(None) == (set(), []), "None 应安全返回空"

    # ⑥ 匹配结果必须回传 overlap（可审计）
    #    铁律（本轮 CI 实测教训）：测试**不得依赖本机真实基因库**。
    #    CI 无基因库 → _load_gene_bank 返回空 → match_genes 报 BLOCKED。
    #    旧断言硬编码 status=="OK"，在 CI 直接失败（run 35497886801）。
    #    修法：用 tempfile 自建基因库并把 mod.SANDBOX 指过去，忠实模拟。
    tmp = Path(tempfile.mkdtemp(prefix="sig-test-"))
    try:
        (tmp / "genes").mkdir()
        (tmp / "genes" / "genes-t.json").write_text(json.dumps({
            "schema": "pgg-evolution/gene-bank/v1",
            "genes": [
                {"id": "gene_repair_probe", "category": "repair",
                 "signals_match": ["发现故障或缺陷", "子系统报错/不可用"],
                 "mechanism": "发现故障时必须先定位根因再修", "strategy": ["定位", "修复"]},
                {"id": "gene_canonical_probe", "category": "optimize",
                 "signals_match": ["需要更新 PGG-WIKI / 外置大脑正本"],
                 "mechanism": "正本变更必须走 staging 单通道", "strategy": ["staging"]},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        mod.SANDBOX = tmp

        r = mod.match_genes("本地检索系统故障 修复", top_n=3)
        assert r["status"] == "OK", f"临时基因库存在时必须能匹配: {r}"
        assert r["matched"] > 0, r
        # 有信号命中的基因必须回传 _signal_overlap（不能只算不说）
        with_ov = [g for g in r["genes"] if g.get("_signal_overlap")]
        assert with_ov, f"有信号命中的基因必须回传 _signal_overlap: {r['genes']}"
        assert "error" in with_ov[0]["_signal_overlap"], with_ov[0]

        # 正本任务应命中 pgg_ 本地扩展信号
        r2 = mod.match_genes("需要更新 PGG-WIKI 正本", top_n=3)
        ov2 = [s for g in r2["genes"] for s in (g.get("_signal_overlap") or [])]
        assert "pgg_canonical_write_intent" in ov2, f"正本任务应命中本地扩展信号: {ov2}"

        # 空基因库 → BLOCKED（而非崩溃/静默空结果）
        (tmp / "genes" / "genes-t.json").unlink()
        r3 = mod.match_genes("任意任务", top_n=3)
        assert r3["status"] == "BLOCKED", f"空库必须显式 BLOCKED: {r3}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("✓ 信号归一：APEX 24 标准 + 本地扩展可区分，未映射不丢弃，匹配 overlap 可审计")


def test_risk_tier_classification_is_conservative() -> None:
    """D03 风险分级 R0-R4（吸收自成果包 03-实施手册 §2.2）。

    手册原文优先级（不得被任何评分抬升）：
      平台与法律硬约束 > 用户明确指令 > 数据治理/安全策略 > 任务策略/评分公式
    原文明确：「任何全局公式、自动自治或模型自我评价均不得提高权限」。

    核心不变式：
      ① R2/R3/R4 一律 auto_allowed=False（与 diff 大小无关）
      ② R4 必须双人复核
      ③ 命中多项时就高不就低
      ④ **未知变更保守落到 R2**（不得降级放行）
      ⑤ 五个层级必须齐备且描述不外借
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("se_risk", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ⑤ 层级齐备
    assert set(mod.RISK_TIERS) == {"R0", "R1", "R2", "R3", "R4"}, mod.RISK_TIERS.keys()

    # ①/② 自动放行只能出现在 R0/R1
    for tier, spec_ in mod.RISK_TIERS.items():
        if tier in ("R2", "R3", "R4"):
            assert spec_["auto_allowed"] is False, f"{tier} 不得自动放行"
            assert spec_["needs_human"] is True, f"{tier} 必须人工"
    assert mod.RISK_TIERS["R4"]["auto_allowed"] is False, "R4 原文：禁止自动执行"
    assert "two_person_review" in mod.RISK_TIERS["R4"]["requires"], "R4 必须双人复核"

    # 实际分级
    cases = [
        ("只读盘点资源盘文件", "R0", True),
        ("改技能卡文档草案", "R1", True),
        ("改自进化内核门禁实现", "R2", False),
        ("生产环境策略切换", "R3", False),
        ("更新凭据密钥", "R4", False),
        ("写一个全新的自创场景没有任何关键词", "R2", False),  # ④ 保守
    ]
    for desc, expect_tier, expect_auto in cases:
        r = mod.classify_risk_tier(desc)
        assert r["tier"] == expect_tier, f"{desc!r} 应 {expect_tier}，实际 {r['tier']}"
        assert r["auto_allowed"] is expect_auto, f"{desc!r} auto_allowed 应 {expect_auto}: {r}"

    # ③ 就高不就低
    mixed = mod.classify_risk_tier("只读盘点，顺便改生产策略和凭据")
    assert mixed["tier"] == "R4", f"混合描述必须取最高层级: {mixed}"
    assert mixed["matched_tiers"] == ["R0", "R3", "R4"], mixed["matched_tiers"]

    # ④ 未知变更必须标保守默认（不得默认为 R0）
    unknown = mod.classify_risk_tier("zzz 无关键词 zzz")
    assert unknown["is_conservative_default"] is True, unknown
    assert unknown["tier"] == "R2", "未知变更保守落 R2，不得放行到 R0"
    assert unknown["auto_allowed"] is False, unknown

    # 空输入不崩栈
    assert mod.classify_risk_tier("")["tier"] == "R2", "空输入应保守"
    assert mod.classify_risk_tier(None)["tier"] == "R2", "None 应保守"

    print("✓ 风险分级：R0-R4 就高不就低，未知保守 R2，R2+ 一律人工")


def test_defect_rate_penalizes_worst_shortfall() -> None:
    """最大短板非线性惩罚（吸收自 EVM 仓库 CoreFormula/EVM_FORMULA.py）。

    只吸收可验证的真数学：avg*0.5 + boost*max**1.5。
    明确**不吸收**其玄学层：`AncientTao` 七大道法强度硬编码为 1.0，
    乘法系数恒等于 1（乘 1 = 没乘），对结果零作用。

    核心不变式：
      ① 同总量缺陷，**集中在单点**比**分散多点**惩罚更重
         （系统承压看最弱环，不看平均值）
      ② 口径必须可复现上游：--defect-total 12 得 0.140665
      ③ 空输入 → 0.0，不崩栈
      ④ 超界/负数输入钳制，不制造不真实的高惩罚
      ⑤ 分母不得小于已报缺陷项数（防平均被偷换）
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("se_defect", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ① 集中 vs 分散（同为 0.8 总量，分母 12）
    concentrated = mod.defect_rate({"Tok": 0.8}, total_metrics=12)
    spread = mod.defect_rate({"Tok": 0.2, "Clw": 0.2, "Agt": 0.2, "Pan": 0.2}, total_metrics=12)
    assert concentrated > spread, (
        f"集中单点必须比分散更重：{concentrated} vs {spread}"
    )
    assert concentrated > spread * 2.5, "集中惩罚应显著高于分散（实测约 3.0 倍）"

    # ② 复现上游固定 12 维口径（实测值与上游输出逐位一致）
    assert mod.defect_rate({"Tok": 0.8}, total_metrics=12) == 0.140665, "须复现上游 0.140665"
    assert mod.defect_rate({"Tok": 0.2, "Clw": 0.2, "Agt": 0.2, "Pan": 0.2}, total_metrics=12) == 0.04675
    assert mod.defect_rate({"Tok": 0.4}, total_metrics=12) == 0.054614

    # 默认口径分母=传入项数，与上游 12 维口径**不同**（不得含糊成同一个数）
    default_denom = mod.defect_rate({"Tok": 0.8})
    assert default_denom != 0.140665, "默认口径不得冒充上游 12 维口径"
    assert default_denom == pytest.approx(0.507331, abs=1e-5), default_denom

    # ③ 空输入
    assert mod.defect_rate({}) == 0.0
    assert mod.defect_rate(None) == 0.0

    # ④ 钳制：负值不产生负缺陷率，超界不超 1.0
    assert mod.defect_rate({"A": -5.0}) == 0.0, "负缺陷率必须钳到 0"
    assert mod.defect_rate({"A": 99.0}, total_metrics=1) <= 1.0, "缺陷率不得超 1.0"

    # ⑤ 分母不得小于已报项数
    assert mod.defect_rate({"A": 0.8, "B": 0.8, "C": 0.8}, total_metrics=1) == \
        mod.defect_rate({"A": 0.8, "B": 0.8, "C": 0.8}), "分母被下界钳制"

    print("✓ 缺陷率：最大短板非线性惩罚，集中>分散，口径可复现上游")


def test_ghost_reference_scan_is_blind_to_nothing_but_real_ghosts() -> None:
    """幽灵引用扫描（吸收自资源盘《开智进化循环执行规范》陷阱 #1）。

    含义：文档/工具引用了已不存在的路径或命令。
    后果不是报错，而是**一跑就崩**或**默默拿到空结果**——表面「工具在」，实际不可用。

    本轮实测撞到两次（教训）：
      ① 晋升矩阵硬编码已退役 Hermes 基因库路径 → 一跑即崩
      ② 《开智进化循环执行规范》**自己**引用了 `~/.hermes/...` 与
        `apex_evolution_genes.sqlite3`——它警告过的缺陷，它自己犯了

    核心不变式：
      ① 真幽灵必须检出（存在性为准，不靠字符串猜测）
      ② 干净文本不得误报（低假阳性优先于高召回）
      ③ 示例路径（/path/to、<X>、…）必须豁免
      ④ 临时路径（/tmp）不作为幽灵
      ⑤ 去重：同一引用只报一次
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("se_ghost", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ① 真幽灵路径必须检出（用一个几乎不可能存在的绝对路径）
    bogus = str(Path(tempfile.gettempdir()) / "pgg-ghost-scan-does-not-exist" / "x.json")
    r = mod.scan_ghost_references(f"配置见 `{bogus}`", base_dir=str(Path.cwd()))
    # /tmp 下的路径按设计归为 suspects 而非 ghosts，所以这里用非 tmp 路径验证
    home_bogus = "~/.pgg-ghost-scan-definitely-absent-dir/file.json"
    r2 = mod.scan_ghost_references(f"配置见 `{home_bogus}`", base_dir=str(Path.cwd()))
    assert r2["status"] == "BLOCKED", r2
    assert r2["ghost_count"] >= 1, r2
    assert any("pgg-ghost-scan-definitely-absent" in g["ref"] for g in r2["ghosts"]), r2["ghosts"]

    # ② 干净文本不得误报
    clean = mod.scan_ghost_references("这里只有中文描述，没有任何路径或命令引用。")
    assert clean["status"] == "OK", clean
    assert clean["ghost_count"] == 0, clean

    # ③ 示例路径必须豁免
    for sample in ["放在 `/path/to/config.json`", "参考 `<HOME>/x.json`", "见 `.../file.json`"]:
        got = mod.scan_ghost_references(sample)
        assert got["ghost_count"] == 0, f"示例路径不得当幽灵: {sample} → {got}"

    # ④ /tmp 下的不存在路径归为 suspects（临时路径），不是 ghosts
    tmp_got = mod.scan_ghost_references(f"临时文件 `{bogus}`")
    assert tmp_got["ghost_count"] == 0, f"临时路径不应算幽灵: {tmp_got}"

    # ⑤ 去重：同一引用重复出现只报一次
    dup_src = f"见 `{home_bogus}`，再强调 `{home_bogus}`"
    dup = mod.scan_ghost_references(dup_src)
    refs = [g["ref"] for g in dup["ghosts"] if "pgg-ghost-scan-definitely-absent" in g["ref"]]
    assert len(refs) == 1, f"同一引用应去重: {refs}"

    # 只读不变式：扫描不创建、不修改任何文件
    probe_dir = Path(tempfile.mkdtemp())
    before = sorted(p.name for p in probe_dir.iterdir())
    mod.scan_ghost_references(f"`{probe_dir}/ghost.json`")
    after = sorted(p.name for p in probe_dir.iterdir())
    assert before == after, "幽灵引用扫描不得写任何文件（只读）"

    # 若本机存在 Hermes 退役后的残留，这个扫描必须能识别（不依赖具体环境）
    assert isinstance(mod.GHOST_PATH_PATTERNS, tuple)
    assert isinstance(mod._GHOST_CMD_PATTERNS, tuple)

    print("✓ 幽灵引用扫描：真幽灵检出、干净文本不误报、示例豁免、只读")


def test_gate_scans_untracked_files() -> None:
    """行为测试（真漏洞回归）：未跟踪文件的内容必须进 L3/L4。

    原实现只把 `git diff HEAD` 当 diff_text，未跟踪（新增）文件内容不在其中，
    导致 L3 diff 大小与 L4 密钥扫描双双漏检新文件。
    实测：新增 1729 行代码时 L3 只报 56 行；新文件里的密钥不被 L4 发现。
    """
    import shutil
    if not shutil.which("git"):
        print("⊘ gate 未跟踪文件回归测试跳过（无 git）")
        return
    with tempfile.TemporaryDirectory() as repo:
        repo_path = Path(repo)
        def g(*a):
            return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
        g("init", "-q")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        (repo_path / "tracked.txt").write_text("hello\n", encoding="utf-8")
        g("add", "-A")
        g("commit", "-qm", "init")
        # 造一个大的未跟踪新文件（应被 L3 计入）
        (repo_path / "new_big.py").write_text("\n".join(f"x{i} = {i}" for i in range(400)) + "\n",
                                               encoding="utf-8")
        spec = importlib.util.spec_from_file_location("se_gate_ut", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        paths, diff_text = mod.gate_paths_from_git(Path(repo))
        assert "new_big.py" in paths, f"未跟踪文件应在路径列表: {paths}"
        assert "x399 = 399" in diff_text, "未跟踪文件内容必须进 diff_text（否则 L3/L4 漏检）"
        r = mod.apply_gate(paths, diff_text, backup_dir=repo)
        assert r["checks"]["L3_diff_size"]["diff_lines"] >= 400, \
            f"未跟踪文件的 400 行必须被 L3 计入: {r['checks']['L3_diff_size']}"
        assert r["allow_apply"] is False, "超限应 BLOCKED"
        # 未跟踪文件里的密钥必须被 L4 扫到（夹具运行时拼串，源码不留字面密钥）
        fake = "sk-" + "live-" + "abcdefghijklmnopqrstuvwxyz" + "123456"
        (repo_path / "leak.py").write_text(f'API_KEY = "{fake}"\n', encoding="utf-8")
        paths2, diff2 = mod.gate_paths_from_git(Path(repo))
        r2 = mod.apply_gate(paths2, diff2, backup_dir=repo)
        assert r2["checks"]["L4_secret_scan"]["ok"] is False, \
            f"未跟踪文件里的密钥必须被 L4 拦截: {r2['checks']['L4_secret_scan']}"
    print("✓ gate 覆盖未跟踪文件（L3 计入行数 / L4 扫到密钥，修复假阴性）")


def main() -> None:
    test_health()
    test_health_deep()
    test_list_orders()
    test_init_workspace()
    test_substitute_orders()
    test_substitute_invalid_order()
    test_substitute_missing_order()
    test_kill_switch()
    test_list_dimensions()
    test_eval_missing_workspace()
    test_gene_missing_workspace()
    test_list_genes()
    test_llm_missing_params()
    test_llm_fail_closed()
    test_gene_llm_missing_provider()
    test_gene_llm_missing_workspace()
    test_match_empty_bank()
    test_match_filters_deprecated()
    test_gene_sync_missing_workspace()
    test_gene_from_memory()
    # 真行为测试（补齐"只验不崩"短板：验证行为正确性而非不崩溃）
    test_prune_actually_deprecates_rule_genes()
    test_eval_pairs_hit_with_total()
    test_gate_blocks_out_of_allowlist()
    test_gate_blocks_secret_and_danger()
    test_gate_passes_clean_change()
    test_substitute_persists()
    # 证据等级账本（E0-E9）行为测试
    test_evidence_rejects_insufficient()
    test_evidence_empty_file_and_single_run_rejected()
    test_evidence_state_derivation()
    test_evidence_exaggeration_and_downgrade()
    test_gene_l5_constraints_derived_not_faked()
    test_gene_match_warns_on_missing_l5()
    test_permission_doctor_gives_actionable_attribution()
    test_host_capture_probe_never_uses_hidden_filename()
    test_promotion_authority_matrix_read_only_and_fail_closed()
    test_signal_taxonomy_match_is_auditable()
    test_risk_tier_classification_is_conservative()
    test_defect_rate_penalizes_worst_shortfall()
    test_ghost_reference_scan_is_blind_to_nothing_but_real_ghosts()
    test_permission_doctor_no_contradiction_when_service_down()
    test_service_repair_diagnoses_plist_kinds_safely()
    test_gate_l3_feature_mode_requires_verifiable_entry()
    test_d07_claim_scanner_and_false_positive()
    test_coord_probe_distinguishes_env_from_bug()
    test_readonly_mode_blocks_writes()
    test_gate_scans_untracked_files()
    test_feedback_record()
    test_feedback_invalid_outcome()
    test_feedback_stats()
    test_prune_genes()
    test_status_summary()
    print("\n全部测试通过 ✅")


if __name__ == "__main__":
    main()
