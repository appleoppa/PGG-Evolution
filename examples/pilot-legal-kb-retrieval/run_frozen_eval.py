#!/usr/bin/env python3
"""冻结评测 runner — 本地法条检索基线评测（自进化闭环 Batch0）。

用法：
  python3 run_frozen_eval.py               # 跑全部
  python3 run_frozen_eval.py --task 3-1    # 只跑 3-1
  python3 run_frozen_eval.py --verbose     # 显示每条结果

调用本地法律知识库 CLI：legal-kb-search / legal-kb-article-search（路径可用环境变量 LEGAL_KB_CLI_DIR 覆盖，默认按 PATH 查找）
评测集：evidence/frozen-eval-set-001.json
输出：runs/frozen-eval-<timestamp>.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL_SET = HERE / "evidence" / "frozen-eval-set-001.json"


def _find_cli(name: str) -> str:
    """优先环境变量指定目录，其次 PATH，找不到返回名字（调用时报错更清晰）。"""
    cli_dir = os.environ.get("LEGAL_KB_CLI_DIR")
    if cli_dir:
        p = Path(cli_dir) / name
        if p.exists():
            return str(p)
    found = shutil.which(name)
    return found if found else name


CLI_SEARCH = _find_cli("legal-kb-search")
CLI_ARTICLE = _find_cli("legal-kb-article-search")


def cn2num(s: str) -> int:
    if s.isdigit():
        return int(s)
    cmap = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
            "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000, "万": 10000}
    total, current = 0, 0
    for ch in s:
        if ch not in cmap:
            continue
        v = cmap[ch]
        if v >= 10:
            total += (current or 1) * v
            current = 0
        else:
            current = v
    return total + current


def extract_article_nums(text: str) -> list[int]:
    nums = re.findall(r"第([一二三四五六七八九十百千万零〇两\d、]+)条", text)
    out = []
    for n in nums:
        for part in n.split("、"):
            part = part.strip()
            if part:
                out.append(cn2num(part))
    return out


def run_cli(cli: str, args: list[str], timeout: int = 40) -> dict:
    try:
        r = subprocess.run([cli] + args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            try:
                return json.loads(r.stdout)
            except Exception:
                return {"status": "CLI_ERROR", "reason": r.stdout.strip()[:200]}
        return json.loads(r.stdout)
    except Exception as e:
        return {"status": "RUN_ERROR", "reason": str(e)}


def eval_3_1(sample: dict) -> dict:
    resp = run_cli(CLI_ARTICLE, [sample["query"], "--law-title", "中华人民共和国刑法", "--limit", "5"])
    if resp.get("status") != "OK":
        return {"status": resp.get("status", "FAIL"), "reason": resp.get("reason")}
    kb_nums = []
    for h in resp.get("results", []):
        kb_nums.extend(extract_article_nums(h.get("article_no", "")))
    hit = any(e in kb_nums for e in sample["expected_article"])
    return {"status": "OK", "hit": hit, "kb_article_nums": kb_nums[:8]}


def eval_3_2(sample: dict) -> dict:
    law = sample.get("expected_law", "")
    # 用短关键词而不是整句长查询（长查询全文 LIKE 会落空；短关键词命中率更高）
    keywords = sample.get("keywords", sample["query"][:30])
    resp = run_cli(CLI_ARTICLE, [keywords, "--law-title", law, "--limit", "10"])
    if resp.get("status") != "OK":
        return {"status": resp.get("status", "FAIL"), "reason": resp.get("reason")}
    kb_nums = []
    for h in resp.get("results", []):
        kb_nums.extend(extract_article_nums(h.get("article_no", "")))
    hit = any(e in kb_nums for e in sample["expected_article"])
    return {"status": "OK", "hit": hit, "kb_article_nums": kb_nums[:8]}


def eval_3_3(sample: dict) -> dict:
    # 7-5 基准方法：罪名名 + "罪名" 直接检索（KB 是罪名知识库）
    # 案件事实→罪名 的语义映射是另一条链路（见候选 cand-20260910-002）
    crime = sample["expected_crime"][0]
    resp = run_cli(CLI_SEARCH, [f"{crime} 罪名", "--collection", "法律法规库", "--limit", "5"])
    if resp.get("status") != "OK":
        return {"status": resp.get("status", "FAIL"), "reason": resp.get("reason")}
    hit = False
    for h in resp.get("results", []):
        title = h.get("title", "")
        if "罪名" in title or crime in title:
            hit = True
            break
    return {"status": "OK", "hit": hit, "top_titles": [h.get("title", "")[:40] for h in resp.get("results", [])]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["3-1", "3-2", "3-3", "all"], default="all")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--set", choices=["warmup", "holdout", "holdout2", "all"], default="warmup",
                    help="warmup=冻结集(可调优) holdout=分离集(禁看) all=两者")
    args = ap.parse_args()

    HOLD_SET = HERE / "evidence" / "holdout-set-001.json"
    HOLD_SET2 = HERE / "evidence" / "holdout-set-002.json"
    if args.set in ("warmup", "all"):
        data = json.loads(EVAL_SET.read_text(encoding="utf-8"))
        samples = [s for s in data["samples"] if args.task == "all" or s["task"] == args.task]
    else:
        samples = []
    if args.set in ("holdout", "all") and HOLD_SET.is_file():
        hdata = json.loads(HOLD_SET.read_text(encoding="utf-8"))
        samples += [s for s in hdata["samples"] if args.task == "all" or s["task"] == args.task]
    if args.set in ("holdout2", "all") and HOLD_SET2.is_file():
        h2 = json.loads(HOLD_SET2.read_text(encoding="utf-8"))
        samples += [s for s in h2["samples"] if args.task == "all" or s["task"] == args.task]

    results = []
    for s in samples:
        fn = {"3-1": eval_3_1, "3-2": eval_3_2, "3-3": eval_3_3}[s["task"]]
        r = fn(s)
        r.update({"sample_id": s["id"], "task": s["task"]})
        results.append(r)
        if args.verbose:
            print(f"[{s['id']}] {s['task']} → {'✓' if r.get('hit') else '✗'} {r.get('reason','')}")

    # 汇总
    summary = {}
    for task in ("3-1", "3-2", "3-3"):
        t = [r for r in results if r["task"] == task]
        if not t:
            continue
        ok = sum(1 for r in t if r.get("hit"))
        summary[task] = {"hit": ok, "total": len(t), "rate": round(ok / len(t), 3)}
    overall_ok = sum(1 for r in results if r.get("hit"))
    summary["overall"] = {"hit": overall_ok, "total": len(results),
                          "rate": round(overall_ok / len(results), 3)}

    payload = {
        "schema": "pgg-evolution-pilot/frozen-eval-run/v1",
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "eval_set": (data["set_id"] if args.set in ("warmup", "all") else ("holdout2" if args.set == "holdout2" else "holdout")),
        "set": args.set,
        "summary": summary,
        "results": results,
    }
    out = HERE / "runs" / f"frozen-eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
