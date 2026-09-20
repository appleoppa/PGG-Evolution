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
    test_gate_scans_untracked_files()
    test_feedback_record()
    test_feedback_invalid_outcome()
    test_feedback_stats()
    test_prune_genes()
    test_status_summary()
    print("\n全部测试通过 ✅")


if __name__ == "__main__":
    main()
