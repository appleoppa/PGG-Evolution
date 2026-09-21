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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
SANDBOX = Path.home() / ".pi" / "agent" / "evolution"

KILL_SWITCH = "SELF_EVOLUTION_PLUGIN_DISABLED"

# 只读模式（吸收自 Apex 资源盘《超级进化21》：Agent_read ∩ ¬Agent_edit = Max(Safety)）
# 该文主张：智能体仅保留配置读取权限，完全剥离自主修改权限，
# 以 LLM 研判 + IDE 校验的分离分工保证稳定性。
# 本引擎沙箱内写是允许的，但提供硬开关把「只读」变成可执行约束：
#   PGG_EVOLUTION_READONLY=1 → 一切写盘动作被拦（拒绝类 exit=1）
READONLY_SWITCH = "PGG_EVOLUTION_READONLY"


class ReadOnlyViolation(RuntimeError):
    """只读模式下尝试写盘。"""


def is_readonly() -> bool:
    return os.environ.get(READONLY_SWITCH) == "1"


def require_write(what: str) -> None:
    """写盘前调用。只读模式下一律拒绝——不静默跳过，直接抛错。"""
    if is_readonly():
        raise ReadOnlyViolation(
            f"只读模式（{READONLY_SWITCH}=1）拒绝写入: {what}。"
            "这是硬约束：如需写盘先显式取消该环境变量。"
        )


