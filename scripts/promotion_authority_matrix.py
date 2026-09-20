#!/usr/bin/env python3
"""PGG Promotion Authority Matrix v0.2 — 只读晋升/降级证据包生成器。

## 为什么有 v0.2

v0.1（2026-08-15）把基因库路径硬编码为已退役的 Hermes 路径：
    ~/.hermes/workspace/04_knowledge/开智/02-进化基因/apex_evolution_genes.sqlite3
Hermes 退役后该文件不存在，工具**一跑即崩**（实测 2026-09-20：

    File ".../pgg-promotion-authority-matrix", line 140, in main
        rows=load_rows(db)
    sqlite3.OperationalError: unable to open database file

即 §2 红线「文件存在/服务启动 ≠ 能力完成」的同构反例：工具装好了，
但它依赖的世界已经不存在。这正是资源盘《开智进化循环执行规范》自列的
「规范文件幽灵引用」缺陷——该规范自己警告过，自己却犯了。

v0.2 改读当前正本基因库（`~/.pi/agent/evolution/genes/*.json`），
保留 v0.1 的核心判定逻辑，并接入本仓库的只读门禁。

## 边界（硬约束）

- **只读基因库**：不写、不改、不删任何基因。
- **不自动晋升**：只产出证据包（proof packet），晋升动作留给人工/门禁。
- **不自动退役**：降级风险只标 `WATCH_DOWNGRADE_REVIEW_REQUIRED`。
- **fitness/status 单独不足以晋升**：必须补齐所需证据。
- **高风险必须人工**：法律/凭据/配置/调度/安全 lane 一律 `human_required=true`。

## 退出码

    0  报告已生成
    1  拒绝（基因库不可读 / 路径不存在 / 只读模式写拦截）
    3  环境不可用（无基因库目录）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ── 路径（可用环境变量覆盖，便于测试与换宿主）──────────────────────────
SANDBOX = Path(os.environ.get("PGG_EVOLUTION_SANDBOX") or (Path.home() / ".pi" / "agent" / "evolution"))
GENES_DIR = SANDBOX / "genes"

READONLY_SWITCH = "PGG_EVOLUTION_READONLY"

# ── 判定词表 ─────────────────────────────────────────────────────────
REJECT_WORDS = {"rejected", "retired", "failed", "blocked", "deprecated"}
PROMOTED_WORDS = {"promoted", "active", "verified", "pass", "pass_ready", "approved"}
CANDIDATE_WORDS = {"candidate", "pending", "watch", "needs_review", "draft", "new"}

# 高风险 lane：命中即必须人工复核，不得自动晋升
RISK_HIGH_RE = re.compile(
    r"legal|law|case|案件|法律|credential|token|secret|provider|config|"
    r"scheduler|security|auth|oauth|生产|办案|内核|kernel|权重|weight",
    re.I,
)
RISK_LEGAL_RE = re.compile(r"法律|law|legal|case|案件|办案", re.I)
RISK_CRED_RE = re.compile(r"credential|token|secret|auth|oauth|凭据|密钥", re.I)
RISK_CONF_RE = re.compile(r"provider|config|scheduler|security|生产|内核|kernel", re.I)

# 证据类别（按 lane 要求）
BASE_EVIDENCE = ["source_readback", "claim_extract", "test_output", "runtime_output"]
HIGH_LANES = {"legal", "credential", "security_or_config", "high"}


class Refused(RuntimeError):
    """拒绝类：exit=1。"""


def is_readonly() -> bool:
    return os.environ.get(READONLY_SWITCH) == "1"


def require_write(what: str) -> None:
    """写盘前调用；只读模式下一律拒绝——不静默跳过，直接抛错。"""
    if is_readonly():
        raise Refused(
            f"只读模式（{READONLY_SWITCH}=1）拒绝写入: {what}。"
            "这是硬约束：如需写盘先显式取消该环境变量。"
        )


def norm(s) -> str:
    return (s or "").strip().lower()


def safe_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ── 基因加载 ─────────────────────────────────────────────────────────

def load_genes(genes_dir: Path) -> list[dict]:
    """读基因库 JSON。文件不存在 → 抛 Refused（fail-closed，不静默返回空）。"""
    if not genes_dir.is_dir():
        raise Refused(f"基因库目录不存在: {genes_dir}（环境不可用，不静默当空处理）")
    files = sorted(genes_dir.glob("genes-*.json"))
    if not files:
        raise Refused(f"基因库目录无 genes-*.json 文件: {genes_dir}")

    genes: list[dict] = []
    broken: list[str] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            broken.append(f.name)
            continue
        for g in data.get("genes", []) if isinstance(data, dict) else []:
            if not isinstance(g, dict):
                continue
            g = dict(g)
            g["_file"] = f.name
            # 稳定 id：优先显式 id，否则内容哈希（不依赖文件名，改名不换身份）
            payload = json.dumps(
                {k: v for k, v in g.items() if not k.startswith("_")},
                ensure_ascii=False, sort_keys=True,
            )
            g["_gid"] = g.get("id") or g.get("gene_id") or hashlib.sha256(
                payload.encode()
            ).hexdigest()[:16]
            genes.append(g)

    if broken:
        # 损坏文件不得静默忽略——如实报告
        raise Refused(f"基因库存在不可解析文件: {broken}")
    return genes


# ── 分类 ─────────────────────────────────────────────────────────────

def gene_text(g: dict) -> str:
    """基因的全文（用于证据词与风险词扫描）。"""
    keys = ("mechanism", "resolution", "source", "module", "type",
            "gene_name", "absorbed_knowledge", "repair_mechanism",
            "reusable_rule", "boundary", "status", "verification_status")
    return " ".join(str(g.get(k) or "") for k in keys)


def risk_lane(g: dict) -> str:
    """风险 lane：命中高风险正则即人工 lane，绝不落入 low_engineering。"""
    txt = gene_text(g)
    if RISK_LEGAL_RE.search(txt):
        return "legal"
    if RISK_CRED_RE.search(txt):
        return "credential"
    if RISK_CONF_RE.search(txt):
        return "security_or_config"
    if RISK_HIGH_RE.search(txt):
        return "high"
    return "low_engineering"


def required_for(lane: str) -> list[str]:
    req = list(BASE_EVIDENCE)
    if lane in HIGH_LANES:
        req += ["human_review", "secondary_llm_or_domain_audit"]
    return req


def evidence_present(g: dict) -> list[str]:
    """实际存在的证据类别（只看基因自身声明，不做善意推定）。"""
    txt = gene_text(g)
    present: list[str] = []
    if g.get("_file"):
        present.append("source_readback")
    if g.get("mechanism") or g.get("resolution") or g.get("absorbed_knowledge"):
        present.append("claim_extract")
    if re.search(r"pytest|cargo test|test passed|tests?\b|测试|全绿|passed", txt, re.I):
        present.append("test_output")
    if re.search(r"\bcli\b|runtime|执行|实跑|output|实测", txt, re.I):
        present.append("runtime_output")
    if re.search(r"gpt|claude|mimo|agnes|deepseek|reviewer|audit|审计|复核|secondary", txt, re.I):
        present.append("secondary_llm_or_domain_audit")
    if re.search(r"人工|human|苹果哥|授权|批准", txt, re.I):
        present.append("human_review")
    return sorted(set(present))


def status_bucket(g: dict) -> str:
    joined = " ".join(norm(g.get(k)) for k in ("status", "verification_status"))
    if any(w in joined for w in REJECT_WORDS):
        return "rejected_or_blocked"
    if any(w in joined for w in CANDIDATE_WORDS):
        return "candidate_like"
    if "active" in joined:
        return "active_like"
    if any(w in joined for w in PROMOTED_WORDS):
        return "promoted_like"
    return "unknown"


def verdict(g: dict, lane: str, present: list[str]) -> tuple[str, list[str]]:
    """产出裁定。核心不变式：证据不齐 → WATCH，绝不放行为可晋升。"""
    req = required_for(lane)
    missing = [x for x in req if x not in present]
    if missing:
        return "WATCH_PROOF_PACKET_INCOMPLETE", missing
    if lane in HIGH_LANES:
        # 高风险即使证据齐，仍只能进人工队列
        return "WATCH_REQUIRES_HUMAN_REVIEW", []
    return "PASS_PROMOTION_ELIGIBLE_NO_DB_MUTATION", []


def make_packet(g: dict, lane: str, present: list[str], v: str, missing: list[str], idx: int) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return {
        "schema": "PGGPromotionProofPacket/v0.2",
        "task_id": f"pam-{today}-{idx:04d}-{g['_gid'][-6:]}",
        "capability_id": g["_gid"],
        "capability_type": "gene",
        "source_file": g.get("_file"),
        "gene_module": g.get("module"),
        "gene_type": g.get("type"),
        "claim": (g.get("resolution") or g.get("mechanism") or "")[:1000],
        "gap": (g.get("mechanism") or "")[:500],
        "risk_lane": lane,
        "required_evidence": required_for(lane),
        "present_evidence": present,
        "missing_evidence": missing,
        "verdict": v,
        # 硬门：高风险或证据不齐 → 必须人工
        "auto_allowed": (lane not in HIGH_LANES and not missing),
        "human_required": (lane in HIGH_LANES) or bool(missing),
        "rollback_path": "本工具不改基因库；若后续晋升，回滚 = 还原 genes/*.json 备份",
        "boundary": "READ_ONLY_NO_GENE_MUTATION_NO_AUTO_PROMOTION_NO_AUTO_RETIREMENT",
    }


def run(genes_dir: Path, outdir: Path, limit_review: int) -> dict:
    genes = load_genes(genes_dir)
    if limit_review < 0:
        raise Refused(f"--limit-review 不得为负: {limit_review}")

    packets: list[dict] = []
    for i, g in enumerate(genes, 1):
        lane = risk_lane(g)
        present = evidence_present(g)
        v, missing = verdict(g, lane, present)
        packets.append(make_packet(g, lane, present, v, missing, i))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rundir = outdir / ts
    require_write(str(rundir))
    (rundir / "proof_packets").mkdir(parents=True, exist_ok=True)

    for p in packets[:limit_review]:
        (rundir / "proof_packets" / f"{p['task_id']}.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    vc = Counter(p["verdict"] for p in packets)
    lc = Counter(p["risk_lane"] for p in packets)
    mc = Counter(m for p in packets for m in p["missing_evidence"])
    human = sum(1 for p in packets if p["human_required"])
    auto = sum(1 for p in packets if p["auto_allowed"])

    report = {
        "schema": "PGGPromotionAuthorityMatrix/v0.2",
        "generated_at_utc": ts,
        "genes_dir": str(genes_dir),
        "rundir": str(rundir),
        "input_counts": {
            "total_genes": len(genes),
            "human_required": human,
            "auto_allowed": auto,
        },
        "reviewed_first_n": min(limit_review, len(packets)),
        "verdict_counts": dict(vc),
        "risk_lane_counts": dict(lc),
        "missing_evidence_counts": dict(mc),
        "policy": {
            "auto_promote": "仅 low_engineering 且证据齐备；本工具仍不做任何基因库写入",
            "human_required": "法律/凭据/配置/安全 lane，或证据不齐，一律人工",
            "no_promotion_by_status": "status/fitness 单独绝不构成晋升依据",
        },
        "boundary": "READ_ONLY_NO_GENE_MUTATION_NO_AUTO_PROMOTION_NO_AUTO_RETIREMENT",
    }
    (rundir / "proof_packet_queue.json").write_text(
        json.dumps(packets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rundir / "promotion_authority_matrix_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="PGG Promotion Authority Matrix v0.2（只读）")
    ap.add_argument("--genes-dir", default=str(GENES_DIR))
    ap.add_argument("--outdir", default=str(SANDBOX / "promotion-authority"))
    ap.add_argument("--limit-review", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    gd = Path(args.genes_dir).expanduser()
    od = Path(args.outdir).expanduser()

    if not gd.is_dir():
        print(json.dumps({"ok": False, "refused": "genes_dir_missing", "path": str(gd)},
                         ensure_ascii=False), file=sys.stderr)
        return 3

    try:
        report = run(gd, od, args.limit_review)
    except Refused as e:
        print(json.dumps({"ok": False, "refused": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    else:
        ic = report["input_counts"]
        print(f"基因库: {report['genes_dir']}")
        print(f"基因总数: {ic['total_genes']}")
        print(f"需人工: {ic['human_required']} | 可自动: {ic['auto_allowed']}")
        print(f"风险 lane: {report['risk_lane_counts']}")
        print(f"裁定: {report['verdict_counts']}")
        print(f"报告: {report['rundir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
