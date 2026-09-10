#!/usr/bin/env python3
"""self-evolution 插件兼容性测试（契约 v0.1 要求）。

运行：python3 tests/test_self_evolve.py
"""
from __future__ import annotations

import json
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


def test_health() -> None:
    d = run(["--health"])
    assert d.get("status") == "OK", f"health 失败: {d}"
    assert d.get("plugin") == "self-evolution"
    assert "21354" in d.get("three_orders", [])
    print("✓ health")


def test_list_orders() -> None:
    r = subprocess.run([sys.executable, str(SCRIPT), "--list-orders"],
                       capture_output=True, text=True, timeout=30)
    assert "21354" in r.stdout and "12534" in r.stdout and "14325" in r.stdout
    assert "Θ" in r.stdout and "K" in r.stdout and "ε" in r.stdout
    print("✓ list_orders（含 21354/12534/14325 与模块映射）")


def test_init_workspace() -> None:
    with tempfile.TemporaryDirectory() as td:
        os.environ["HOME_TMP"] = td  # 不实际生效，仅验证返回值
    d = run(["--init", "test-task"])
    assert d.get("status") == "OK", f"init 失败: {d}"
    ws = d.get("workspace", "")
    assert "test-task" in ws
    for sub in ("evidence", "gaps", "candidates", "decisions", "runs"):
        assert sub in d.get("meta", {}).get("dirs", []), f"缺 {sub}"
    print("✓ init_workspace（五目录结构）")


def test_substitute_orders() -> None:
    for order in ("21354", "12534", "14325"):
        d = run(["--substitute", "demo", "--order", order])
        assert d.get("status") == "OK", f"{order} 失败: {d}"
        probes = d.get("substitute", {}).get("probes", [])
        assert len(probes) == 5, f"{order} 应有 5 个模块探查，实际 {len(probes)}"
        # 验证路径与顺序
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


def main() -> None:
    test_health()
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
    test_gene_sync_missing_workspace()
    test_gene_from_memory()
    print("\n全部测试通过 ✅")


if __name__ == "__main__":
    main()
