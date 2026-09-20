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
    try:
        d = open(path, "rb").read(24)
        if d[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", d[16:24])
    except OSError:
        return None


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

    r = mcp("get_app_state", {"app": args.bundle, "mode": "image"}) if args.bundle else \
        mcp("get_app_state", {"pid": 0, "mode": "image"})
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
        print("✗ 未返回图像内容")
        return 3

    size = png_size(args.shot)
    if not size:
        print("✗ 截图非合法 PNG")
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
    print("  下一步（人工/后续脚本）：取截图中心像素点做 click，再读回该点元素，")
    print("  比对是否命中窗口中心元素——只有这一步通过才能说『坐标校正实测有效』。")
    print("  仅凭『缩放比等比』**不足以**证明点击准确。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
