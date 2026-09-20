#!/usr/bin/env python3
"""kimi-cu 屏幕录制权限诊断与修复指引（只读，不改系统设置）。

为什么需要这个：kimi-cu 的坐标校正实测被 macOS TCC 权限挡住，
但「服务没加载」与「没屏幕录制权限」症状不同、修法不同，容易互相误判。
本工具把两者分开报，并给出精确到宿主进程的授权指引。

用法：
    python3 scripts/kimi_permission_doctor.py

退出码：
    0 = 权限就绪，可实测
    2 = 服务未加载（可自动修，命令已给出）
    3 = 无屏幕录制权限（需人工在系统设置授权）
    4 = 其他
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SERVICE_LABEL = "ai.kimi.cu.service"
USER_PLIST = "~/Library/LaunchAgents/ai.kimi.cu.service.plist"


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def service_state() -> dict:
    """检查 kimi-cu LaunchAgent 是否加载。"""
    out = {"loaded": False, "pid": None}
    r = _run(["launchctl", "list"])
    for ln in r.stdout.splitlines():
        if SERVICE_LABEL in ln:
            parts = ln.split()
            out["loaded"] = True
            out["pid"] = None if parts[0] == "-" else parts[0]
    return out


def screen_capture_state() -> bool:
    """用系统 screencapture 作对照，判断宿主进程是否有屏幕录制权限。

    关键：用系统自带工具而非 kimi-cu，才能区分「权限问题」与「kimi-cu 问题」。
    """
    probe = Path("/tmp/.kimi_perm_doctor_probe.png")
    try:
        r = _run(["screencapture", "-x", str(probe)])
        ok = r.returncode == 0 and probe.exists() and probe.stat().st_size > 0
        if probe.exists():
            probe.unlink()
        return ok
    except OSError:
        return False


def host_chain() -> list[dict]:
    """回溯当前进程的宿主链（用于告诉用户该给哪个 app 授权）。"""
    chain: list[dict] = []
    pid = os.getpid()
    for _ in range(8):
        r = _run(["ps", "-o", "pid=,ppid=,comm=", "-p", str(pid)])
        line = r.stdout.strip()
        if not line:
            break
        parts = line.split(None, 2)
        if len(parts) < 3:
            break
        chain.append({"pid": parts[0], "ppid": parts[1], "comm": parts[2]})
        if parts[1] in ("0", "1"):
            break
        pid = int(parts[1])
    return chain


def launch_agent_for(pid: str) -> str | None:
    """找 pid 对应的 LaunchAgent label（若有）。"""
    r = _run(["launchctl", "list"])
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[0] == pid:
            return parts[2]
    return None


def main() -> int:
    print("=" * 68)
    print("kimi-cu 权限诊断（只读，不改系统设置）")
    print("=" * 68)

    svc = service_state()
    cap = screen_capture_state()

    print(f"\n① 服务 {SERVICE_LABEL}")
    if svc["loaded"]:
        print(f"   ✅ 已加载" + (f"（pid {svc['pid']}）" if svc["pid"] else "（未运行，RunAtLoad=false）"))
    else:
        print("   ❌ 未加载 —— 这是 MCP 调用报 'service unavailable' 的根因")
        print("\n   → 修复（一条命令）：")
        print(f"     launchctl bootstrap gui/$(id -u) {USER_PLIST}")
        print("\n   说明：该服务是 MachServices(XPC)。MCP 进程在但服务未加载时，")
        print("         一切调用都会返回 'perform failed after retries'。")

    print(f"\n② 宿主屏幕录制权限")
    if cap:
        print("   ✅ 可用 —— 坐标校正探针可以实测")
    else:
        print("   ❌ 不可用（系统 screencapture 同样失败 → 是 TCC 权限，不是代码 bug）")
        chain = host_chain()
        print("\n   当前进程宿主链：")
        for i, c in enumerate(chain):
            label = launch_agent_for(c["pid"]) if i > 0 else None
            tag = f"  ← LaunchAgent: {label}" if label else ""
            print(f"     {'└─' * min(i, 3)} {c['comm']} (pid {c['pid']}){tag}")
        print("\n   → 需人工授权（我无法自行授予）：")
        print("     系统设置 → 隐私与安全性 → 屏幕录制")
        print("     添加并勾选上面链中**承载本进程的那个宿主**（通常是 LaunchAgent 的")
        print("     Program 路径，例如 node 或 app 可执行文件）。")
        print("\n   ⚠️ 授权后必须重启该宿主进程才生效（TCC 在进程启动时读取）：")
        for c in chain:
            label = launch_agent_for(c["pid"])
            if label:
                print(f"     launchctl kickstart -k gui/$(id -u)/{label}")
                break

    print("\n" + "=" * 68)
    if cap:
        print("结论：权限就绪 → 可跑 python3 scripts/kimi_coord_probe.py 实测坐标校正")
        return 0
    if not svc["loaded"]:
        print("结论：先加载服务，再处理屏幕录制权限")
        return 2
    print("结论：服务已就绪；仅缺屏幕录制权限（需人工授权 + 重启宿主）")
    return 3


if __name__ == "__main__":
    sys.exit(main())
