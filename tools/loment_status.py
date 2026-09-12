#!/usr/bin/env python3
# loment_status.py — 100 里程碑状态矩阵 (M100 审计素材, docs/154)
#
# 从 docs/145 的里程碑表格提取每行状态, 生成单一矩阵与计数:
#   python tools/loment_status.py --emit docs/154-loment-status.md
#   python tools/loment_status.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "145-loment-100-milestones.md"
OUT = ROOT / "docs" / "154-loment-status.md"
ROW = re.compile(r"^\|\s*(M\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$")


def parse() -> list[tuple[str, str, str, str]]:
    rows = []
    for ln in DOC.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("|"):
            continue
        # 尊重 Markdown 表格转义: `\|` 是字面竖线, 不能当分隔符
        cells = [c.strip().replace("\\|", "|")
                 for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))]
        if not cells or not re.fullmatch(r"M\d+", cells[0]):
            continue
        mid = cells[0]
        name = cells[1] if len(cells) > 1 else ""
        crit = cells[2] if len(cells) > 2 else ""
        status = cells[3] if len(cells) > 3 else "未开始"
        rows.append((mid, name, crit, status))
    return rows


def classify(status: str) -> str:
    if status.startswith("✅ 部分"):
        return "部分"
    if status.startswith("✅"):
        return "完成"
    if status.startswith("⚠️"):
        return "未达"
    if status.startswith("待做") or status in ("—", ""):
        return "未开始"
    return "未开始"


def build() -> str:
    rows = parse()
    assert len(rows) == 100, f"docs/145 里程碑行数 {len(rows)} != 100"
    counts = {"完成": 0, "部分": 0, "未达": 0, "未开始": 0}
    for _, _, _, st in rows:
        counts[classify(st)] += 1
    out = [
        "# 154 · Loment 100 里程碑状态矩阵",
        "",
        "> 由 `tools/loment_status.py` 从 docs/145 生成 —— 请勿手改。",
        "> 计数口径：`完成` = ✅；`部分` = ✅ 部分；`未达` = ⚠️；其余按未开始。",
        "",
        f"- 完成 **{counts['完成']}/100** · 部分 **{counts['部分']}** · "
        f"未达 **{counts['未达']}** · 未开始 **{counts['未开始']}**",
        "",
        "| 里程碑 | 名称 | 判据 | 状态 | 归类 |",
        "|---|---|---|---|---|",
    ]
    for mid, name, crit, st in rows:
        out.append(f"| {mid} | {name} | {crit} | {st} | {classify(st)} |")
    out += ["", "## 剩余工作（按依赖）", ""]
    pending = [r for r in rows if classify(r[3]) != "完成"]
    for mid, name, _, st in pending:
        out.append(f"- **{mid}** {name} —— {st}")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_status")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    try:
        want = build()
    except AssertionError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 2
    if a.emit:
        # 显式 LF (仓库 *.md 声明 eol=lf), 见 loment_manual 同处注释。
        with OUT.open("w", encoding="utf-8", newline="\n") as f:
            f.write(want)
        head = next(l for l in want.splitlines() if l.startswith("- 完成"))
        print(f"[OK] {OUT.relative_to(ROOT)}: {head.lstrip('- ')}")
        return 0
    if a.check or not (a.emit or a.check):
        # 无参数 = 门禁模式 (与其它工具的自检约定一致): 只校验
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != want:
            print("[DIFF] docs/154-loment-status.md 与 docs/145 不一致")
            return 1
        print("[OK] 状态矩阵与 docs/145 一致")
        return 0
    print("[ERR] 需要 --emit 或 --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