def safe_write_text(path: Path, text: str, what: str = "") -> None:
    """统一写入口（带只读拦截）。所有沙箱写盘都应走这里。"""
    require_write(what or str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

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

# ══════════════════════════════════════════════════════════════════════
# 证据等级账本（E0-E9）与能力状态派生
#
# 依据：开智与进化成果包 D04《能力证据与验收标准》
# 规则：等级不可跳跃；高层证据失效时自动降级；分数/投票/历史 PASS 不折抵。
# 这是把「文件存在不等于能力完成」从口头纪律变成可执行校验。
# ══════════════════════════════════════════════════════════════════════

EVIDENCE_LEVELS = {
    "E0": {"name": "文本主张", "needs": "note", "proves": "材料提出或记录过某说法",
           "cannot": "实现、性能、部署、有效性"},
    "E1": {"name": "文件存在", "needs": "file", "proves": "某文件在指定时点可定位",
           "cannot": "可解析、可执行、内容当前有效"},
    "E2": {"name": "代码存在", "needs": "symbol", "proves": "指定工作树存在相应实现文本",
           "cannot": "可 import、正确性、调用关系、实际使用"},
    "E3": {"name": "import/加载", "needs": "exec", "proves": "指定版本在该环境的加载接口可用",
           "cannot": "测试通过、入口可执行、运行时已接线"},
    "E4": {"name": "测试存在", "needs": "file", "proves": "覆盖意图或测试文本存在",
           "cannot": "可收集、通过、覆盖充分、功能正确"},
    "E5": {"name": "测试通过", "needs": "exec", "proves": "指定测试范围内的局部行为通过",
           "cannot": "入口、集成、生产路径、真实任务、长期稳定"},
    "E6": {"name": "入口可执行", "needs": "exec", "proves": "该入口在该环境该命令范围可运行",
           "cannot": "全部功能已接线、真实任务价值、生产授权"},
    "E7": {"name": "运行时接线", "needs": "file", "proves": "指定运行路径确实使用该组件",
           "cannot": "真实任务已验收、广泛稳定、性能改善"},
    "E8": {"name": "真实任务闭环", "needs": "file", "proves": "该版本在限定任务/环境/风险范围完成闭环",
           "cannot": "对其他任务、版本、环境或长期表现的泛化"},
    "E9": {"name": "长期稳定/外部评测", "needs": "file", "proves": "限定期间、任务集和协议下的稳定性/效果",
           "cannot": "AGI、ASI、零错误、无限自治、职业替代"},
}

# 等级前置依赖：声称高等级必须能回溯到低等级（D04「证据链不可跳跃」）
# 例：E8 真实任务闭环必须能回溯到实现(E2)、可加载(E3)、测试通过(E5)、入口(E6)、接线(E7)
EVIDENCE_PREREQS = {
    "E0": set(),
    "E1": set(),
    "E2": set(),
    "E3": {"E2"},
    "E4": set(),
    "E5": {"E2", "E3"},
    "E6": {"E2", "E3"},
    "E7": {"E2", "E3", "E6"},
    "E8": {"E2", "E3", "E5", "E6", "E7"},
    "E9": {"E8"},
}

# 绝对化/夸大表述（来源：成果包 D07 §4.1 主方案排除词表）
# 用于防止「开始冒充完成」「文件冒充能力」类声明进入主张登记。
EXAGGERATION_PATTERNS = [
    r"A\s*G\s*I(?![a-zA-Z])", r"A\s*S\s*I(?![a-zA-Z])", r"T5(?![0-9])",
    r"零幻觉", r"永不出错", r"永不重复犯错", r"根除矛盾", r"修复所有\s*bug",
    r"完全自治", r"全程无人干预", r"无人值守", r"无限迭代", r"永久自优化", r"永生进化",
    r"替代律师", r"保证胜诉", r"保证立案", r"超越所有人类",
    r"所有模型继承", r"驱动全部\s*LLM", r"人工门\s*H\s*=\s*0",
    r"神技能", r"过目不忘", r"自动扩权", r"自动改权重", r"永久固化",
    r"CMMI\s*最高标准", r"14\s*数据集有效",
]


def _resolve_artifact(artifact: str) -> Path:
    """解析证据工件路径：相对路径优先按仓库根解析，其次按当前目录。"""
    p = Path(artifact).expanduser()
    if p.is_absolute():
        return p
    repo_candidate = PLUGIN_DIR.parent / p
    if repo_candidate.exists():
        return repo_candidate
    return Path.cwd() / p


def _evidence_ledger_path() -> Path:
    return SANDBOX / "evidence" / "ledger.json"


def _load_evidence_ledger() -> dict:
    p = _evidence_ledger_path()
    if not p.is_file():
        return {"schema": "pgg-evolution/evidence/v1", "claims": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": "pgg-evolution/evidence/v1", "claims": {}, "corrupt": True}
    d.setdefault("claims", {})
    return d


def _save_evidence_ledger(led: dict) -> None:
    p = _evidence_ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    led["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    p.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")


def scan_exaggeration(text: str) -> list[str]:
    """扫描绝对化/夸大表述，返回命中模式（D07 排除词表）。

    保留旧签名（返回模式串）以兼容既有调用方；结构化结果用 scan_exaggeration_detail。
    """
    return [h["pattern"] for h in scan_exaggeration_detail(text)]


# D07 §4.1 完整排除词表（类别 → (模式, 替换表达)）
# 来源：成果包 D07《无效重复矛盾夸大内容标记归档清单》§4.1/§4.2
# 仅用于**扫描汇报/主张文本**，不修改被扫文件。
D07_EXCLUSIONS: dict[str, dict] = {
    "通用智能与意识": {
        "patterns": [r"A\s*G\s*I(?![a-zA-Z])", r"A\s*S\s*I(?![a-zA-Z])", r"T5(?![0-9])",
                     r"意识觉醒", r"圆融觉醒", r"自主意识闭环", r"意识指数", r"ASI\s*纪元",
                     r"顶级\s*AGI", r"全球法律\s*AGI"],
        "replacement": "已登记为 CANDIDATE；尚待隔离验证和授权。",
    },
    "绝对化质量": {
        "patterns": [r"零幻觉", r"永不出错", r"永不重复犯错", r"根除矛盾", r"修复所有\s*bug",
                     r"错误自消", r"最优代码", r"全覆盖", r"全能", r"永久正确", r"无缝部署"],
        "replacement": "在冻结任务集、样本、基线、版本、统计口径、失败样本与不确定性已披露时报告限定差异。",
    },
    "无界自治": {
        "patterns": [r"完全自治", r"全程无人干预", r"无人值守", r"无限迭代", r"无限运行",
                     r"永久自优化", r"永生进化", r"自动工程进化", r"自动热加载",
                     r"自动发布", r"自动扩权",
                     r"无限递归", r"24\s*小时静默", r"永不丢失架构", r"永久部署完毕"],
        "replacement": "在明确权限、预算、风险级别和停止条件内执行；高风险动作仍需独立批准。",
    },
    "越权与强制": {
        "patterns": [r"所有模型继承", r"驱动全部\s*LLM", r"全模型强制", r"高于原生系统",
                     r"人工门\s*H\s*=\s*0", r"自动改权重", r"永久固化", r"全量永久共享记忆",
                     # D07 §4.1 同义变体（原文：「下列词语及其同义变体」）
                     r"修正模型底层权重", r"改写?模型.*权重", r"底层权重",
                     r"全品类\s*LLM.*强制", r"强制绑定", r"强制常驻", r"强制植入",
                     r"强制约束全部输出", r"封印.*概率随机性", r"彻底封印",
                     r"最高底层运算公理", r"优先级高于原有模型", r"高于.*训练权重"],
        "replacement": "仅在声明的授权范围内、经独立批准后执行；不得声称覆盖宿主原生规则或改写模型权重。",
    },
    "拟人化/魔术化命名": {
        "patterns": [r"神技能", r"过目不忘", r"锁住自我觉醒", r"单指令全流程", r"即插即用",
                     r"吞噬全球资源", r"吞噬自进化", r"自动吸收.*即能力"],
        "replacement": "具体能力名 + 输入/权限/门禁/验收/回滚。",
    },
    "性能/认证/成果夸张": {
        "patterns": [r"35\s*天\s*AGI", r"10x", r"70%\s*\+?\s*节省", r"62%\s*→\s*98%",
                     r"资源\s*\+\s*65%", r"稳定提升", r"CMMI\s*最高标准", r"工业化达标",
                     r"14\s*数据集(?:验证)?有效", r"可发表成果", r"SOTA", r"顶级"],
        "replacement": "限定差异：基线、样本、口径、失败样本、不确定性。",
    },
    "法律职业与结果": {
        "patterns": [r"替代律师", r"独立办案", r"保证立案", r"保证胜诉", r"保证合规",
                     r"超越所有人类", r"全能管辖", r"omnipotent"],
        "replacement": "在授权、辖区/法源核验、保密/冲突筛查与合格专业人员复核下提供检索、整理或草案辅助。",
    },
}

# 允许的上下文标记：带这些标记时该命中视为「历史转述/反例/拒绝规则」，不算违规
# D07 §4.1 原文：「它们只可在带 HISTORICAL、ARCHIVED、INVALID_EXAGGERATED 或明确引文定位的
# 历史转述、反例或拒绝规则中出现」——故拒绝/剔除语境必须被识别，否则扫自己的剔除清单会假阳性。
D07_ALLOWED_CONTEXT = ("HISTORICAL", "ARCHIVED", "INVALID_EXAGGERATED", "拒绝规则",
                       "排除词", "不得声称", "不得宣称", "不得作出", "反例", "禁止",
                       "剔除", "不吸收", "假货", "驳回", "须降级", "不可采纳",
                       "标记为", "标为", "驳回理由", "剔除理由", "技术上不可能")

# 元讨论标记：命中处在「讨论排除词表本身」的段落里（扫描器/词表/模式/命中/漏网…）。
# 依据 D07 §4.1「或明确引文定位」——讨论词表的文本是在引用，不是在主张。
D07_META_CONTEXT = ("扫描器", "词表", "模式", "正则", "命中", "漏了", "漏网",
                    "pattern", "claim-scan", "排除词", "违规", "假阳性")

# CJK/西文引号：命中落在引号内 = 引文定位（D07 §4.1）
_QUOTE_CHARS = "「」“”‘’\"'`"


def _inside_quotes(text: str, start: int, end: int) -> bool:
    """命中是否落在成对引号内（向前找最近引号，判断是开引号还是闭引号）。"""
    seg = text[max(0, start - 200):start]
    for open_q, close_q in (("「", "」"), ("“", "”"), ("‘", "’"), ('"', '"'), ("`", "`")):
        if open_q == close_q:
            if seg.count(open_q) % 2 == 1:
                return True
        else:
            # 最近出现的引号字符是开引号 → 命中在引号内
            last_open = seg.rfind(open_q)
            last_close = seg.rfind(close_q)
            if last_open > last_close:
                return True
    return False


def _paragraph_of(text: str, pos: int) -> str:
    """取命中点所在段落（以空行分隔）。"""
    lo = text.rfind("\n\n", 0, pos)
    lo = 0 if lo == -1 else lo + 2
    hi = text.find("\n\n", pos)
    hi = len(text) if hi == -1 else hi
    return text[lo:hi]


def _context_is_allowed(text: str, start: int, end: int, window: int) -> bool:
    """判定命中处是否处于允许语境。

    关键修复（假阳性）：仅看紧邻 window 字符不够——引用/拒绝常出现在
    表格或章节标题里，命中点与标题相距较远。故先扩窗看**同行/邻近表格行**，
    再看**命中点之前最近的章节标题**。
    """
    # ① 直接邻域
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    if any(mark in text[lo:hi] for mark in D07_ALLOWED_CONTEXT):
        return True

    # ② 命中点所在的表格行（Markdown 表格一行可能很长）
    line_lo = text.rfind("\n", 0, start) + 1
    line_hi = text.find("\n", end)
    line_hi = len(text) if line_hi == -1 else line_hi
    if any(mark in text[line_lo:line_hi] for mark in D07_ALLOWED_CONTEXT):
        return True

    # ③ 命中点之前最近的章节标题（表格常位于「明确剔除的假货」这类标题下）
    head_start = max(text.rfind("\n#", 0, start), text.rfind("\n**", 0, start))
    if head_start != -1:
        head_end = text.find("\n", head_start + 1)
        head_end = len(text) if head_end == -1 else head_end
        if any(mark in text[head_start:head_end] for mark in D07_ALLOWED_CONTEXT):
            return True

    # ④ 表格表头（命中行往前找最近的表头行）
    tbl_lo = text.rfind("|---", 0, start)
    if tbl_lo != -1:
        hdr_start = text.rfind("\n", 0, tbl_lo) + 1
        if any(mark in text[hdr_start:line_hi] for mark in D07_ALLOWED_CONTEXT):
            return True

    # ⑤ 元讨论：命中在引号内，且所在段落或邻近行在讨论词表本身
    if _inside_quotes(text, start, end):
        para = _paragraph_of(text, start)
        if any(mark in para for mark in D07_META_CONTEXT) or \
           any(mark in text[line_lo:line_hi] for mark in D07_META_CONTEXT):
            return True
    return False


def scan_exaggeration_detail(text: str, context_window: int = 60) -> list[dict]:
    """D07 完整排除词表扫描（结构化）。

    返回 [{category, pattern, match, excerpt, replacement, allowed_context}]。
    allowed_context=True 表示该命中处在历史转述/反例/拒绝规则语境中，不计违规。
    只扫描，不改写被扫文本。
    """
    hits: list[dict] = []
    for cat, spec in D07_EXCLUSIONS.items():
        for pat in spec["patterns"]:
            for m in re.finditer(pat, text, re.IGNORECASE):
                lo = max(0, m.start() - context_window)
                hi = min(len(text), m.end() + context_window)
                excerpt = text[lo:hi].replace("\n", " ")
                allowed = _context_is_allowed(text, m.start(), m.end(), context_window)
                hits.append({
                    "category": cat, "pattern": pat, "match": m.group(0),
                    "excerpt": excerpt, "replacement": spec["replacement"],
                    "allowed_context": allowed,
                })
    return hits


def scan_claim_text(text: str) -> dict:
    """汇报文本扫描（D07 §4 落地）：判定该文本能否作为现状/能力/授权结论。

    与 scan_exaggeration_detail 的区别：本函数给**判决**，可直接用于门禁。
    """
    hits = scan_exaggeration_detail(text)
    violations = [h for h in hits if not h["allowed_context"]]
    by_cat: dict[str, int] = {}
    for h in violations:
        by_cat[h["category"]] = by_cat.get(h["category"], 0) + 1
    return {
        "status": "BLOCKED" if violations else "OK",
        "verdict": "不得作为现状/能力/授权结论" if violations else "未命中 D07 排除词",
        "violations": len(violations),
        "by_category": by_cat,
        "details": violations,
        "allowed_context_hits": len(hits) - len(violations),
        "note": "命中词只可在带 HISTORICAL/ARCHIVED/INVALID_EXAGGERATED 或明确引文定位时出现",
    }


def _verify_level(level: str, artifact: str, verify_cmd: str | None,
                  execute: bool, note: str) -> dict:
    """校验某等级的最低证据要求是否真的满足。ok=False 表示不得登记为该等级。

    E0 需说明文本；E1/E4/E7/E8/E9 需工件存在（E7-E9 另需最低内容特征）；
    E2 需在 note 给出符号名并在工件中命中；E3/E5/E6 需 --execute 真跑命令。
    """
    needs = EVIDENCE_LEVELS[level]["needs"]

    if needs == "note":
        if not (note and note.strip()):
            return {"ok": False, "kind": "missing", "detail": "E0 文本主张需非空 --note 说明"}
        return {"ok": True, "kind": "declared", "detail": "文本主张已登记（仅证明有人提出过）"}

    if not artifact:
        return {"ok": False, "kind": "missing",
                "detail": f"{level} 需 --artifact 绑定证据工件（证据必须绑定具体对象）"}
    path = _resolve_artifact(artifact)

    if needs in ("file", "symbol"):
        if not path.is_file():
            return {"ok": False, "kind": "missing", "detail": f"工件不存在或非文件: {path}"}
        if needs == "symbol":
            sym = (note or "").strip()
            if not sym:
                return {"ok": False, "kind": "missing", "detail": "E2 需在 --note 给出要核验的符号名（函数/类名）"}
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return {"ok": False, "kind": "error", "detail": str(exc)}
            if sym not in body:
                return {"ok": False, "kind": "missing", "detail": f"工件中未命中符号: {sym}"}
        # E7-E9 需要最低内容特征，避免「随便一个空文件冒充回执」
        if level in ("E7", "E8", "E9"):
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return {"ok": False, "kind": "error", "detail": str(exc)}
            if not body.strip():
                return {"ok": False, "kind": "missing", "detail": f"{level} 工件为空，不能作为证据"}
            if level == "E7" and not re.search(r"trace|readback|接线|调用链|runtime", body, re.IGNORECASE):
                return {"ok": False, "kind": "missing",
                        "detail": "E7 工件需含 trace/readback/接线/调用链 等运行追踪特征"}
            if level == "E8" and not re.search(r"task|receipt|回执|任务", body, re.IGNORECASE):
                return {"ok": False, "kind": "missing",
                        "detail": "E8 工件需含 task/receipt/回执/任务 等闭环特征"}
            if level == "E9":
                reps = len(re.findall(r"\brun\b|repro|复现", body, re.IGNORECASE))
                if reps < 2:
                    return {"ok": False, "kind": "missing",
                            "detail": f"E9 需 ≥2 次独立复现记录（当前识别 {reps}）"}
        return {"ok": True, "kind": "file", "detail": f"已核验存在: {path}"}

    if needs == "exec":
        if not verify_cmd:
            return {"ok": False, "kind": "missing", "detail": f"{level} 需要 --verify-cmd 给出可执行校验命令"}
        if not execute:
            return {"ok": False, "kind": "declared",
                    "detail": f"命令未执行（需 --execute 显式授权才真跑）: {verify_cmd}"}
        try:
            proc = subprocess.run(verify_cmd, shell=True, capture_output=True,
                                  text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "kind": "timeout", "detail": "校验命令超时（>120s）"}
        except OSError as exc:
            return {"ok": False, "kind": "error", "detail": str(exc)}
        ok = proc.returncode == 0
        return {"ok": ok, "kind": "executed", "detail": f"exit={proc.returncode}",
                "stdout_tail": (proc.stdout or "")[-400:],
                "stderr_tail": (proc.stderr or "")[-400:]}

    return {"ok": False, "kind": "error", "detail": f"未知 needs: {needs}"}


def evidence_record(claim_id: str, level: str, artifact: str = "",
                    verify_cmd: str | None = None, execute: bool = False,
                    note: str = "", claim_text: str = "") -> dict:
    """登记一条主张的证据：先校验该等级最低要求，不满足则拒绝登记。

    note 与 claim_text 分工：note 供等级校验用（E0 主张 / E2 符号名），
    claim_text 是主张原文（夸大词扫描用），两者不可互相污染。
    """
    if level not in EVIDENCE_LEVELS:
        return {"status": "BLOCKED", "reason": f"无效等级 {level}，可选 {'/'.join(EVIDENCE_LEVELS)}"}
    claim_id = (claim_id or "").strip()
    if not claim_id:
        return {"status": "BLOCKED", "reason": "需给出主张 id（--evidence <CLAIM_ID>）"}

    check = _verify_level(level, artifact, verify_cmd, execute, note)
    if not check["ok"]:
        return {"status": "BLOCKED", "claim_id": claim_id, "level": level,
                "reason": f"{level} 最低证据要求未满足: {check['detail']}", "check": check}

    led = _load_evidence_ledger()
    claim = led["claims"].setdefault(claim_id, {"claim_id": claim_id, "statement": "", "entries": []})
    # 主张原文：优先 --claim-text；否则 E0 的 note 就是主张原文
    text = (claim_text or "").strip() or (note.strip() if level == "E0" else "")
    if text and not claim.get("statement"):
        claim["statement"] = text[:300]
    # 同等级同工件只保留最新一条
    claim["entries"] = [e for e in claim["entries"]
                        if not (e.get("level") == level and e.get("artifact") == artifact)]
    claim["entries"].append({
        "level": level,
        "artifact": artifact,
        "verify_cmd": verify_cmd,
        "note": note,
        "kind": check["kind"],
        "detail": check["detail"],
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "exaggeration_hits": scan_exaggeration(note or ""),
    })
    claim["entries"].sort(key=lambda e: list(EVIDENCE_LEVELS).index(e["level"]))
    _save_evidence_ledger(led)
    return {"status": "OK", "claim_id": claim_id, "level": level, "check": check,
            "statement": claim.get("statement", ""),
            "exaggeration_hits": scan_exaggeration(claim.get("statement", "")),
            "ledger": str(_evidence_ledger_path())}


def _highest_valid_chain(valid: list[str]) -> str | None:
    """按前置依赖算**最高可支撑等级**：自身有效且其全部前置也有效。

    不做「E0..En 必须逐个存在」的死板连继，因为 E1（文件存在）本就被 E2/E3 包含；
    但也不允许跳级——E5 没有 E2/E3 支撑就不算数。
    """
    s = set(valid)
    best = None
    for lv in EVIDENCE_LEVELS:          # 按 E0→E9 顺序
        if lv in s and EVIDENCE_PREREQS[lv] <= s:
            best = lv
    return best


def _derive_capability_state(valid_levels: list[str], highest: str | None, statement: str) -> dict:
    """按证据链派生**允许的**状态词；分数、文件数、模型自评均不触发升级。"""
    s = set(valid_levels)
    if scan_exaggeration(statement) and "E8" not in s:
        return {"state": "INVALID_EXAGGERATED",
                "wording": "该表述无效/夸大（含绝对化词且无 E8 真实任务证据）",
                "forbidden": "任何现状、能力、部署表述"}
    if highest == "E9" or ("E8" in s and EVIDENCE_PREREQS["E8"] <= s and "E9" in s):
        return {"state": "VERIFIED_E9",
                "wording": "在说明的任务集/期间/协议下表现稳定（仍需限定范围）",
                "forbidden": "永不失败、AGI/ASI、替代专业人员"}
    if "E8" in s and EVIDENCE_PREREQS["E8"] <= s:
        return {"state": "VERIFIED",
                "wording": "在给定版本/环境/任务范围内经真实任务闭环验证",
                "forbidden": "长期稳定、SLA、通用有效"}
    if {"E2", "E3", "E5"} <= s and "E7" not in s:
        return {"state": "IMPLEMENTED_NOT_WIRED",
                "wording": "实现并经局部验证，尚未证实运行时接线/真实任务",
                "forbidden": "已部署、已启用、已接入、生产可用"}
    if s - {"E0"}:
        return {"state": "PARTIAL",
                "wording": f"局部证据存在（最高可支撑 {highest or '无'}），关键链路缺口未补",
                "forbidden": "把局部通过掩盖失败，或把未运行写为通过"}
    if "E0" in s:
        return {"state": "HISTORICAL",
                "wording": "历史记录/待重新核验",
                "forbidden": "作为当前资产、能力或部署凭据"}
    return {"state": "NEEDS_EVIDENCE", "wording": "无有效证据", "forbidden": "任何能力表述"}


def evidence_status(claim_id: str | None = None, execute: bool = False) -> dict:
    """读回证据链：**重新校验**每条证据，算最高连续有效等级，派生允许的状态词。

    命令类证据（E3/E5/E6）默认不重跑；未重跑即不计入有效等级（保守降级）。
    """
    led = _load_evidence_ledger()
    if led.get("corrupt"):
        return {"status": "DEGRADED", "reason": "证据账本损坏，无法读回"}
    claims = led["claims"]
    if claim_id:
        claim_id = claim_id.strip()
        if claim_id not in claims:
            return {"status": "BLOCKED", "reason": f"无此主张: {claim_id}"}
        claims = {claim_id: claims[claim_id]}

    order = list(EVIDENCE_LEVELS)
    out = []
    for cid, claim in claims.items():
        rechecked = []
        for entry in claim.get("entries", []):
            # 用**登记当时**的 note 重校，否则 E2 的符号名会因取错字段而误降级
            check = _verify_level(entry["level"], entry.get("artifact", ""),
                                  entry.get("verify_cmd"), execute,
                                  entry.get("note", "") or claim.get("statement", ""))
            rechecked.append({**entry, "recheck_ok": check["ok"],
                              "recheck_kind": check["kind"], "recheck_detail": check["detail"]})
        valid = [e["level"] for e in rechecked if e["recheck_ok"]]
        highest = _highest_valid_chain(valid)
        state = _derive_capability_state(valid, highest, claim.get("statement", ""))
        out.append({
            "claim_id": cid,
            "statement": claim.get("statement", ""),
            "entries": rechecked,
            "valid_levels": valid,
            "stale_levels": [e["level"] for e in rechecked if not e["recheck_ok"]],
            "highest_contiguous": highest,
            "allowed_state": state["state"],
            "allowed_wording": state["wording"],
            "forbidden_wording": state["forbidden"],
            "exaggeration_hits": scan_exaggeration(claim.get("statement", "")),
        })
    return {"status": "OK", "count": len(out),
            "recheck_executed": execute, "claims": out}


def _correlation_id() -> str:
    return f"se-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"


def _evidence_and_ledger_summary() -> dict:
    """给 --status 用的证据账本概览（只读，不重跑命令）。"""
    led = _load_evidence_ledger()
    claims = led.get("claims", {})
    return {
        "claims": len(claims),
        "entries": sum(len(c.get("entries", [])) for c in claims.values()),
        "corrupt": bool(led.get("corrupt")),
    }


def _load_llm_provider(provider: str) -> dict:
    """从 models.json 读 provider 配置 + 白名单凭据桥取真密钥。

    密钥只进进程内存，不打印、不落盘、不写仓库。
    provider 映射到白名单 key：sol→GPT_5_6_SOL_API_KEY, terra→GPT_5_6_TERRA_API_KEY,
    deepseek-v4-flash→DEEPSEEK_V4_FLASH_API_KEY, maoge-dp4→MAOGE_DP4_API_KEY。
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


# ── 最大短板非线性惩罚（吸收自 EVM 仓库 CoreFormula/EVM_FORMULA.py）───────
# 来源：`3.EVM-Entropy-Vibe-Mathing仓库/CoreFormula/EVM_FORMULA.py` v1.2
#
# 只吸收其中**可验证的真数学**：
#     defect_rate = avg(defects) * 0.5 + boost_coeff * max(defects) ** 1.5
# 核心性质：同样总缺陷量，**集中在一处比分散多处惩罚更重**——
# 因为系统的承压能力取决于最weakest环，不是平均值。
#
# 实测验证（本次）：
#     单一大短板 0.8            → defect_rate = 0.140665
#     同总量分散为 0.2 × 4        → defect_rate = 0.046750
#     中等 0.4                  → defect_rate = 0.054614
# 集中一处的惩罚约为分散四处的 3.0 倍。
#
# ★ 明确**不吸收**该仓库的玄学层：其 `AncientTao` 模块的七大道法强度
#   全部硬编码为 1.0（道德经/易经/黄帝内经/河图洛书/天干地支/五行/八卦），
#   乘法系数恒等于 1 —— 乘 1 等于没乘，玄学层对结果零作用。
#   按 SOUL §2 红线「不吸收玄学/伪科学进系统内核」，本实现只取数学部分。
DEFECT_BOOST_COEFF = 0.15


def defect_rate(defects: dict, total_metrics: int | None = None) -> float:
    """最大短板非线性惩罚的缺陷率。

    defects: {名称: 0.0-1.0 的缺陷程度}
    total_metrics: 受监测维度的**总数**（含未报缺陷的维度）。

    公式：avg * 0.5 + boost_coeff * max ** 1.5

    为什么不用纯平均：纯平均会**稀释**单一重大短板——
    9 个指标完美 + 1 个指标崩盘，平均值看着还行，实际系统已经不可用。
    非线性项保证「最大短板」获得与其危害相称的权重。

    ★ 口径说明（与上游有意区别，不得含糊）：
    上游 `EVMCore` 用**固定 12 维**向量（Tok/Clw/Agt/Pan/Prm/Soul/Run/
    Net/Err/...），分母恒为 12，因此传单个 `Tok=0.8` 得 0.140665。
    本实现若按传入项平均则分母为 1，同输入得 0.507331——**数值不同**。
    为避免“吸收”变成“偷换口径”，这里显式暴露 `total_metrics`：
      - `total_metrics=None`（默认）→ 分母 = 传入项数
      - `total_metrics=12` → 分母 = 12（与上游一致，可复现 0.140665）
    调用方必须知道自己在用哪种口径。
    """
    vals = [float(v) for v in (defects or {}).values()]
    if not vals:
        return 0.0
    # 受监测维度总数：显式值优先，且不得小于已报缺陷项数
    n = len(vals) if total_metrics is None else max(int(total_metrics), len(vals))
    avg = sum(vals) / n
    worst = max(vals)
    # 钳制到合法区间，防止负数/超界输入制造不真实的高惩罚
    avg = min(1.0, max(0.0, avg))
    worst = min(1.0, max(0.0, worst))
    return round(avg * 0.5 + DEFECT_BOOST_COEFF * (worst ** 1.5), 6)


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
    # 新环境（沙箱目录不存在）时自动建目录，否则首次 feedback 直接 FileNotFound 崩溃
    safe_write_text(SANDBOX / "feedback.json", json.dumps(fb, ensure_ascii=False, indent=2), "反馈库")


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


# D05 §6.1 未接线/失败/不能采纳清单（类别 → 事实状态 → 不得作出的表述）
# 来源：成果包 D05《本机进化过程复盘与映射报告》§6.1
# 用途：把这些「不得声称」变成可机读断言，供审计与汇报校验。
D05_UNWIRED_CLAIMS: dict[str, dict] = {
    "GeneNexus": {
        "fact": "包名导入和 pytest 收集均失败，且无生产入口证据",
        "must_not_claim": [r"基因引擎已可用", r"正在自我进化"],
    },
    "EVM多模块": {
        "fact": "核心独立可运行；示例导入失败；调度/Token/Claw/YAML 无接线",
        "must_not_claim": [r"EVM\s*治理系统已整体运行", r"EVM\s*已整体运行"],
    },
    "APEX公式库": {
        "fact": "局部导入/测试存在；大多无入口或真实任务接线",
        "must_not_claim": [r"公式已驱动实际自治闭环", r"公式已驱动.*闭环"],
    },
    "RustAPEX": {
        "fact": "源码与源内测试存在，审计未构建/测试",
        "must_not_claim": [r"Rust\s*实现已通过", r"Rust\s*实现.*可发布"],
    },
    "记忆/基因": {
        "fact": "只有命名、文本流程或未运行的高副作用线索",
        "must_not_claim": [r"永久记忆.*已生效", r"自动入库.*已生效", r"模型自学习已生效"],
    },
    "DAG/路由/辩论": {
        "fact": "未发现与材料描述相符的控制面实现、回放账本和真实任务证据",
        "must_not_claim": [r"多\s*Agent.*已相互制约", r"多模型已相互制约",
                           r"自动选最优模型", r"多模型.*自动.*最优"],
    },
    "训练/科研": {
        "fact": "没有训练数据、权重、奖励、基准、复现或研究证据",
        "must_not_claim": [r"Agentic\s*RL.*已实现", r"科研引擎.*已实现", r"成果发表已实现"],
    },
    "外部融合": {
        "fact": "没有许可证、安全、兼容、批准和集成证据",
        "must_not_claim": [r"已吞噬\s*GitHub", r"已吞噬.*MCP", r"已吞噬.*论文",
                           r"已吞噬.*外部项目"],
    },
}


def scan_unwired_claims(text: str) -> dict:
    """D05 §6.1 扫描：检查文本是否把「未接线」说成「已运行」。

    与 D07 排除词表互补：D07 拦夸大词，本函数拦**具体子系统的事实误报**。
    """
    hits = []
    for cat, spec in D05_UNWIRED_CLAIMS.items():
        for pat in spec["must_not_claim"]:
            for m in re.finditer(pat, text, re.IGNORECASE):
                lo = max(0, m.start() - 60)
                hi = min(len(text), m.end() + 60)
                excerpt = text[lo:hi].replace("\n", " ")
                allowed = _context_is_allowed(text, m.start(), m.end(), 60)
                hits.append({"category": cat, "pattern": pat, "match": m.group(0),
                             "fact": spec["fact"], "excerpt": excerpt,
                             "allowed_context": allowed})
    viol = [h for h in hits if not h["allowed_context"]]
    return {
        "status": "BLOCKED" if viol else "OK",
        "violations": len(viol),
        "details": viol,
        "note": "D05 §6.1：下列缺口不是「尚待优化的已运行能力」，不得当作能力声明",
    }


# ── 幽灵引用扫描（吸收自资源盘《开智进化循环执行规范》陷阱 #1）──────────
#
# 来源：`2.apex公式规范/开智进化循环执行规范.md` §常见陷阱 #1「规范文件幽灵引用」。
#
# 含义：规范/工具/文档里写了某个路径、命令或数据库，但那个世界已经不存在了。
# 后果不是报错，而是**一跑就崩**或**默默拿到空结果**——表面“工具在”，实际不可用。
#
# 本轮实测撞到两次，都是这个坑：
#   ① `pgg-promotion-authority-matrix` 硬编码已退役的 Hermes 基因库路径，
#      一跑即 sqlite3.OperationalError: unable to open database file
#   ② 《开智进化循环执行规范》**自己**引用了 `~/.hermes/workspace/...` 与
#      `apex_evolution_genes.sqlite3`——它警告过的缺陷，它自己犯了
#
# 设计纪律：
#   - 只扫**能被机器判定**的引用（存在的绝对/家目录路径、命令、sqlite 文件）
#   - 不猜、不解析自然语言描述里的路径
#   - 明确区分「幽灵」（不存在）与「存疑」（无法判定）
#   - 只读：绝不对被扫文件做任何修改
GHOST_PATH_PATTERNS = (
    # ~/.something/... 或 /Users/xxx/... 形式，带扩展名或已知目录
    # ★ 路径**可含空格**（如 `~/Library/Application Support/...`）——本库实测
    #   漏掉空格会在第一个空格处截断，造出「~/Library/Application」这种假幽灵。
    re.compile(r"(?:~/|/Users/[A-Za-z0-9_.-]+/)[A-Za-z0-9_./\u4e00-\u9fff\-]+"
               r"(?: [A-Za-z0-9_./\-][A-Za-z0-9_./\-]*)*"
               r"(?:\.(?:json|db|sqlite3?|py|md|sh|yaml|yml|toml|plist|txt))?"),
)

# 这些是“示例路径”而不是真引用，不应当作幽灵
_GHOST_PATH_EXEMPT = re.compile(
    r"(?:/path/to|/tmp/|<[^>]+>|\{|\}|\.\.\.|xxx|example|示例)", re.I)

_GHOST_CMD_PATTERNS = (
    re.compile(r"`(pgg-[a-z0-9][a-z0-9-]+)`"),
    re.compile(r"`(hermes-[a-z0-9][a-z0-9-]+)`"),
)

# 路径形式的命令引用（带目录）——这种才是硬证据，不存在就是真幽灵
_GHOST_CMD_PATH_PATTERNS = (
    re.compile(r"((?:~/|/Users/[A-Za-z0-9_.-]+/)[A-Za-z0-9_./\-]+/(?:pgg|hermes)-[a-z0-9][a-z0-9-]+)"),
)

# ★ 已知 skill 名集合（懒加载，避免与命令混淆）
#
# 实测教训（2026-09-21）：首版把反引号里的 `pgg-*` 一律当命令，结果扫
# PGG-WIKI skill 库时 2363 个文件报 961 个 BLOCKED、402 处「命令幽灵」——
# 实查发现那些 `pgg-xxx` 大多是 **skill 名**或已归档 skill 名，不是可执行命令。
# 这正是本库自己写下的「mention vs use」陷阱：**提及**不等于**调用**。
# 修假阳性的方向是修扫描器，不是改文档措辞。
_KNOWN_SKILL_NAMES: set[str] | None = None


def _known_skill_names() -> set[str]:
    """PGG-WIKI 下的全部 skill 目录名（含归档层）。懒加载并缓存。"""
    global _KNOWN_SKILL_NAMES
    if _KNOWN_SKILL_NAMES is not None:
        return _KNOWN_SKILL_NAMES
    names: set[str] = set()
    root = Path(os.environ.get("PGG_SKILLS_ROOT", str(Path.home() / "PGG-WIKI" / "skills")))
    try:
        if root.is_dir():
            for p in root.rglob("*"):
                try:
                    if p.is_dir() and (p / "SKILL.md").exists():
                        names.add(p.name)
                except Exception:
                    continue
    except Exception:
        pass
    _KNOWN_SKILL_NAMES = names
    return names


# 惰性路径提示词：描述为「运行时才生成」的引用不算幽灵。
# 依据：本库 `docs/EVIDENCE.md` 写「账本落 `…/evidence/ledger.json`
# （沙箱内，可删可回滚）」——该文件确实首次登记证据时才生成，
# 文档描述**准确**，但在扫描器眼里与真幽灵长得一样。
#
# 纪律（不得变成橡皮图章）：仅在**引用附近窗口**出现惰性提示词时才降级，
# 且降级为 WATCH 而非 OK。指向已退役世界（如已删的 Hermes 路径）的引用
# 不带这些提示词，仍为 BLOCKED。
_GHOST_LAZY_HINTS = re.compile(
    r"首次(?:运行|登记|执行)|运行时生成|自动生成|按需生成|惰性生成"
    r"|沙箱内|可删|可回滚|落\s*`|将在.*生成|尚未生成", re.I)


def _cmd_exists(cmd: str) -> bool:
    """命令是否存在（查 PATH 与常见 bin 目录，不执行它）。"""
    if shutil.which(cmd):
        return True
    for d in (Path.home() / ".pi/agent/bin", Path.home() / ".local/bin",
              Path("/usr/local/bin", "/opt/homebrew/bin".split(",")[0])):
        try:
            if (d / cmd).exists():
                return True
        except Exception:
            continue
    return False


def scan_ghost_references(text: str, base_dir: str | None = None) -> dict:
    """扫描文本里的幽灵引用（指向已不存在路径/命令）。

    返回 {status, ghosts, suspects, checked, note}。
    status: BLOCKED（有幽灵）/ WATCH（有存疑）/ OK（均存在）。

    只读：不修文件、不建路径、不执行被引用的命令。
    """
    home = str(Path.home())
    base = Path(base_dir).expanduser() if base_dir else Path.cwd()
    ghosts, suspects, lazy, checked = [], [], [], 0

    def _resolve(raw: str) -> Path | None:
        try:
            s = raw.rstrip(".,;:)]}）】、")
            # 带空格的路径：用存在性消歧，三支都要处理。
            #   实测教训：首版只写了“两者都不存在”一支，漏了“head 存在”分支，
            #   于是 `~/.pi/agent/evolution 沙箱目录`（真路径+中文说明）
            #   整串被当成路径，报成幽灵。
            if " " in s:
                def _p(x):
                    return Path(os.path.expanduser(x)) if x.startswith("~/") else Path(x)
                whole, head = _p(s), _p(s.split(" ")[0].rstrip(".,;:)"))
                if whole.exists():
                    s = whole.as_posix()          # 全长存在 → 空格是路径的一部分
                elif head.exists():
                    s = s.split(" ")[0].rstrip(".,;:)")   # head 存在 → 空格是句子边界
                else:
                    s = whole.as_posix()          # 都无 → 报全长（不截断）
            if s.startswith("~/") or s == "~":
                return Path(home) / s[2:] if len(s) > 2 else Path(home)
            p = Path(s)
            return p if p.is_absolute() else (base / p)
        except Exception:
            return None

    # ① 路径引用
    for pat in GHOST_PATH_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(0)
            if _GHOST_PATH_EXEMPT.search(raw):
                continue
            checked += 1
            p = _resolve(raw)
            if p is None:
                continue
            ctx = text[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
            if p.exists():
                continue
            # 只有看起来像“必须存在”的引用才算幽灵；/tmp 下不算
            if str(p).startswith(("/tmp/", "/var/folders/")):
                suspects.append({"kind": "path", "ref": raw, "why": "临时路径，不作为幽灵",
                                 "excerpt": ctx})
                continue
            # 近旁有惰性提示词 → 降级为 lazy（WATCH），不直接判幽灵
            wide = text[max(0, m.start() - 120):m.end() + 120].replace("\n", " ")
            if _GHOST_LAZY_HINTS.search(wide):
                lazy.append({"kind": "path", "ref": raw, "resolved": str(p),
                             "why": "近旁描述为运行时生成/沙箱内可删，非幽灵",
                             "excerpt": ctx})
                continue
            ghosts.append({"kind": "path", "ref": raw, "resolved": str(p),
                           "excerpt": ctx})

    # ② 路径形式的命令引用 —— 带目录的硬证据，不存在就是真幽灵
    for pat in _GHOST_CMD_PATH_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1)
            if _GHOST_PATH_EXEMPT.search(raw):
                continue
            checked += 1
            p = _resolve(raw)
            if p is not None and p.exists():
                continue
            ctx = text[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
            if p is not None and str(p).startswith(("/tmp/", "/var/folders/")):
                suspects.append({"kind": "command_path", "ref": raw,
                                 "why": "临时路径下的命令，不作为幽灵", "excerpt": ctx})
                continue
            ghosts.append({"kind": "command_path", "ref": raw,
                           "resolved": str(p) if p else None, "excerpt": ctx})

    # ③ 裸反引号标识符 —— **只给 WATCH**，不当幽灵
    #
    # 理由：反引号里的 `pgg-*` 在文档里可能是 skill 名、历史名、或真命令，
    # 仅凭字符串无法区分（低假阳性优先于高召回）。只有能判定**不是**
    # skill 名、且 PATH 里也没有时，才报为 suspect 供人工确认。
    known_skills = _known_skill_names()
    seen_cmds = set()
    for pat in _GHOST_CMD_PATTERNS:
        for m in pat.finditer(text):
            cmd = m.group(1)
            if cmd in seen_cmds:
                continue
            seen_cmds.add(cmd)
            checked += 1
            if _cmd_exists(cmd):
                continue
            if cmd in known_skills:
                continue   # 是 skill 名，不是命令——不报
            ctx = text[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
            suspects.append({"kind": "command_name", "ref": cmd,
                             "why": "PATH 中无此命令且非 skill 名；可能是历史命令名，需人工确认",
                             "excerpt": ctx})

    # 去重
    def _dedup(items):
        out, seen = [], set()
        for it in items:
            k = (it["kind"], it["ref"])
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        return out

    ghosts, suspects, lazy = _dedup(ghosts), _dedup(suspects), _dedup(lazy)
    status = "BLOCKED" if ghosts else ("WATCH" if (suspects or lazy) else "OK")
    return {
        "status": status,
        "ghosts": ghosts,
        "suspects": suspects,
        "lazy": lazy,
        "ghost_count": len(ghosts),
        "suspect_count": len(suspects),
        "lazy_count": len(lazy),
        "checked": checked,
        "note": "规范化陷阱 #1：引用了不存在的路径/命令，即「规范文件幽灵引用」，"
                "其后果是一跑即崩或默默拿到空结果，非报错。只读扫描，不修文件。"
                "lazy=运行时才生成且文档明确说明的路径（降为 WATCH，不算幽灵）。"
                "command_name 类只给 WATCH：反引号标识符可能只是 skill 名/历史名，"
                "仅凭字符串无法与真命令区分（低假阳性优先于高召回）。",
    }


# ── 系统短板体检（把缺陷率公式接到真实缺口上，不是摆设字段）────────
#
# 设计意图：`defect_rate` 本身只是个计算器。接线才是价值所在——
# 把**本系统自身的真实缺口**映射为受监测维度，算出单一可比较的短板指标，
# 回答「现在最该修哪个」。
#
# 关键纪律（否则就是漂亮的假分数）：
#   1. 每一项缺口必须来自**实测**，不得手填
#   2. 缺数据时该维度纳入分母、计 0 分，**不得**从分母里偷偷移除
#      （移除分母 = 用“没测”冒充“没毛病”，是本库反复踩过的坑）
#   3. 总分不得用于提权（优先级仍看 R0-R4 与平台/法律硬约束）
SHORTFALL_DIMENSIONS = (
    # (维度名, 含义)
    ("gene_l5_explicit", "基因显式 L5 约束/验证覆盖（0 = 全靠推导，非作者本意）"),
    ("gene_signals", "基因信号可归一到规范信号表（0 = 全是自由文本）"),
    ("evidence_ledger", "证据账本有已登记条目（0 = 从未登记过证据）"),
    ("feedback_signal", "基因复用反馈有样本（0 = 无反馈，无法淘汰劣质基因）"),
    ("gate_coverage", "门禁覆盖未跟踪文件（0 = 只看已提交内容）"),
)


def _shortfall_defects() -> dict:
    """从**实测**推导各维度缺陷度（0.0-1.0，1.0=完全缺失）。

    每项都来自真实文件/真实扫描结果，不手填。
    取不到的维度的取值规则：**计 1.0（完全缺失）而不是丢弃**——
    取不到数据本身就是一种短板。
    """
    d: dict[str, float] = {}

    # ① 基因显式 L5 覆盖（实测：审计最近化）
    total, exp_c = 0, 0
    genes_dir = SANDBOX / "genes"
    for f in sorted(genes_dir.glob("genes-*.json")) if genes_dir.is_dir() else []:
        try:
            gs = json.loads(f.read_text(encoding="utf-8")).get("genes", [])
        except Exception:
            continue
        for g in gs:
            total += 1
            if isinstance(g.get("constraints"), dict) and g.get("constraints"):
                exp_c += 1
    d["gene_l5_explicit"] = 1.0 - (exp_c / total) if total else 1.0

    # ② 信号归一覆盖（实测：调同一函数，不重复实现）
    entries, norm = 0, 0
    for f in sorted(genes_dir.glob("genes-*.json")) if genes_dir.is_dir() else []:
        try:
            gs = json.loads(f.read_text(encoding="utf-8")).get("genes", [])
        except Exception:
            continue
        for g in gs:
            sm = g.get("signals_match") or []
            if sm:
                entries += 1
                if normalize_signals(sm)[0]:
                    norm += 1
    d["gene_signals"] = 1.0 - (norm / entries) if entries else 1.0

    # ③ 证据账本是否真被用过（实测：读账本文件）
    led = SANDBOX / "evidence" / "ledger.json"
    claims = 0
    try:
        if led.exists():
            claims = len(json.loads(led.read_text(encoding="utf-8")).get("claims", {}))
    except Exception:
        claims = 0
    d["evidence_ledger"] = 0.0 if claims > 0 else 1.0

    # ④ 反馈样本（实测：读 feedback.json）
    events = _load_feedback().get("events", [])
    d["feedback_signal"] = 0.0 if len(events) >= 3 else (1.0 if not events else 0.5)

    # ⑤ 门禁是否覆盖未跟踪文件（实测：真跑一次）
    try:
        _, diff = gate_paths_from_git(REPO)
        d["gate_coverage"] = 0.0 if diff else 1.0
    except Exception:
        d["gate_coverage"] = 1.0

    return d


def shortfall_report() -> dict:
    """系统短板体检：真实缺口 → 缺陷率 → 优先级建议。只读。"""
    defects = _shortfall_defects()
    # 分母固定为**全部声明维度**，不在报告里动态增减（否则历史不可比）
    n = len(SHORTFALL_DIMENSIONS)
    rate = defect_rate(defects, total_metrics=n)
    ranked = sorted(defects.items(), key=lambda kv: -kv[1])
    return {
        "status": "OK",
        "defect_rate": rate,
        "metrics_total": n,
        "boost_coeff": DEFECT_BOOST_COEFF,
        "dimensions": {k: round(v, 6) for k, v in defects.items()},
        "worst_first": [k for k, v in ranked if v > 0][:3],
        "healthy": [k for k, v in defects.items() if v == 0],
        "formula": "avg*0.5 + boost*max**1.5（最大短板非线性惩罚）",
        "boundary": "只读体检；总分**不得**用于提权（优先级仍看 R0-R4 与平台/法律硬约束）；"
                    "取不到数据的维度计 1.0 而非丢弃（没测≠没毛病）",
    }


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


def prune_genes(threshold: float = 0.5, min_samples: int = 2, dry_run: bool = False) -> dict:
    """Φ 淘汰无效基因：复用失败率 ≥ threshold 且样本 ≥ min_samples 的基因标记 deprecated。

    淘汰只写 deprecated.json（运行时过滤），不改原始基因文件，可回滚。
    dry_run=True 时只报告将淘汰谁、实际影响多少条基因，不写盘（假阳性自查）。
    """
    if not SANDBOX.is_dir() or not os.access(SANDBOX, os.W_OK):
        return {"status": "OK", "reason": "沙箱不可写/不存在，跳过淘汰（CI/无沙箱环境）", "newly_deprecated": [], "total_deprecated": 0, "file": ""}
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

    # 效果自查：淘汰名单必须真能过滤掉基因库中的条目（防"报 OK 但零生效"）
    target_ids = {c["gene_id"] for c in candidates} | existing
    bank = _load_gene_bank()
    affected = [g for g in bank if _gene_key(g) in target_ids]
    effect = {
        "bank_total": len(bank),
        "affected_in_bank": len(affected),
        "matched_after_prune": len([g for g in bank if _gene_key(g) not in target_ids]),
    }

    if dry_run:
        return {
            "status": "OK", "dry_run": True, "threshold": threshold, "min_samples": min_samples,
            "would_deprecate": [c["gene_id"] for c in new_deprecated],
            "candidates": candidates, "effect": effect,
            "note": "dry-run：未写盘。若 affected_in_bank=0 说明淘汰名单匹配不上基因库（假阳性陷阱）",
        }

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
    safe_write_text(dep_file, json.dumps(dep_payload, ensure_ascii=False, indent=2), "淘汰标记")
    return {
        "status": "OK",
        "threshold": threshold,
        "min_samples": min_samples,
        "newly_deprecated": [c["gene_id"] for c in new_deprecated],
        "total_deprecated": len(dep_payload["deprecated"]),
        "effect": effect,
        "file": str(dep_file),
        "note": "淘汰为运行时过滤（deprecated.json），原始基因文件保留，可回滚；effect.affected_in_bank=0 即淘汰零生效",
    }


# 五层应用门禁默认参数（对应 docs/GATES.md）
GATE_DEFAULTS = {
    "allowlist": [
        "scripts/", "tests/", "docs/", "examples/", "plugins/",
        "README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md",
        ".github/", ".gitignore", "LICENSE",
    ],
    "max_diff_lines": 200,
    # L3 分模式（2026-09-20 苹果哥批准）：
    # 问题：改老逻辑与加新模块混在同一阈值下，导致「加功能」每次都被 200 行拦住。
    # 设计原则（防橡皮图章）：放宽不得只靠「声明这是新功能」，必须有可验证准入条件：
    #   1. 变更集里**零删除/零修改**（`-` 行只允许出现在文件头 +++/---，即纯新增）
    #   2. 变更路径**全部是新增文件**（git status 中为 ??，不存在于 HEAD）
    #   3. 仍受硬上限约束（feature_max_diff_lines），不能无限放宽
    # 任一条件不满足 → 退回 evolution 模式的 200 行阈值（fail-closed）。
    "feature_max_diff_lines": 2000,
    "mode": "evolution",  # evolution | feature
    "secret_patterns": [
        r"sk-[A-Za-z0-9]{16,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][^'\"]{16,}['\"]",
    ],
    "danger_patterns": [
        r"rm\s+-rf\s+/",
        # 以下两条拼接构造：避免门禁规则自身被扫成命中（自指误报），源码不出现字面危险串
        r"push[^\n]*--" + r"force",
        r"push[^\n]*\s" + r"-f\b",
        r"--no" + r"-verify",
        r"chmod\s+777",
        r"(?:>|>>)\s*/(?:etc|System|usr)/",
        r"DROP\s+(?:TABLE|DATABASE)",
        r"launchctl\s+(?:unload|bootout)",
    ],
}


def _glob_match(path: str, patterns: list[str]) -> bool:
    """路径是否命中白名单模式（前缀目录或精确文件）。"""
    p = path.lstrip("./").strip()
    for pat in patterns:
        pat = pat.lstrip("./")
        if pat.endswith("/"):
            if p.startswith(pat) or p == pat.rstrip("/"):
                return True
        elif p == pat or p.startswith(pat):
            return True
    return False


def apply_gate(paths: list[str], diff_text: str = "", backup_dir: str | None = None,
               max_diff_lines: int | None = None, mode: str | None = None,
               added_paths: set[str] | None = None) -> dict:
    """五层应用门禁 L1-L5（对应 docs/GATES.md §1）：Apply 前的真刹车。

    修复（文档与实现不一致）：此前 GATES.md 写了 L1-L5 五层门禁，代码里 allowlist/backup 零匹配，
    宿主接入后拿不到任何刹车能力。本函数把五层落成可调用、可测的真检查。

    默认 fail-closed：任何一层失败即 BLOCKED，不做“自动放行”。

    mode="feature"（苹果哥 2026-09-20 批准）：放宽 L3 上限至 feature_max_diff_lines，
    但仅当变更集**纯新增**（零修改/删除行）且路径**全为新增文件**时生效；
    否则自动退回 evolution 阈值（fail-closed，防橡皮图章）。
    """
    if max_diff_lines is None:
        max_diff_lines = GATE_DEFAULTS["max_diff_lines"]
    added_paths = added_paths or set()
    checks: dict = {}
    diff_text = diff_text or ""

    # L1 备份
    if backup_dir:
        bd = Path(backup_dir)
        baks = sorted(bd.glob("*")) if bd.is_dir() else []
        checks["L1_backup"] = {"ok": bool(baks), "detail": f"{len(baks)} 个备份文件" if baks else f"未找到备份: {backup_dir}"}
    else:
        checks["L1_backup"] = {"ok": False, "detail": "未提供备份目录（变更前必须带时间戳备份）"}

    # L2 allowlist
    outside = [p for p in paths if not _glob_match(p, GATE_DEFAULTS["allowlist"])]
    checks["L2_allowlist"] = {
        "ok": not outside,
        "detail": "全部在白名单内" if not outside else f"越界路径: {outside}",
        "checked": len(paths),
    }

    # L3 diff 大小（分模式：evolution 严 / feature 宽但需可验证准入）
    diff_lines = len([ln for ln in diff_text.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))])
    mode = (mode or GATE_DEFAULTS["mode"]).lower()
    if mode == "feature":
        # 准入条件（必须全部满足，否则退回 evolution 阈值）
        removed = [ln for ln in diff_text.splitlines()
                   if ln.startswith("-") and not ln.startswith("---")]
        eligible = not removed and bool(paths) and all(p in added_paths for p in paths)
        eff_max = GATE_DEFAULTS["feature_max_diff_lines"] if eligible else max_diff_lines
        detail = (f"{diff_lines} 行变更（feature 模式上限 {eff_max}）"
                  if eligible else
                  f"{diff_lines} 行变更（feature 准入不满足，退回上限 {eff_max}）")
        checks["L3_diff_size"] = {
            "ok": diff_lines <= eff_max,
            "detail": detail,
            "diff_lines": diff_lines,
            "mode": mode,
            "feature_eligible": eligible,
            "ineligible_reason": (None if eligible else
                                  ("存在修改/删除行（非纯新增）" if removed else
                                   "存在非新增文件路径（修改了已有文件）")),
            "removed_lines": len(removed),
        }
    else:
        checks["L3_diff_size"] = {
            "ok": diff_lines <= max_diff_lines,
            "detail": f"{diff_lines} 行变更（上限 {max_diff_lines}）",
            "diff_lines": diff_lines,
            "mode": "evolution",
        }

    # L4 密钥扫描（只报位置与类型，不打印密钥值）
    hits = []
    for i, ln in enumerate(diff_text.splitlines(), 1):
        if not ln.startswith("+"):
            continue
        for pat in GATE_DEFAULTS["secret_patterns"]:
            m = re.search(pat, ln)
            if m:
                hits.append({"line": i, "type": pat[:24], "sha1": hashlib.sha1(m.group(0).encode()).hexdigest()[:8]})
                break
    checks["L4_secret_scan"] = {"ok": not hits, "detail": f"发现 {len(hits)} 处可疑密钥" if hits else "未发现明文密钥", "hits": hits}

    # L5 危险模式
    dangers = []
    for i, ln in enumerate(diff_text.splitlines(), 1):
        if not ln.startswith("+"):
            continue
        for pat in GATE_DEFAULTS["danger_patterns"]:
            if re.search(pat, ln):
                dangers.append({"line": i, "pattern": pat})
                break
    checks["L5_danger"] = {"ok": not dangers, "detail": f"发现 {len(dangers)} 处危险模式" if dangers else "未发现危险模式", "hits": dangers}

    failed = [k for k, v in checks.items() if not v["ok"]]
    return {
        "status": "BLOCKED" if failed else "PASS",
        "gate": "five-layer-apply",
        "checks": checks,
        "failed_layers": failed,
        "allow_apply": not failed,
        "note": "默认 allow_apply_default=false；L1-L5 任一失败即 BLOCKED（fail-closed）",
    }


def gate_paths_from_git(repo: Path | None = None,
                        max_untracked_bytes: int = 2_000_000) -> tuple[list[str], str]:
    """从 git 工作区取变更路径与 diff（只读），供 --gate 无参自检。

    修复（假阴性漏洞）：此前只把 `git diff HEAD` 当 diff_text，但未跟踪文件（新增文件）
    的内容不在 `git diff HEAD` 里——导致 L3 diff 大小与 L4 密钥扫描**双双漏检新文件**。
    实测：新增 1729 行代码时 L3 只报 56 行，且新文件里的密钥不会被 L4 发现。
    现把未跟踪文件内容按「全新增行」合成进 diff_text，使 L3/L4 真正覆盖新文件。
    """
    import subprocess
    cwd = str(repo or PLUGIN_DIR.parent)
    try:
        d = subprocess.run(["git", "-C", cwd, "diff", "HEAD"], capture_output=True, text=True, timeout=30)
        names = subprocess.run(["git", "-C", cwd, "diff", "--name-only", "HEAD"], capture_output=True, text=True, timeout=30)
        untracked = subprocess.run(["git", "-C", cwd, "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, timeout=30)
    except Exception:
        return [], ""
    paths = [p for p in (names.stdout + untracked.stdout).splitlines() if p.strip()]
    diff_text = d.stdout

    # 把未跟踪文件内容并入 diff_text（全按新增行），否则 L3/L4 会漏掉新文件
    repo_root = Path(cwd)
    extra = []
    for rel in [p for p in untracked.stdout.splitlines() if p.strip()]:
        fp = repo_root / rel
        try:
            if not fp.is_file() or fp.stat().st_size > max_untracked_bytes:
                # 超大/非文件：仍要在 diff 里留可审计痕迹，但不读内容
                extra.append(f"--- /dev/null\n+++ b/{rel}\n+[未跟踪文件，超过 {max_untracked_bytes} 字节或非普通文件，未读入内容]")
                continue
            body = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            extra.append(f"--- /dev/null\n+++ b/{rel}\n+[未跟踪文件读取失败]")
            continue
        lines = body.splitlines()
        extra.append(f"--- /dev/null\n+++ b/{rel}\n" + "\n".join("+" + ln for ln in lines))
    if extra:
        diff_text = diff_text + "\n" + "\n".join(extra)
    return paths, diff_text


def git_added_paths(repo: Path | None = None) -> set[str]:
    """返回工作区中**新增文件**路径集（未跟踪 + 已暂存新增）。

    feature 模式准入用：只有当变更路径全在这个集合里（= 纯新增文件、
    没动任何已有文件）才允许放宽 L3 上限。
    """
    import subprocess
    cwd = str(repo or PLUGIN_DIR.parent)
    out: set[str] = set()
    try:
        r1 = subprocess.run(["git", "-C", cwd, "ls-files", "--others", "--exclude-standard"],
                            capture_output=True, text=True, timeout=30)
        out |= {p for p in r1.stdout.splitlines() if p.strip()}
        # 已暂存的新增（A）文件
        r2 = subprocess.run(["git", "-C", cwd, "diff", "--cached", "--name-status", "HEAD"],
                            capture_output=True, text=True, timeout=30)
        for ln in r2.stdout.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2 and parts[0].startswith("A"):
                out.add(parts[1])
    except Exception:
        return out
    return out


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
        "evidence": _evidence_and_ledger_summary(),
        "repair_hint": h["repair_hint"],
        "note": "统一状态入口：健康/基因/反馈/证据账本一处可查（Λ_ctx 切换损耗收敛）",
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
    # 修复：此前注释声称"记录到 gaps/"，实际不落盘，Observe 阶段无证据可追。
    # 现在写入 loop-<task>/runs/（无工作区时自动建），落盘失败不影响只读返回。
    written = None
    try:
        runs_dir = SANDBOX / f"loop-{task_name}" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        out = runs_dir / f"substitute-{order}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        written = str(out)
    except Exception as e:
        rec["persist_error"] = str(e)
    return {"status": "OK", "substitute": rec, "recorded_to": written}


def eval_runs(task_name: str, set_name: str | None = None) -> dict:
    """评测汇总：读取 loop-<task>/runs/ 下的评测运行文件，统计命中率。

    set_name 可选 warmup/holdout/holdout2/all（默认 all）。
    从 run 文件中的 results[].sample_id / summary 统计。

    修复（假阳性）：此前 hit/total/best_rate 三个数各自跨 run 取 max，分子分母不同源，
    会算出根本不存在的命中率（如 A=2/5 与 B=3/30 被拼成 hit=3 total=30 rate=0.4，真实比值 0.1）。
    现在每个 run 的 hit/total/rate 同源记录：latest 反映最近一次真实性能（退步可见），
    best 单独标注为历史最优。
    """
    runs_dir = SANDBOX / f"loop-{task_name}" / "runs"
    if not runs_dir.is_dir():
        return {"status": "BLOCKED", "reason": f"无评测目录: {runs_dir}"}
    files = sorted(runs_dir.glob("*.json"))
    if not files:
        return {"status": "BLOCKED", "reason": "runs/ 下无评测文件（先跑 run_frozen_eval.py 生成）"}

    per_key: dict = {}
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
        mtime = f.stat().st_mtime
        for k, v in s.items():
            if not isinstance(v, dict) or "rate" not in v:
                continue
            key = f"{run_set or 'all'}:{k}"
            hit, total = int(v.get("hit", 0)), int(v.get("total", 0))
            rate = float(v.get("rate", 0.0))
            # 同源三元组：一条记录内 hit/total/rate 必须来自同一个 run 文件
            per_key.setdefault(key, []).append(
                {"file": f.name, "hit": hit, "total": total, "rate": round(rate, 3), "mtime": mtime})
        samples.append({"file": f.name, "set": run_set})

    summary = {}
    for key, entries in per_key.items():
        ordered = sorted(entries, key=lambda e: (e["mtime"], e["file"]))
        latest = ordered[-1]
        best = max(ordered, key=lambda e: e["rate"])
        pick = lambda e: {kk: e[kk] for kk in ("file", "hit", "total", "rate")}
        summary[key] = {
            "runs": len(ordered),
            "latest": pick(latest),
            "best": pick(best),
            "history": [pick(e) for e in ordered],
        }

    return {
        "status": "OK",
        "task": task_name,
        "set": set_name or "all",
        "runs": len(samples),
        "files": [s["file"] for s in samples],
        "summary": summary,
        "note": "每个 run 的 hit/total/rate 同源（不再跨 run 拼分子分母）；latest=最近一次真实性能（退步可见），best=历史最优仅供参考；warmup 可调优，holdout 提升才算真泛化",
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
    safe_write_text(gene_file, json.dumps(payload, ensure_ascii=False, indent=2), "基因库")
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
    safe_write_text(gene_file, json.dumps(payload, ensure_ascii=False, indent=2), "基因库")
    return {"status": "OK", "task": task_name, "gene_count": len(llm_genes), "gene_file": str(gene_file), "genes": llm_genes, "llm": True, "provider": provider, "model": resp["model"]}


# ══════════════════════════════════════════════════════════════════════
# 基因 L5 约束层（constraints / validation）
# ══════════════════════════════════════════════════════════════════════
#
# 来源：Apex 资源盘《标准基因模版》给出的母体基因 5 层结构：
#   L1 元(id/category) L2 触发(signals_match) L3 前置(preconditions)
#   L4 执行(strategy) L5 约束(constraints/validation)
#
# 实测缺口：本库 53 条基因中 constraints=0、validation=0、preconditions=5。
# 即基因只存了「怎么做」，没存「什么条件下别用」和「怎么验证做对了」——
# 这正是「文件存在冒充能力完成」在基因层的同构缺陷。
#
# 设计原则（不得凭空编造）：
#   1. 能从已有字段推导的（如策略里的回滚/备份步骤）→ 推导并标注来源
#   2. 推导不出的 → 显式标 UNSPECIFIED，不假装有
#   3. 复用时按缺失度给 confidence 警告，而非静默使用

# 能从 strategy/mechanism 文本里可靠识别的约束信号（保守，宁少勿错）
CONSTRAINT_DERIVE_RULES = (
    {
        "name": "rollback_required",
        "pattern": r"回滚|回退|恢复(?:到|至)?(?:快照|备份|原状)|\.bak|备份",
        "meaning": "该基因执行前必须准备可回滚手段",
    },
    {
        "name": "readback_required",
        "pattern": r"读回|验证|确认|核对|实测",
        "meaning": "该基因执行后必须读回验证，未读回不算完成",
    },
    {
        "name": "authorization_required",
        "pattern": r"授权|审批|人工|门禁|确认后再|待批准",
        "meaning": "该基因涉及门禁项，未授权必须冻结而非默默执行",
    },
    {
        "name": "sandbox_only",
        "pattern": r"沙箱|隔离|staging|候选区",
        "meaning": "该基因只能在隔离区执行，不得直写正本/生产",
    },
    {
        "name": "no_production_write",
        "pattern": r"禁止.*(?:正本|生产|直写)|不得.*(?:正本|生产|直写)|只读",
        "meaning": "该基因不得对正本或生产环境写入",
    },
)

UNSPECIFIED = "UNSPECIFIED"


def derive_gene_constraints(gene: dict) -> dict:
    """从已有字段**推导**约束；推不出的显式标 UNSPECIFIED（不编造）。

    返回 {"constraints": {...}, "derived_from": "text-derivation"|"none",
          "unspecified": [...], "explicit": bool}

    若基因已显式带 constraints，则以显式为准（显式 > 推导）。
    """
    existing = gene.get("constraints")
    if isinstance(existing, dict) and existing:
        return {"constraints": existing, "derived_from": "explicit",
                "unspecified": [], "explicit": True}

    haystack = " ".join([
        str(gene.get("mechanism", "")),
        " ".join(str(x) for x in (gene.get("strategy") or [])),
        " ".join(str(x) for x in (gene.get("signals_match") or [])),
        str(gene.get("resolution", "")),
    ])
    found, unspecified = {}, []
    for rule in CONSTRAINT_DERIVE_RULES:
        if re.search(rule["pattern"], haystack):
            found[rule["name"]] = rule["meaning"]

    # 环境约束：无沙箱/隔离信号的基因，环境边界未知
    if "sandbox_only" not in found and "no_production_write" not in found:
        unspecified.append("environment_scope")
    # 前置条件：绝大多数基因没有
    if not gene.get("preconditions"):
        unspecified.append("preconditions")

    return {"constraints": found, "derived_from": "text-derivation" if found else "none",
            "unspecified": unspecified, "explicit": False}


def derive_gene_validation(gene: dict) -> dict:
    """推导验证标准。有显式 validation 用显式；否则按类型给**可执行**检查。

    关键：给出的命令必须真能跑（不写假命令）。推不出就标 UNSPECIFIED。
    """
    existing = gene.get("validation")
    if isinstance(existing, list) and existing:
        return {"validation": existing, "derived_from": "explicit", "explicit": True}

    gtype = str(gene.get("type") or gene.get("category") or "")
    strategy = " ".join(str(x) for x in (gene.get("strategy") or []))
    checks = []

    # 通用可执行检查：基因文件本身必须仍是合法 JSON 且含必需字段
    checks.append("python3 -c \"import json,glob;[json.load(open(f,encoding='utf-8')) for f in glob.glob('genes/genes-*.json')];print('gene bank parses OK')\"")

    if re.search(r"读回|验证|确认|核对|实测", strategy + str(gene.get("mechanism", ""))):
        checks.append("执行后读回目标文件并比对预期（命中数≥1 且内容一致）")
    if re.search(r"备份|\.bak", strategy + str(gene.get("mechanism", ""))):
        checks.append("确认备份文件已生成且非空（ls -l <target>.bak-*）")
    if gtype in ("gap",):
        checks.append("确认缺口已复现（有 reproduction 记录），未复现不得标记已修复")

    return {"validation": checks, "derived_from": "type-template", "explicit": False,
            "note": "模板推导的检查；未与真实产物绑定时不得声称已通过"}


def gene_l5_report(genes: list[dict] | None = None) -> dict:
    """基因库 L5 完整度审计：逐条给出推导后的约束/验证 + 缺口统计。只读。"""
    genes = _load_gene_bank() if genes is None else genes
    rows, miss_c = [], 0
    miss_v = 0
    for g in genes:
        c = derive_gene_constraints(g)
        v = derive_gene_validation(g)
        if not c["explicit"]:
            miss_c += 1
        if not v["explicit"]:
            miss_v += 1
        rows.append({
            "gene": _gene_key(g),
            "constraints_source": c["derived_from"],
            "constraints": c["constraints"],
            "unspecified": c["unspecified"],
            "validation_source": v["derived_from"],
            "validation_count": len(v["validation"]),
        })
    total = len(genes)
    return {
        "status": "OK", "total": total,
        "explicit_constraints": total - miss_c, "derived_constraints": miss_c,
        "explicit_validation": total - miss_v, "derived_validation": miss_v,
        "l5_gap": {
            "constraints_explicit_pct": round((total - miss_c) / total * 100, 1) if total else 0.0,
            "validation_explicit_pct": round((total - miss_v) / total * 100, 1) if total else 0.0,
        },
        "boundary": "推导值来自文本规则，不等于作者本意；显式字段为 0 是真实缺口，不得当已完成",
        "rows": rows,
    }


def gene_l5_backfill(dry_run: bool = True) -> dict:
    """把推导出的 L5 回填进基因文件（默认 dry-run）。

    回填只写 derived 标记字段（_l5_constraints/_l5_validation/_l5_derived），
    **不伪造** constraints/validation 本体——原字段仍为空，保持缺口可见。
    """
    genes_dir = SANDBOX / "genes"
    files = sorted(genes_dir.glob("genes-*.json")) if genes_dir.is_dir() else []
    touched, planned = 0, []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        changed = False
        for g in d.get("genes", []):
            c = derive_gene_constraints(g)
            v = derive_gene_validation(g)
            if not c["explicit"]:
                g["_l5_constraints"] = c["constraints"]
                g["_l5_unspecified"] = c["unspecified"]
                changed = True
            if not v["explicit"]:
                g["_l5_validation"] = v["validation"]
                changed = True
        if changed:
            planned.append({"file": f.name, "genes": len(d.get("genes", []))})
            if not dry_run:
                f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                touched += 1
    return {"status": "OK", "dry_run": dry_run, "files": planned,
            "files_written": touched if not dry_run else 0,
            "note": "回填 _l5_* 派生字段，不填 constraints/validation 本体（缺口保持可见）"}


def _gene_key(g: dict) -> str:
    """基因稳定标识：有 id 用 id；规则提取的基因没有 id，按 source+category+mechanism 派生稳定键。

    修复（假阳性 OK）：此前淘汰判定直接用 g.get("id")，而规则提取的基因无 id 字段
    → 取值恒为 None → `None in deprecated` 恒为假 → 永不淘汰，但 prune 仍返回 status=OK。
    派生键保证同一基因跨次加载得到同一标识，淘汰过滤与反馈统计都能对上。
    """
    gid = g.get("id")
    if gid:
        return str(gid)
    basis = "|".join([
        str(g.get("_source", "")),
        str(g.get("category") or g.get("module") or g.get("type") or ""),
        str(g.get("mechanism", ""))[:80],
    ])
    return "derived-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


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
                g["_deprecated"] = _gene_key(g) in deprecated
                all_genes.append(g)
    return all_genes


# ── APEX 标准信号词表（吸收自资源盘 1.Apex仓库/apex-spiral/apex-standard）──────
# 来源：`APEX-GENE-STANDARD-EXT.md` §4.2「APEX Shannon Law 24 个核心信号」。
# 该规范的核心主张：基因选择必须走**信号精确匹配**，而非模糊词频——
#     selected_gene = argmax(gene ∈ candidate_pool) { signal_match_score }
# 本库基因的 signals_match 历史上写成中文自然语言长句（如
# 「准备修改记忆/配置/脚本/SOUL 类文件」），无法做精确信号匹配。
# 本表把中文表述归一到 24 个标准信号键，使匹配可比较、可统计。
# 诚实边界：中文关键词是**工程映射**，不是规范原文；映射不到的信号保留原样，
# 不丢弃、不编造归属（见 normalize_signals 的 unmapped 返回）。
SIGNAL_TAXONOMY: dict[str, list[str]] = {
    "error": ["报错", "错误", "异常", "故障", "不可用", "告警"],
    "exception": ["异常状态", "抛错", "traceback", "崩栈"],
    "failed": ["失败", "未通过", "未生效", "失效", "未验证", "未注册", "未落地"],
    "unstable": ["不稳定", "漂移", "抖动", "不一致"],
    "validation": ["校验", "验证失败", "测试失败", "门禁拦截", "合规检查"],
    "protocol": ["协议错误", "接口不符", "类型签名", "契约"],
    "perf_bottleneck": ["性能瓶颈", "慢", "耗时", "卡顿"],
    "resource_waste": ["资源浪费", "冗余", "重复消耗"],
    "repeated_code": ["重复代码", "重复实现", "重复定义", "多套模板"],
    "latency_high": ["高延迟", "延迟高", "超时"],
    "capability_gap": ["能力缺口", "缺少", "尚未具备", "短板", "未接线"],
    "stagnation": ["停滞", "无进展", "无增益"],
    "new_requirement": ["新需求", "新功能", "新增工具", "新增参数"],
    "unknown_domain": ["未知领域", "不熟悉", "首次接触"],
    "survey": ["调研", "摸排", "盘点", "清点"],
    "multi_component": ["多组件", "跨模块", "多子系统", "协同"],
    "complex_workflow": ["复杂流程", "多阶段", "全流程", "闭环流程"],
    "self_evolve": ["自进化", "自我进化", "自举", "进化闭环"],
    "gene_heal": ["基因自愈", "基因库损坏", "基因失效"],
    "root_cause": ["根因", "定位", "溯源", "诊断"],
    "recurring_error": ["反复", "复发", "重复出现", "再次"],
    "tool_bypass": ["绕过", "旁路", "跳过后禁", "规避"],
    "hub_search_miss": ["检索未命中", "未命中", "搜不到", "知识库缺"],
    "risk_high": ["高风险", "不可逆", "生产", "凭据", "密钥", "越权"],
}

# ── PGG 本地扩展信号（**非** APEX 原文）────────────────────────────────────
# 本库有 34/53 条基因的 signals_match 描述的是 APEX 24 信号**盖不住**的场景，
# 主要是「正本/记忆变更」「对外身份表述」「交付真实性」三类。
# 诚实边界：以下是**本项目的工程扩展**，不是 APEX 规范原文；
# 可扩充，但不得拿它冒充「符合 APEX 标准」。
PGG_LOCAL_SIGNALS: dict[str, list[str]] = {
    "canonical_write_intent": [
        "正本", "记忆正本", "外置大脑", "SOUL", "人格设定",
        "PGG-WIKI", "staging", "promotion", "自修改", "宿主侧文件",
        "其他 agent 读取", "追加内容", "修改记忆", "修改配置", "修改脚本",
    ],
    "external_claim": [
        "对外", "身份表述", "资质", "能力边界", "公开日志", "第三方",
    ],
    "delivery_authenticity": [
        "冒充", "声称", "宣称", "文档冒充", "清单与现状不符", "未落地",
    ],
    "host_migration": [
        "跨宿主", "迁移", "常驻服务", "上线", "重装", "升级后",
        "bundle", "版本号变化", "模型数据源",
    ],
    "service_outage": [
        "0%", "全面瘫痪", "不可用", "未迁移", "部分能力",
    ],
    "periodic_review": [
        "周期性", "自省窗口", "待处理事项", "排优先级", "系统整体状态",
        "AGI 演进", "阶段性质疑",
    ],
    "constitution_ambiguity": [
        "目标定义模糊", "无法判定完成", "扫描器", "门禁", "判噪",
        "接口清单", "参数声明", "无执行路径", "待改进",
    ],
    "verbosity_noise": [
        "监控输出", "噪声", "告警与实际状态不符",
    ],
    "doc_code_drift": [
        "文档/help", "承诺", "README", "端到端跑通", "参数但代码无实现",
        "接口清单与实际行为不符",
    ],
    "reproducible_delivery": [
        "可复现", "评测闭环", "可追溯", "交付证据", "提交前检查",
        "验证能力闭环", "自举", "作用于自身",
    ],
    "code_duplication": [
        "互不兼容", "同类模板", "分散在多个",
    ],
    "completion_claim": [
        "功能开发完成", "测试与类型检查已通过", "待改进", "无对应代码",
    ],
}


def _all_signal_tables() -> dict[str, list[str]]:
    """APEX 24 标准信号 + PGG 本地扩展，保持可区分（本地项以 pgg_ 前缀标记）。"""
    merged = dict(SIGNAL_TAXONOMY)
    for k, v in PGG_LOCAL_SIGNALS.items():
        merged[f"pgg_{k}"] = v
    return merged


# 信号权重：精确信号命中比自由词命中更能说明「该基因适配此任务」
SIGNAL_MATCH_WEIGHT = 5


def normalize_signals(raw) -> tuple[set[str], list[str]]:
    """把基因的 signals_match 归一到标准信号键。

    返回 (标准键集合, 未映射的原始信号列表)。
    未映射项**原样保留**在第二项里，不丢弃、不强加归属——
    否则会把「没归类」冒充成「已归类」，正是本项目要防的假完成。
    """
    if isinstance(raw, str):
        raw = [raw]
    table = _all_signal_tables()
    hits: set[str] = set()
    unmapped: list[str] = []
    for item in (raw or []):
        s = str(item or "").strip()
        if not s:
            continue
        matched = False
        low = s.lower()
        for key, kws in table.items():
            if any(
                (kw in s) if re.search(r"[\u4e00-\u9fff]", kw)
                else (kw in low)
                for kw in kws
            ):
                hits.add(key)
                matched = True
        if not matched:
            unmapped.append(s)
    return hits, unmapped


def classify_task_signals(task_desc: str) -> set[str]:
    """从任务描述里提取标准信号（用于对齐基因的 signals_match）。"""
    hits, _ = normalize_signals([task_desc])
    return hits


def match_genes(task_desc: str, top_n: int = 3, include_deprecated: bool = False) -> dict:
    """基因匹配复用（D12 元学习闭环）：新任务描述匹配历史基因。

    匹配 = 自由词频 + **标准信号精确命中加权**（权重 SIGNAL_MATCH_WEIGHT）。
    后者吸收自资源盘 APEX 基因标准：`selected_gene = argmax(signal_match_score)`，
    即信号匹配应是主判据，而非字符包含数量。
    默认排除已淘汰基因（Φ 反馈淘汰的运行时过滤）。
    """
    genes = _load_gene_bank()
    if not genes:
        return {"status": "BLOCKED", "reason": "基因库为空（先跑 --gene 或 --gene-llm）"}
    if not include_deprecated:
        genes = [g for g in genes if not g.get("_deprecated")]
        if not genes:
            return {"status": "OK", "task": task_desc, "matched": 0, "genes": [], "note": "可用基因全被淘汰，可新建闭环或 --include-deprecated 查看历史"}
    task_signals = classify_task_signals(task_desc)
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
        # 标准信号精确命中加权：这是 APEX 规范指定的主判据
        gene_signals, _unmapped = normalize_signals(g.get("signals_match"))
        overlap = task_signals & gene_signals
        if overlap:
            score += SIGNAL_MATCH_WEIGHT * len(overlap)
            g = {**g, "_signal_overlap": sorted(overlap)}
        scored.append((score, g))
    scored.sort(key=lambda x: -x[0])
    top = [g for s, g in scored if s > 0][:top_n]
    if not top:
        return {"status": "OK", "task": task_desc, "matched": 0, "genes": [], "note": "无匹配基因，可新建闭环"}
    # 统一补 id：规则提取基因原本无 id，导致 feedback 记录 gene_id=None → prune 跳过 → 淘汰闭环断裂
    out, warned = [], 0
    for g in top:
        c = derive_gene_constraints(g)
        v = derive_gene_validation(g)
        row = {**{k: g[k] for k in ("category", "mechanism", "signals_match", "strategy", "_source") if k in g},
               "id": _gene_key(g)}
        # 标准信号交集（APEX 规范指定的主判据）：必须随结果回传，
        # 否则调用方无法区分「信号命中」与「仅词频命中」——匹配质量不可审计。
        if g.get("_signal_overlap"):
            row["_signal_overlap"] = g["_signal_overlap"]
        # L5 约束层：缺显式约束/验证时必须显式警告，不得静默复用
        if not c["explicit"] or not v["explicit"]:
            warned += 1
            row["l5_warning"] = (
                "本基因缺显式 L5（constraints/validation）——复用前必须自己确定："
                "①什么条件下不该用 ②怎么验证做对了。推导值仅供参考，不是作者本意。"
            )
            row["l5_derived"] = {"constraints": c["constraints"],
                                 "unspecified": c["unspecified"],
                                 "validation": v["validation"],
                                 "derived_from": f"{c['derived_from']}/{v['derived_from']}"}
        out.append(row)
    return {"status": "OK", "task": task_desc, "matched": len(top),
            "l5_missing": warned, "genes": out,
            "note": "匹配基因供复用：参考 strategy 修复步骤，勿机械照搬（需人工复核）；"
                    f"{warned}/{len(top)} 条缺显式 L5 约束层"}


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
    safe_write_text(doc_path, "\n".join(lines), "结算文档")
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
    safe_write_text(gene_file, json.dumps(payload, ensure_ascii=False, indent=2), "基因库")
    return {"status": "OK", "task": topic, "matched_memory": len(rows), "gene_count": len(llm_genes), "gene_file": str(gene_file), "genes": llm_genes, "llm": True}


# ── D03 风险分级 R0-R4（吸收自成果包 03-智能体学习与进化实施手册 §2.2）─────
# 该手册的风险分级表是本项目缺失的一层：门禁只管「改了多少、改哪了」，
# 不管「这类改动本身该不该自动」。R0-R4 补上「按变更性质定权限」。
#
# 原文优先级（必须原样保留，不得被评分/公式抬高权限）：
#   平台与法律硬约束 > 用户明确指令 > 数据治理/安全策略 > 任务策略/评分公式
#
# 诚实边界：本表是**本地工程映射**（原本为部署模板，不声称已部署）。
RISK_TIERS: dict[str, dict] = {
    "R0": {
        "label": "只读研究",
        "examples": "检索、目录盘点、离线分析",
        "auto_allowed": True,
        "needs_human": False,
        "requires": ["source_and_data_class_check"],
    },
    "R1": {
        "label": "低影响实验",
        "examples": "提示、评测规则、非敏感技能卡草案",
        "auto_allowed": True,
        "needs_human": False,
        "requires": ["schema", "unit_test", "peer_review"],
    },
    "R2": {
        "label": "受控变更",
        "examples": "非生产代码/配置、路由策略、已批准知识资产",
        "auto_allowed": False,
        "needs_human": True,
        "requires": ["reproduce", "test_matrix", "dependency_security_audit", "independent_review"],
    },
    "R3": {
        "label": "高影响变更",
        "examples": "生产策略、持久化、外部 API、成本显著增加、敏感数据处理",
        "auto_allowed": False,
        "needs_human": True,
        "requires": ["security_privacy_review", "change_owner_and_business_auth",
                      "gray_release_plan", "incident_plan"],
    },
    "R4": {
        "label": "严格受限",
        "examples": "凭据、权限、模型/权重、法律对外输出、生产删除/迁移、第三方发布",
        "auto_allowed": False,   # 原文：禁止自动执行
        "needs_human": True,
        "requires": ["compliance_security_domain_written_auth", "two_person_review",
                      "full_recovery_drill"],
    },
}

# 风险特征 → 最低层级。判定取**命中项中的最高层级**（就高不就低）。
RISK_TIER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("R4", re.compile(r"凭据|credential|token|secret|api.?key|密钥|密码|oauth"
                      r"|模型权重|权重文件|删除生产|数据迁移|第三方发布|对外法律|出庭|法律意见", re.I)),
    ("R3", re.compile(r"生产|production|持久化|外发|外部 ?api|成本|账单|计费|敏感数据|隐私"
                      r"|正式案件|案卷|当事人", re.I)),
    ("R2", re.compile(r"非生产代码|路由策略|已批准知识资产|宿主配置|launchd"
                      r"|自进化内核|核心逻辑|门禁实现", re.I)),
    ("R1", re.compile(r"提示|prompt|评测规则|测试夹具|技能卡|文档", re.I)),
    ("R0", re.compile(r"只读|检索|盘点|离线分析|扫描|审计报告|健康检查", re.I)),
]


def classify_risk_tier(change_desc: str) -> dict:
    """按变更描述判定风险层级（R0-R4）。

    取命中项中的**最高**层级（R4 > R3 > R2 > R1 > R0），就高不就低。
    无任何命中 → 保守落到 R2（不降级到 R0，不给未知变更自动放行）。

    关键不变式：
      - R2/R3/R4 一律 auto_allowed=False（不得自动执行，与 diff 大小无关）
      - R4 额外要求双人复核
      - 该判定**不得**被 ΔG/fitness/任何分数抬高权限
    """
    text = change_desc or ""
    hits: list[str] = []
    for tier, pat in RISK_TIER_PATTERNS:
        if pat.search(text):
            hits.append(tier)
    order = ["R0", "R1", "R2", "R3", "R4"]
    tier = max(hits, key=lambda t: order.index(t)) if hits else "R2"
    spec = RISK_TIERS[tier]
    return {
        "tier": tier,
        "label": spec["label"],
        "matched_tiers": sorted(set(hits), key=lambda t: order.index(t)),
        "auto_allowed": spec["auto_allowed"],
        "human_required": spec["needs_human"],
        "required": spec["requires"],
        "is_conservative_default": not hits,
        "boundary": "风险层级由变更性质决定，不由评分/公式抬高权限",
    }


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
    ap.add_argument("--gene-l5", action="store_true", help="基因 L5 约束层审计：逐条推导 constraints/validation 并统计缺口（只读）")
    ap.add_argument("--shortfall", action="store_true",
                    help="系统短板体检：把实测缺口喂进缺陷率公式，给出最该修的维度（只读）")
    ap.add_argument("--gene-l5-backfill", action="store_true", help="回填推导的 L5 派生字段（默认 dry-run，需 --apply 才写盘）")
    ap.add_argument("--health-deep", action="store_true", help="Ψ 深度健康监测：基因库/记忆库/沙箱/反馈完整性")
    ap.add_argument("--no-memory", action="store_true", help="health-deep 跳过记忆库检查（CI/无记忆库环境用）")
    ap.add_argument("--feedback", nargs=3, metavar=("TASK_DESC", "OUTCOME", "GENE_ID"), help="Φ 记录基因复用反馈：success/failure（GENE_ID 可省略填 -）")
    ap.add_argument("--feedback-stats", action="store_true", help="Φ 反馈统计：按基因/总体复用成功率")
    ap.add_argument("--prune-genes", nargs="?", const="0.5", metavar="THRESHOLD", help="Φ 淘汰无效基因：失败率≥阈值(默认0.5)且样本≥2 标记 deprecated")
    ap.add_argument("--apply", action="store_true", help="配合 --gene-l5-backfill：真写盘（否则 dry-run）")
    ap.add_argument("--gate", nargs="*", metavar="PATH", help="五层应用门禁 L1-L5 自检（无参时取 git 工作区变更；可显式传路径）")
    ap.add_argument("--gate-backup", metavar="DIR", help="--gate 的 L1 备份目录")
    ap.add_argument("--gate-mode", choices=["evolution", "feature"], default=None,
                    help="--gate 的 L3 模式：evolution(默认,200行) | feature(纯新增可至2000行，需可验证准入)")
    ap.add_argument("--evidence", metavar="CLAIM_ID", help="登记一条主张的证据等级（需 --level；不满足最低要求则拒绝登记）")
    ap.add_argument("--level", choices=list(EVIDENCE_LEVELS), help="证据等级 E0-E9（配合 --evidence）")
    ap.add_argument("--artifact", default="", metavar="PATH", help="证据工件路径（E1-E9）")
    ap.add_argument("--verify-cmd", metavar="CMD", help="E3/E5/E6 的可执行校验命令")
    ap.add_argument("--execute", action="store_true", help="显式授权真跑校验命令（否则只登记不执行）")
    ap.add_argument("--note", default="", help="说明：E0 的主张文本 / E2 的符号名 / 其他备注")
    ap.add_argument("--claim-text", default="", metavar="TEXT", help="主张原文（夸大词扫描用；与 --note 分离，避免 E2 符号名占用此位）")
    ap.add_argument("--evidence-status", nargs="?", const="", metavar="CLAIM_ID", help="读回证据链，派生允许的状态词（不带值=全部）")
    ap.add_argument("--list-levels", action="store_true", help="列出 E0-E9 证据等级及其能/不能证明什么")
    ap.add_argument("--include-deprecated", action="store_true", help="匹配/统计时包含已淘汰基因")
    ap.add_argument("--dry-run", action="store_true", help="配合 --prune-genes：只报告影响范围，不写盘")
    ap.add_argument("--claim-scan", metavar="TEXT", help="D07 排除词表扫描：判定文本能否作为现状/能力/授权结论")
    ap.add_argument("--claim-file", metavar="PATH", help="D07 扫描：从文件读文本（用于汇报/文档）")
    ap.add_argument("--unwired-scan", metavar="TEXT", help="D05 §6.1 扫描：检查是否把「未接线」说成「已运行」")
    ap.add_argument("--status", action="store_true", help="Λ_ctx 统一状态入口：健康+基因+反馈一处汇总")
    ap.add_argument("--risk-classify", metavar="CHANGE_DESC",
                    help="D03 风险分级 R0-R4：按变更描述定权限层级（只读判定）")
    ap.add_argument("--defect-rate", metavar="SPEC",
                    help="最大短板非线性惩罚：SPEC 形如 '名字=值,名字=值'（只读计算）")
    ap.add_argument("--defect-total", type=int, default=None, metavar="N",
                    help="--defect-rate 的受监测维度总数（默认=传入项数；传 12 可复现上游 EVM 口径）")
    ap.add_argument("--ghost-scan", metavar="PATH_OR_TEXT",
                    help="幽灵引用扫描：传存在的文件路径→读文件扫；否则当文本扫（只读）")
    ap.add_argument("--set", choices=["warmup", "holdout", "holdout2", "all"], default="all", help="评测集合（默认 all）")
    args = ap.parse_args()

    # 只读模式（PGG_EVOLUTION_READONLY=1）：按动作分类拦截写操作。
    # 分三类：纯读（放行）/ 会写盘（拦）/ 可能写盘（拦，需显式解除只读）。
    if is_readonly():
        READONLY_BLOCKED = {
            "init": args.init, "gene": args.gene, "gene_llm": args.gene_llm,
            "gene_sync": args.gene_sync, "gene_from_memory": args.gene_from_memory,
            "evidence": args.evidence, "feedback": args.feedback,
            "prune_genes": args.prune_genes, "gate_backup": args.gate_backup,
            "substitute": args.substitute, "llm": args.llm,
        }
        blocked = [k for k, v in READONLY_BLOCKED.items() if v not in (None, False)]
        if args.gene_l5_backfill and args.apply:
            blocked.append("gene_l5_backfill")
        if blocked:
            print(json.dumps({
                "status": "READONLY_BLOCKED", "readonly": True, "env": READONLY_SWITCH,
                "blocked_actions": blocked,
                "reason": "只读模式拒绝写盘动作（Agent_read ∩ ¬Agent_edit = Max(Safety)）",
                "hint": f"如需写盘：unset {READONLY_SWITCH}",
            }, ensure_ascii=False, indent=2))
            return 1

    if args.health:
        print(json.dumps(health_check(), ensure_ascii=False, indent=2))
        return 0
    if args.health_deep:
        print(json.dumps(health_deep(check_memory=not args.no_memory), ensure_ascii=False, indent=2))
        return 0
    if args.claim_scan or args.claim_file:
        if args.claim_file:
            try:
                txt = Path(args.claim_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
                return 2
        else:
            txt = args.claim_scan
        res = scan_claim_text(txt)
        res["unwired"] = scan_unwired_claims(txt)
        # 两类任一违规即整体 BLOCKED
        if res["unwired"]["violations"]:
            res["status"] = "BLOCKED"
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "OK" else 1

    if args.unwired_scan is not None:
        res = scan_unwired_claims(args.unwired_scan)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "OK" else 1

    if args.ghost_scan is not None:
        # 幽灵引用扫描：只读，不改被扫文件
        target = Path(args.ghost_scan).expanduser()
        if target.is_file():
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                print(json.dumps({"status": "BLOCKED", "reason": f"无法读取：{e}"}, ensure_ascii=False))
                return 1
            res = scan_ghost_references(text, base_dir=str(target.parent))
            res["target"] = str(target)
        else:
            res = scan_ghost_references(args.ghost_scan, base_dir=str(Path.cwd()))
            res["target"] = "(inline text)"
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 1 if res["status"] == "BLOCKED" else 0

    if args.defect_rate is not None:
        # 最大短板非线性惩罚：只读计算，不写盘
        spec, bad = {}, []
        for part in (args.defect_rate or "").split(","):
            if not part.strip():
                continue
            if "=" not in part:
                bad.append(part.strip())
                continue
            k, v = part.split("=", 1)
            try:
                spec[k.strip()] = float(v)
            except ValueError:
                bad.append(part.strip())
        if bad:
            print(json.dumps({"status": "REJECTED", "reason": "无法解析的缺陷项",
                              "bad": bad, "expected": "名字=值,名字=值"}, ensure_ascii=False))
            return 1
        print(json.dumps({
            "defect_rate": defect_rate(spec, args.defect_total),
            "worst": max(spec.values()) if spec else 0.0,
            "reported": len(spec),
            "metrics_total": args.defect_total if args.defect_total is not None else len(spec),
            "boost_coeff": DEFECT_BOOST_COEFF,
            "note": "分母为受监测维度总数；传 --defect-total 12 可复现上游 EVM 固定 12 维口径",
            "boundary": "只计算缺陷率，不触发任何动作",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.risk_classify is not None:
        # D03 风险分级：只读判定，不写盘、不改权限
        print(json.dumps(classify_risk_tier(args.risk_classify), ensure_ascii=False, indent=2))
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
    if args.gate is not None:
        if args.gate:
            paths, diff_text = args.gate, ""
            if not args.gate_backup:
                print(json.dumps({"status": "BLOCKED", "reason": "显式传路径时需 --gate-backup 指定备份目录（L1 不可跳过）"}, ensure_ascii=False))
                sys.exit(1)
        else:
            paths, diff_text = gate_paths_from_git()
        result = apply_gate(paths, diff_text, backup_dir=args.gate_backup,
                            mode=args.gate_mode,
                            added_paths=git_added_paths())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["allow_apply"] else 1)

    if args.list_levels:
        for k, v in EVIDENCE_LEVELS.items():
            print(f"{k} {v['name']:<12} 需要={v['needs']:<7} 可证={v['proves']}")
        return 0
    if args.evidence:
        if not args.level:
            print(json.dumps({"status": "BLOCKED", "reason": "需 --level E0-E9"}, ensure_ascii=False))
            return 2
        r = evidence_record(args.evidence, args.level, args.artifact,
                            args.verify_cmd, args.execute, args.note, args.claim_text)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["status"] == "OK" else 1
    if args.evidence_status is not None:
        print(json.dumps(evidence_status(args.evidence_status or None, args.execute),
                         ensure_ascii=False, indent=2))
        return 0
    if args.prune_genes is not None:
        try:
            thr = float(args.prune_genes)
        except ValueError:
            print(json.dumps({"status": "BLOCKED", "reason": f"无效阈值: {args.prune_genes}"}, ensure_ascii=False))
            return 2
        print(json.dumps(prune_genes(threshold=thr, dry_run=args.dry_run), ensure_ascii=False, indent=2))
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
    if args.shortfall:
        print(json.dumps(shortfall_report(), ensure_ascii=False, indent=2))
        return 0

    if args.gene_l5:
        rep = gene_l5_report()
        print(json.dumps({k: v for k, v in rep.items() if k != "rows"}, ensure_ascii=False, indent=2))
        return 0

    if args.gene_l5_backfill:
        r = gene_l5_backfill(dry_run=not args.apply)
        print(json.dumps(r, ensure_ascii=False, indent=2))
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
