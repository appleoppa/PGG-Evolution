#!/usr/bin/env python3
"""生成评测曲线（纯文本 ASCII 图，无第三方依赖）。"""
from __future__ import annotations

DATA = [
    ("修复前基线", 0.0),
    ("第一轮修复", 0.75),
    ("第二轮aliases", 1.0),
    ("holdout2基线", 0.333),
    ("fuzzy真泛化", 1.0),
]


def render_ascii_chart(data: list[tuple[str, float]], width: int = 40) -> str:
    lines = []
    max_label = max(len(k) for k, _ in data)
    for label, val in data:
        bar_len = int(val * width)
        bar = "█" * bar_len + ("░" * (width - bar_len))
        lines.append(f"{label:<{max_label}} │{bar}│ {val:.0%}")
    return "\n".join(lines)


def main() -> None:
    print("自进化评测曲线（warmup vs holdout）")
    print("=" * 70)
    print("\n--- warmup（已知样例，可调优）---")
    print(render_ascii_chart(DATA))
    print("\n--- holdout（未见样例，真泛化）---")
    holdout = [("holdout1基线", 0.0), ("holdout1补齐", 1.0), ("holdout2冻结基线", 0.333), ("fuzzy真泛化", 1.0)]
    print(render_ascii_chart(holdout))
    print("\n" + "=" * 70)
    print("教训：warmup 100% ≠ 真进化；holdout 提升才是真泛化证据")


if __name__ == "__main__":
    main()
