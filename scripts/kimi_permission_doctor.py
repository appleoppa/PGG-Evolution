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
KIMI_BIN = "/Applications/KimiCU.app/Contents/MacOS/kimi-cu"


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


def kimi_capture_state() -> bool | None:
    """**真判据**：直接调 kimi-cu 截图，判断 KimiCU.app（ai.kimi.cu）是否有权限。

    2026-09-20 实测修正：旧版用系统 screencapture 作对照——那是**测错主体**。
    TCC 日志证明两个授权主体完全不同：
        ai.kimi.cu (KimiCU.app)  authValue=2 = 允许
        node        (pi-web)     authValue=0 = 拒绝
    screencapture 探的是宿主 node 的权限，与 kimi-cu 无关；用它判定会在
    KimiCU 已授权时仍误报「无权限」，并给出错的授权指引（让人去授权宿主）。

    返回 True=可截 / False=被拦 / None=无法判定（不得当可用）。
    """
    candidates = ["com.apple.TextEdit", "com.apple.Terminal", "com.google.Chrome",
                  "com.apple.Safari", "com.bytedance.macos.feishu", "com.apple.finder"]
    try:
        r = _run([KIMI_BIN, "mcp", "-s", "user"])
    except OSError:
        return None
    # 先问 kimi-cu 当前有哪些 app，再逐个试截图
    try:
        p = subprocess.run(
            [KIMI_BIN, "mcp", "-s", "user"],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "list_apps", "arguments": {}}}) + "\n",
            capture_output=True, text=True, timeout=30)
        running = [a.get("bundle_id") for a in
                   json.loads(json.loads(p.stdout.strip().splitlines()[0])
                              ["result"]["content"][0]["text"])["apps"]]
    except Exception:
        running = []
    ordered = [c for c in candidates if c in running] + [b for b in running if b not in candidates]
    ax_ok = False
    for bid in ordered:
        for mode in ("image", "ax"):
            try:
                p = subprocess.run(
                    [KIMI_BIN, "mcp", "-s", "user"],
                    input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "get_app_state",
                                                 "arguments": {"app": bid, "mode": mode}}}) + "\n",
                    capture_output=True, text=True, timeout=60)
                blocks = json.loads(p.stdout.strip().splitlines()[0])["result"]["content"]
            except Exception:
                continue
            if mode == "image" and any(b.get("type") == "image" and b.get("data") for b in blocks):
                return True
            if mode == "ax" and any(b.get("type") == "text" and b.get("text") for b in blocks):
                ax_ok = True
    # 没有 app 能出图：AX 能读 ⇒ 更可能是权限被拦；全无内容 ⇒ 无法判定
    return False if ax_ok else None


def host_screen_capture_state() -> bool:
    """宿主进程（node/pi-web）自身的屏幕录制权限——**仅供参考，与 kimi-cu 无关**。

    保留此函数仅用于诊断「为什么本脚本的 screencapture 也不行」，
    不得用它代替 kimi_capture_state() 判定 kimi-cu 权限。

    ⚠️ 2026-09-20 实测教训：此函数的返回值是**唯一真判据**。
    面板开关显示为「开」不等于授权已生效——当时 node 开关已从 0 变 1，
    但系统弹出了指纹/密码认证框（AXSheet「正在尝试修改你的系统设置」），
    实际改动尚未提交，screencapture 仍返回 'could not create image from display'。
    只看开关字段会误报「授权成功」——必须实跑本函数才算数。

    ⚠️ 另一个实测陷阱：探针文件名**不能以点开头**。screencapture 会拒写隐藏文件
    （stderr: 'cannot write file to intended destination'）**但返回码仍为 0**，
    仅看 returncode 会误判为「无权限」。本函数同时校验**文件存在且非空**。
    """
    probe = Path("/tmp/kimi_perm_doctor_probe.png")  # 不得用隐藏文件名
    try:
        r = _run(["screencapture", "-x", str(probe)])
        ok = r.returncode == 0 and probe.exists() and probe.stat().st_size > 0
        if probe.exists():
            probe.unlink()
        return ok
    except OSError:
        return False


