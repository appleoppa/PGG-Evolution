#!/usr/bin/env python3
"""kimi-cu LaunchAgent 服务修复（**写动作**，与只读诊断工具分离）。

为什么需要这个：`kimi-cu upgrade` 会移除用户级 plist，并从 app 内恢复一份
使用 `BundleProgram` 相对路径的版本。用户级 LaunchAgent 需要**绝对路径**，
否则 `launchctl bootstrap` 报 `Bootstrap failed: 5: Input/output error`。

2026-09-20 对照实验（本机实测）：
    相对路径 plist → bootstrap exit=5（I/O error）
    绝对路径 plist → bootstrap OK → 截图恢复
服务停止时 kimi-cu 调用直接报 `service unavailable: perform failed after retries`，
故服务是截图能力的前置依赖。

用法：
    python3 scripts/kimi_cu_service_repair.py            # dry-run，只报要做什么
    python3 scripts/kimi_cu_service_repair.py --apply    # 真正执行修复

退出码：
    0 = 服务已就绪（无需修复）
    1 = 需要修复但未加 --apply（dry-run）
    2 = 修复失败（附具体原因）
    3 = 环境不可用（app 不存在等）
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

APP_BIN = "/Applications/KimiCU.app/Contents/MacOS/kimi-cu"
APP_PLIST = "/Applications/KimiCU.app/Contents/Library/LaunchAgents/ai.kimi.cu.service.plist"
SERVICE_LABEL = "ai.kimi.cu.service"
USER_PLIST = Path.home() / "Library/LaunchAgents" / "ai.kimi.cu.service.plist"

# 绝对路径版模板。RunAtLoad 保持 false 与上游一致（不改变 kimi-cu 自身行为）。
PLIST_TEMPLATE = {
    "Label": SERVICE_LABEL,
    "ProgramArguments": [APP_BIN, "service"],
    "MachServices": {SERVICE_LABEL: True},
    "AssociatedBundleIdentifiers": ["ai.kimi.cu"],
    "RunAtLoad": False,
}


def _run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def service_state() -> dict:
    out = {"loaded": False, "pid": None}
    r = _run(["launchctl", "list"])
    for ln in r.stdout.splitlines():
        if SERVICE_LABEL in ln:
            parts = ln.split()
            out["loaded"] = True
            out["pid"] = None if parts[0] == "-" else parts[0]
    return out


def plist_diagnosis() -> dict:
    """判断当前用户 plist 的问题类型。

    返回 kind:
      ok        —— 存在且为绝对路径
      missing   —— 不存在（升级后被移除）
      relative  —— 存在但用 BundleProgram/相对路径（bootstrap 必失败）
      broken    —— 存在但解析失败
    """
    if not USER_PLIST.exists():
        return {"kind": "missing", "detail": "用户 plist 不存在（升级后常见）"}
    try:
        data = plistlib.loads(USER_PLIST.read_bytes())
    except Exception as exc:  # plistlib 异常类型多样，统一兜底
        return {"kind": "broken", "detail": f"plist 解析失败: {type(exc).__name__}"}

    args = data.get("ProgramArguments") or []
    first = args[0] if args else ""
    has_bundle_program = "BundleProgram" in data
    is_absolute = first.startswith("/")

    if has_bundle_program or not is_absolute:
        return {
            "kind": "relative",
            "detail": (f"使用相对路径（BundleProgram={data.get('BundleProgram')!r}, "
                       f"argv0={first!r}）→ bootstrap 必报 I/O error"),
        }
    return {"kind": "ok", "detail": f"绝对路径 {first}"}


def write_user_plist() -> bool:
    """写入绝对路径版用户 plist。"""
    USER_PLIST.parent.mkdir(parents=True, exist_ok=True)
    USER_PLIST.write_bytes(plistlib.dumps(PLIST_TEMPLATE))
    return USER_PLIST.exists()


def bootstrap() -> tuple[bool, str]:
    """加载服务。先 bootout 清残留，再 bootstrap。"""
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"])
    r = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(USER_PLIST)])
    if r.returncode != 0:
        return False, (r.stderr.strip() or r.stdout.strip() or f"exit={r.returncode}")
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行修复（默认 dry-run）")
    args = ap.parse_args()

    print("=" * 68)
    print("kimi-cu LaunchAgent 服务修复" + ("" if args.apply else "（dry-run）"))
    print("=" * 68)

    if not Path(APP_BIN).exists():
        print(f"\n✗ KimiCU 可执行文件不存在: {APP_BIN}")
        print("  → 请先安装 KimiCU，再重跑本工具。")
        return 3

    svc = service_state()
    plist = plist_diagnosis()

    print(f"\n① 服务 {SERVICE_LABEL}: "
          + (f"✅ 已加载（pid {svc['pid']}）" if svc["loaded"] else "❌ 未加载"))
    print(f"② 用户 plist: {plist['kind']} —— {plist['detail']}")

    need_repair = (not svc["loaded"]) or plist["kind"] != "ok"
    if not need_repair:
        print("\n结论：服务与 plist 均就绪，无需修复。")
        return 0

    print("\n③ 待执行动作：")
    if plist["kind"] != "ok":
        print(f"   重建用户 plist（绝对路径）: {USER_PLIST}")
    if not svc["loaded"]:
        print(f"   bootout + bootstrap gui/{os.getuid()} {USER_PLIST}")

    if not args.apply:
        print("\n[dry-run] 未改动任何系统状态。加 --apply 执行。")
        return 1

    # 备份现有 plist（可回滚）
    if USER_PLIST.exists():
        import time
        bak = USER_PLIST.with_suffix(f".plist.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        bak.write_bytes(USER_PLIST.read_bytes())
        print(f"\n   已备份原 plist: {bak}")

    if plist["kind"] != "ok":
        if not write_user_plist():
            print("\n✗ 写入 plist 失败")
            return 2
        print("   ✅ 已写入绝对路径版 plist")

    ok, detail = bootstrap()
    if not ok:
        print(f"\n✗ bootstrap 失败: {detail}")
        print("  → 常见原因：plist 仍是相对路径、或 Label 冲突（先 bootout 再试）。")
        return 2
    print("   ✅ bootstrap 成功")

    after = service_state()
    if not after["loaded"]:
        print("\n✗ 修复后服务仍未加载（不得报成功）")
        return 2

    # 服务已注册；RunAtLoad=false 时 pid 为空属正常（首次调用才会拉起）
    pid_txt = f"pid {after['pid']}" if after["pid"] else "已注册，按需拉起（RunAtLoad=false）"
    print(f"\n结论：修复完成，服务已加载（{pid_txt}）。")
    print("  下一步：python3 scripts/kimi_permission_doctor.py 复核权限状态")
    return 0


if __name__ == "__main__":
    sys.exit(main())
