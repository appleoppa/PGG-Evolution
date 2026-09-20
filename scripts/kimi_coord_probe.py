#!/usr/bin/env python3
"""kimi-cu 坐标校正实测探针（对应 Apex 资源盘《超级进化6》坐标校正公式）。

背景与诚实边界：
- 资源盘《超级进化6》指出"截图缩放导致物理点击坐标偏移"，给出校正公式：
      X_real = X_out · W_screen / W_img
      Y_real = Y_out · H_screen / H_img
- 实测：kimi-cu 二进制**已内置**该校正（字符串含 backingScaleFactor，
  工具描述明写 "tool rescales to the real window"）。
  所以本探针的目的不是"修 bug"，而是**验证它是否真准**（防"看起来实现了"）。
- 截至 2026-09-20，kimi-cu 服务返回 "service unavailable: perform failed after
  retries"，无法完成实测。本脚本供服务恢复后直接跑，产出真数据。

用法：
    python3 tools/kimi_coord_probe.py            # 完整实测
    python3 tools/kimi_coord_probe.py --dry-run  # 只打印设计，不调服务
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import subprocess
import sys

BIN = "/Applications/KimiCU.app/Contents/MacOS/kimi-cu"
KIMI_BIN = BIN  # 别名（diagnose_service 用）


def mcp(name: str, args: dict, timeout: int = 30) -> dict:
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": args}}
    p = subprocess.run([BIN, "mcp", "-s", "user"], input=json.dumps(req) + "\n",
                       capture_output=True, text=True, timeout=timeout)
    for ln in p.stdout.strip().splitlines():
        try:
            d = json.loads(ln)
            if d.get("id") == 1:
                return d
        except json.JSONDecodeError:
            continue
    return {"_raw": p.stdout[:400], "_err": p.stderr[:300]}


def _bundle_for_window(win_name: str) -> str | None:
    """按窗口进程名从 kimi-cu list_apps 里匹配 bundle_id。

    修复：此前未指定 --bundle 时用 pid=0，必返回空图。
    注意：窗口进程名（System Events）与 app 显示名可能不同语言
    （如窗口名 "Terminal" vs app 名 "终端"），故先试本地化名映射。
    """
    try:
        r = mcp("list_apps", {})
        apps = json.loads(r["result"]["content"][0]["text"])["apps"]
    except Exception:
        return None

    # ① 精确名
    for a in apps:
        if a.get("name") == win_name:
            return a.get("bundle_id")
    # ② 模糊名
    for a in apps:
        n = a.get("name", "")
        if n and (n in win_name or win_name in n):
            return a.get("bundle_id")
    # ③ 进程名 → bundle id 映射（覆盖中英文差异：System Events 报英文，
    #    kimi-cu 报本地化名，如 "TextEdit" vs "文本编辑"）
    pname_map = {
        "terminal": "com.apple.Terminal", "finder": "com.apple.finder",
        "safari": "com.apple.Safari", "chrome": "com.google.Chrome",
        "wechat": "com.tencent.xinWeChat", "feishu": "com.bytedance.macos.feishu",
        "textedit": "com.apple.TextEdit",
        "onedrive": "com.coderforart.One-Markdown",
    }
    low = win_name.lower().replace(" ", "")
    for k, bid in pname_map.items():
        if k in low:
            for a in apps:
                if a.get("bundle_id") == bid:
                    return bid
    return None


def window_geometry(app_name: str | None = None) -> dict | None:
    """取窗口真实几何（逻辑点）。

    不用 `tell process "<name>"`：重名进程（如多个 "Web App"）会取不到，
    改为遍历可见进程找到第一个有窗口的（可按 app_name 过滤）。
    """
    filt = f'if (name of p) is "{app_name}" then' if app_name else ""
    script = f'''
    tell application "System Events"
      repeat with p in (every process whose visible is true)
        try
          {filt}
          if (count of windows of p) > 0 then
            set w to first window of p
            set pn to name of p
            set p to position of w
            set s to size of w
            return pn & "|" & (item 1 of p) & "," & (item 2 of p) & "," & (item 1 of s) & "," & (item 2 of s)
          end if
          {("end if") if app_name else ""}
        end try
      end repeat
      return ""
    end tell
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    out = r.stdout.strip()
    if not out or "|" not in out:
        return None
    name, geom = out.split("|", 1)
    try:
        x, y, w, h = (int(v) for v in geom.split(","))
        return {"name": name, "x": x, "y": y, "w": w, "h": h,
                "center_logical": (x + w // 2, y + h // 2)}
    except ValueError:
        return None


def png_size(path: str) -> tuple[int, int] | None:
    """取图像像素尺寸，支持 PNG 与 JPEG。

    修复：kimi-cu 实测返回 **JPEG**（`file` 报 "JPEG image data ... 1324x768"），
    而旧版只认 PNG magic，导致拿到图后仍报「非合法 PNG」而中止。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] == b"\xff\xd8":  # JPEG：扫 SOF 段拿尺寸
                fh.seek(2)
                while True:
                    b = fh.read(1)
                    if not b:
                        return None
                    if b != b"\xff":
                        continue
                    marker = fh.read(1)
                    while marker == b"\xff":
                        marker = fh.read(1)
                    if marker in (b"\xd8", b"\xd9") or not marker:
                        continue
                    ln = fh.read(2)
                    if len(ln) < 2:
                        return None
                    seglen = struct.unpack(">H", ln)[0]
                    # SOF0-SOF15（排除 DHT/DAC/RST）
                    if 0xC0 <= marker[0] <= 0xCF and marker[0] not in (0xC4, 0xC8, 0xCC):
                        data = fh.read(5)
                        if len(data) < 5:
                            return None
                        h, w = struct.unpack(">HH", data[1:5])
                        return (w, h)
                    fh.seek(seglen - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def diagnose_service() -> dict:
    """诊断 kimi-cu 服务与权限状态。

    2026-09-20 实测根因（两层）：
    ① MCP 进程在，但 LaunchAgent `ai.kimi.cu.service` 未加载
       → 调用返回 "service unavailable: perform failed after retries"。
       修复：launchctl bootstrap gui/<uid> ~/Library/LaunchAgents/ai.kimi.cu.service.plist
    ② **权限主体是 ai.kimi.cu（KimiCU.app），不是本脚本的宿主进程**。
       实测 TCC 日志：
         ai.kimi.cu  authValue=2（允许）
         node        authValue=0（拒绝）
       故**不能用 screencapture 判断 kimi-cu 权限**——那是两个不同主体。
       早期版本拿 screencapture 当对照，导致权限已授予时仍误报不可用。

    现改为直接调 kimi-cu 截图，用「AX 能读但图空」区分权限问题与无窗口。
    """
    out = {"service_loaded": False, "service_pid": None,
           "kimi_screenshot_ok": None, "host_screen_capture_ok": None}
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            if "ai.kimi.cu.service" in ln:
                parts = ln.split()
                out["service_loaded"] = True
                out["service_pid"] = None if parts[0] == "-" else parts[0]
    except (OSError, subprocess.SubprocessError):
        pass

    # 真权限判据：直接调 kimi-cu 截图，并**逐个 app 试**直到有一个出图。
    # 关键区分：
    #   · 有 app 能出图            → 截图权限 OK（真判据 True）
    #   · 所有 app 都出不了图       → 权限被拦（False）
    #   · 服务未加载 / 无法判定     → None（不得当可用）
    # 2026-09-20 修正：旧版用 screencapture 判权限，那是 node 的主体，与 kimi-cu 无关。
    out["probe_app"] = None
    if out["service_loaded"]:
        try:
            probe_app = _pick_probe_app()
            if probe_app:
                out["kimi_screenshot_ok"] = True
                out["probe_app"] = probe_app
            else:
                # 没有任何 app 能出图：再区分「无权限」与「全部无窗口」
                # AX 能读 = 进程通信正常；此时截图空 ⇒ 更可能是权限
                ax_ok = any(_kimi_call(b, "ax") for b in
                            ["com.apple.TextEdit", "com.apple.finder", "com.google.Chrome"])
                out["kimi_screenshot_ok"] = False if ax_ok else None
        except Exception:
            out["kimi_screenshot_ok"] = None

    # 仅作参考：宿主进程自身的截图权限（**与 kimi-cu 无关**，不得用来代替上面）
    try:
        probe = "/tmp/kimi_coord_screencapture_probe.png"
        r = subprocess.run(["screencapture", "-x", probe], capture_output=True, text=True, timeout=15)
        import os as _os
        ok = r.returncode == 0 and _os.path.exists(probe)
        out["host_screen_capture_ok"] = ok
        if _os.path.exists(probe):
            _os.remove(probe)
    except (OSError, subprocess.SubprocessError):
        out["host_screen_capture_ok"] = False
    return out


def _pick_probe_app() -> str | None:
    """选一个当前**真能截到图**的 app 做探针。

    2026-09-20 实测：kimi-cu 对「无窗口」app 返回 `isError: true / no target app`
    或空 content——这与「无权限」症状不同，但都表现为「无图」。
    若只用固定 app 探测，会把「那个 app 没窗口」误判成「无权限」。
    故改为逐个试，返回第一个能出图的 app。
    """
    candidates = ["com.apple.TextEdit", "com.apple.Terminal", "com.google.Chrome",
                  "com.apple.Safari", "com.bytedance.macos.feishu", "com.apple.finder"]
    try:
        r = mcp("list_apps", {})
        running = [a.get("bundle_id") for a in
                   json.loads(r["result"]["content"][0]["text"])["apps"]]
    except Exception:
        running = []
    # 先试候选里正在运行的，再试其他正在运行的
    ordered = [c for c in candidates if c in running] + [b for b in running if b not in candidates]
    for bid in ordered:
        if _kimi_call(bid, "image"):
            return bid
    return None


def _kimi_call(app: str, mode: str) -> bool:
    """调 kimi-cu get_app_state，返回是否有实质内容。"""
    try:
        r = subprocess.run([KIMI_BIN, "mcp", "-s", "user"],
                           input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                             "params": {"name": "get_app_state",
                                                        "arguments": {"app": app, "mode": mode}}}) + "\n",
                           capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout.strip().splitlines()[0])
        blocks = d.get("result", {}).get("content", [])
        if mode == "image":
            return any(b.get("type") == "image" and b.get("data") for b in blocks)
        return any(b.get("type") == "text" and b.get("text") for b in blocks)
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=None, help="目标应用进程名（默认取第一个有窗口的可见进程）")
    ap.add_argument("--bundle", default=None, help="bundle id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--shot", default="/tmp/kimi_coord_probe.png")
    args = ap.parse_args()

    print("=" * 66)
    print("kimi-cu 坐标校正实测探针")
    print("=" * 66)

    # 先诊断服务与权限——避免把环境问题误报成代码 bug
    diag = diagnose_service()
    svc = "✅ 已加载" + (f" (pid {diag['service_pid']})" if diag["service_pid"] else " (未运行)") \
        if diag["service_loaded"] else "❌ 未加载"
    print(f"服务 ai.kimi.cu.service: {svc}")
    # 关键：权限主体是 ai.kimi.cu（KimiCU.app），不是本脚本的宿主进程。
    # 故以「kimi-cu 自己能不能截图」为真判据；宿主 screencapture 仅作参考。
    kimi = {True: "✅ 可用", False: "❌ 不可用（截图被拦）",
            None: "? 无法判定（探针 app 无窗口）"}[diag["kimi_screenshot_ok"]]
    host = {True: "✅ 可用", False: "❌ 不可用", None: "? 未测"}[diag["host_screen_capture_ok"]]
    print(f"kimi-cu 截图权限（ai.kimi.cu，真判据）: {kimi}"
          + (f"  探针 app={diag.get('probe_app')}" if diag.get("probe_app") else ""))
    print(f"本脚本宿主截图权限（node，仅参考）: {host}  ← 与 kimi-cu 无关，不得代替上行")
    if not diag["service_loaded"]:
        print("\n→ 修复：launchctl bootstrap gui/$(id -u) "
              "~/Library/LaunchAgents/ai.kimi.cu.service.plist")
        print("  该服务是 MachServices(XPC)，MCP 进程在但服务未加载时调用必失败。")
    if diag["kimi_screenshot_ok"] is False:
        print("\n→ KimiCU.app 未获屏幕录制权限（AX 能读、截图空 = 被拦）。")
        print("  这是 macOS TCC 权限，不是 kimi-cu 或本脚本的 bug。")
        print("  需人工在「系统设置 → 隐私与安全性 → 屏幕录制」授权 **KimiCU**")
        print("  （bundle id `ai.kimi.cu`，不是本脚本的宿主进程）。")
        print("  → 实测中止，不得报 PASS。")
        return 4

    geo = window_geometry(args.app)
    if not geo:
        print(f"✗ 取不到窗口几何（目标 {args.app or '任意可见进程'}）")
        print("  → 实测中止，不产出结论（不得凭猜测报 PASS）")
        return 2

    print(f"目标进程: {geo['name']}")
    print(f"窗口真实几何(逻辑点): 位置({geo['x']},{geo['y']}) 尺寸({geo['w']}x{geo['h']})")
    print(f"窗口中心逻辑坐标: {geo['center_logical']}")

    if args.dry_run:
        print("\n[dry-run] 将执行：get_app_state(image) → 比对截图尺寸 → 算缩放比")
        return 0

    # 自动解析目标 bundle id：优先 --bundle，否则按窗口名从 kimi-cu list_apps 里匹配。
    # 修复：此前未指定 --bundle 时硬编码 pid=0（无效），必返回空图。
    bundle = args.bundle or _bundle_for_window(geo["name"])
    if bundle:
        print(f"目标 bundle: {bundle}")
    r = mcp("get_app_state", {"app": bundle, "mode": "image"}) if bundle else \
        mcp("get_app_state", {"app": geo["name"], "mode": "image"})
    if r.get("result", {}).get("isError"):
        msg = r["result"]["content"][0].get("text", "")
        print(f"\n✗ kimi-cu 不可用: {msg}")
        print("  → 实测中止。**不得**因此声称坐标校正已验证或已修复。")
        print("  → 服务恢复后重跑本脚本即可产出真数据。")
        return 3

    saved = False
    for item in r.get("result", {}).get("content", []):
        if item.get("type") == "image":
            open(args.shot, "wb").write(base64.b64decode(item["data"]))
            saved = True
    if not saved:
        print("✗ 未返回图像内容（服务在但无图像——通常是屏幕录制权限）")
        return 3

    size = png_size(args.shot)
    if not size:
        print("✗ 截图尺寸解析失败（非 PNG/JPEG）")
        return 3
    sw, sh = size
    sx, sy = sw / geo["w"], sh / geo["h"]
    print(f"\n截图像素尺寸: {sw} x {sh}")
    print(f"实测缩放比: {sx:.3f}x (横) / {sy:.3f}x (纵)")

    # 判定：kimi-cu 声称内部会 rescale；缩放比应等于系统 backingScaleFactor
    if abs(sx - sy) > 0.02:
        print("⚠️ 横纵缩放比不一致 → 可能存在非等比拉伸，点击会偏")
        return 1
    print(f"\n结论：截图相对逻辑窗口为 {sx:.2f}x 等比缩放。")

    print("  下一步：取截图中心像素点做 click，再读回该点元素，")
    print("  比对是否命中窗口中心元素——只有这一步通过才能说『坐标校正实测有效』。")
    print("  仅凭『缩放比等比』**不足以**证明点击准确。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