def pending_auth_prompt() -> bool:
    """检测系统设置是否弹出了「正在尝试修改你的系统设置」等待认证的弹框。

    2026-09-20 实测：此弹框存在时，面板开关可能已显示为「开」，
    但 TCC 授权**尚未落库**，screencapture 仍会失败。区分「已授权」
    与「开关动了但等认证」的关键信号。
    """
    script = (
        'tell application "System Events" to tell process "系统设置"\n'
        '  set out to ""\n'
        '  repeat with sh in (every sheet of window 1)\n'
        '    repeat with st in (every static text of sh)\n'
        '      set out to out & (value of st as string) & "\\n"\n'
        '    end repeat\n'
        '  end repeat\n'
        '  return out\n'
        'end tell'
    )
    r = _run(["osascript", "-e", script], timeout=20)
    return "正在尝试修改" in (r.stdout or "")


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
    # 2026-09-20 修正：权限主体是 ai.kimi.cu（KimiCU.app），不是本脚本宿主。
    # 真判据 = 直接调 kimi-cu 截图；宿主 screencapture 降为参考。
    cap = kimi_capture_state()
    host_cap = host_screen_capture_state()

    print(f"\n① 服务 {SERVICE_LABEL}")
    if svc["loaded"]:
        print(f"   ✅ 已加载" + (f"（pid {svc['pid']}）" if svc["pid"] else "（未运行，RunAtLoad=false）"))
    else:
        print("   ❌ 未加载 —— 这是 MCP 调用报 'service unavailable' 的根因")
        print("\n   → 修复（一条命令）：")
        print(f"     launchctl bootstrap gui/$(id -u) {USER_PLIST}")
        print("\n   说明：该服务是 MachServices(XPC)。MCP 进程在但服务未加载时，")
        print("         一切调用都会返回 'perform failed after retries'。")

    print(f"\n② KimiCU 屏幕录制权限（ai.kimi.cu，真判据）")
    if not svc["loaded"]:
        # 服务未加载时，kimi_capture_state() 拿不到真实结论（调用会 service unavailable）。
        # 旧版直接报「✅ 可用」会误导（与 ①❌ 矛盾）；改报「前置未满足，无法判定」。
        print("   ⏸ 未判定 —— 服务未加载，kimi-cu 调用必失败，权限无法真实生效")
        print("   → 先执行上方 ① 的修复命令，再重跑本工具。")
    elif cap is True:
        print("   ✅ 可用 —— 坐标校正探针可以实测")
    elif cap is None:
        print("   ? 无法判定（服务已加载但当前无任何 app 可截图）")
        print("   → 先开一个有窗口的 app，再重跑本工具。")
    else:
        print("   ❌ 不可用 —— KimiCU.app 未获屏幕录制权限")
        print("\n   → 需人工授权（我无法自行授予）：")
        print("     系统设置 → 隐私与安全性 → 屏幕录制")
        print("     添加并勾选 **KimiCU**（bundle id `ai.kimi.cu`）")
        print("     ⚠️ 不是本脚本的宿主进程（那是 node/pi-web，与 kimi-cu 无关）。")
        print("\n   ⚠️ 授权后必须重启 kimi-cu 服务才生效（TCC 在进程启动时读取）：")
        print(f"     launchctl kickstart -k gui/$(id -u)/{SERVICE_LABEL}")
        print("     pkill -f 'kimi-cu mcp'   # 杀掉旧 MCP 进程（否则仍缓存旧权限）")

    print(f"\n③ 宿主屏幕录制权限（真判据 = 实跑；但相对 kimi-cu 仅参考）")
    print(f"   {'✅ 可用' if host_cap else '❌ 不可用'}"
          "  ← 这是 node/pi-web 的权限，与 kimi-cu 无关，不得代替第②项")
    if not host_cap:
        # 2026-09-20 实测：开关显示「开」≠ 授权已生效。面板开关动过后系统会弹
        # 指纹/密码认证框，此时开关已变 1 但授权未落库，screencapture 仍失败。
        if pending_auth_prompt():
            print("   ⚠️ 检测到系统设置弹出了**等待认证**的弹框")
            print("      → 面板开关可能已显示为「开」，但授权**尚未提交**")
            print("      → 请在该弹框按指纹（触控 ID）或点「使用密码…」完成认证")
            print("      → 认证后重启宿主进程，TCC 在进程启动时读取权限")
        else:
            print("   注：宿主链中承载本脚本的进程若也无权限，只影响本脚本自己的 screencapture，")
            print("       不影响 kimi-cu（两者是不同的 TCC 主体）。")
        print("   → 授权入口：系统设置 → 隐私与安全性 → 屏幕录制 → 找到 **node**")
        print("      （TCC 主体是 node 二进制本身，不是 pi-web 这个名称）")

    print("\n" + "=" * 68)
    # 判定顺序修正（2026-09-20 实测）：服务是截图的前提——服务停止后 kimi-cu
    # 调用直接报 'service unavailable: perform failed after retries'。
    # 旧版把 cap 判定放在最前，导致「服务未加载但权限可测」时先报「权限就绪」
    # 并 return 0，与上文 ①❌ 自相矛盾（CI 模拟实测复现）。
    if not svc["loaded"]:
        print("结论：服务未加载 —— 这是前置阻塞，先加载服务再谈权限")
        print("       （服务未加载时 kimi-cu 调用必失败，权限状态无法真实生效）")
        return 2
    if cap is True:
        print("结论：权限就绪 → 可跑 python3 scripts/kimi_coord_probe.py 实测坐标校正")
        return 0
    if cap is None:
        print("结论：无法判定（服务已加载但无 app 可截图，请先开一个有窗口的 app）")
        return 2
    print("结论：服务已就绪；KimiCU.app 缺屏幕录制权限（需人工授权 + 重启服务）")
    return 3


if __name__ == "__main__":
    sys.exit(main())
