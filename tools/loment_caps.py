#!/usr/bin/env python3
# loment_caps.py — 自举编译器那一侧**固定容量的盘点**（`FujoJTOP/loment#370`、`#345`）。
#
# 口径（机械的，不靠记性）：
#
#   * **容量** = 一个 `const NAME: u32 = <数字>`，且它在某一处**被当作上界**用过 ——
#     也就是出现在一条**序关系**比较里（`>` `>=` `<` `<=`），而不是只做相等比较或只当
#     一个码值传出去。`E_DEEP = 23` 那种是**诊断码**（它只会被 `==` 或当实参），不算容量；
#     `SLOT = 524288`、`TB_LBL_MAX = 32768` 那种才把"能装多少"写成了一个数。
#   * **有话 / 沉默** = 那个上界被突破时，**同一处守卫分支**里有没有一句说得出话的东西。
#     会说话的形状在这两棵树里只有几种，逐个列在 `SPEAKS` 里（都是**测试得到**的，
#     不是猜的）：`fail(`、`sym_pub(`、`err_`、以及带 `E0`／`太大`／`非法` 的字面量。
#     一条都没有 ⇒ 走过去了 ⇒ 越界就是**下标写穿**（自举侧的症状是段错误，`docs/345`）。
#
# 这份清单**是算出来的**，所以它不会漂：改一个容量、加一个检查，下一轮的行数就变 ——
# `loment_caps_test.py` 把两个数钉住（总数、沉默的条数），并逐条钉住沉默的那一份名单。
#
# 用法: python tools/loment_caps.py            # 表格 + 汇总
#       python tools/loment_caps.py --silent   # 只列沉默的那一份（给判据读）

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 扫哪两棵树 —— 编译器本体（`selfhost/`）与包内工具（`tools/`，里面是链接器、CLI 等）。
TREES = ("loment/selfhost", "loment/tools")

#: **排除生成的数据文件**：它们的常量是表项（步长/条目数），不是"编译器自己有多少内存"。
#: 混进来会把数抬高一截，而这个数是要拿来说事的。
EXCLUDE = ("loment/tools/surface_data.lomt", "loment/tools/win_shim_data.lomt")

_CONST = re.compile(r"^const\s+([A-Z][A-Z0-9_]*)\s*:\s*u32\s*=\s*(\d+)\s*;", re.M)
#: 序关系比较：容量出现在任一侧。`==`/`!=` **不算** —— 那是码值，不是上界。
_BOUND = r"(?:>=|<=|>|<)"
#: 说得出话的几种形状（自举侧那两棵树里实际用到的，见文件头）。
#:
#: `loment: ` 是 2026-10-16 加的（`FujoJTOP/loment#8`）：那一轮把面向用户的失败消息
#: 从中文改成 **`loment: ` 开头的纯 ASCII 英文**。原先认得出话靠的是 `超`／`太大` 这类
#: **中文词**，改完就一条都认不出来了 —— 而守卫**照旧在报**（`wstr(2, "loment: …")`
#: 之后 `die(1)`）。加这一条不是放宽判据，是**把新形状补进词汇表**；实测它只让那三条
#: 恢复（`DIAGB_OFF` / `MAX_ADDIN` / `MAX_USE`），沉默总数不动。
SPEAKS = ("fail(", "sym_pub(", "err_", "E0", "loment: ", "太大", "非法", "超", "越界")

#: 守卫分支往后看几行算"同一处"。
LOOKAHEAD = 8


def _sources() -> list[Path]:
    out: list[Path] = []
    for t in TREES:
        out += [p for p in sorted((ROOT / t).glob("*.lomt"))
                if p.relative_to(ROOT).as_posix() not in EXCLUDE]
    return out


def scan() -> list[dict]:
    """回每一处容量：`{file, line, name, value, speaks}`（同一常量在几处用到就是几条）。"""
    rows: list[dict] = []
    for p in _sources():
        text = p.read_text(encoding="utf-8")
        lines = text.split("\n")
        caps = {m.group(1): int(m.group(2)) for m in _CONST.finditer(text)}
        for name, value in caps.items():
            esc = re.escape(name)
            # **上界**：它出现在一条**序关系**比较里（`==`/`!=` 不算 —— 那是码值）。
            bound_lines = [
                i for i, ln in enumerate(lines)
                if re.search(rf"(?:{_BOUND}\s*{esc}\b)|(?:\b{esc}\s*{_BOUND})", ln)
            ]
            # **尺寸/步长**：它乘在别的东西上（`* NN`），或本身就是一次分配/声明的长度。
            # `SLOT` 那种只做步长的就是靠这一条进来的 —— 它当然是容量，只是从没被比较过。
            size_lines = [
                i for i, ln in enumerate(lines)
                if re.search(rf"(?:\*\s*{esc}\b)|(?:\b{esc}\s*\*)|"
                             rf"(?:\b(?:alloc|sys_alloc|reserve)\s*\(\s*{esc}\b)", ln)
            ]
            sites = sorted(set(bound_lines) | set(size_lines))
            if not sites:
                continue                     # 既不是上界也不是尺寸 ⇒ 不是容量（码值/掩码）
            speaks = any(
                any(s in ln for s in SPEAKS)
                for i in sites
                for ln in lines[i:i + LOOKAHEAD]
            )
            rows.append({"file": p.relative_to(ROOT).as_posix(),
                         "line": sites[0] + 1, "name": name, "value": value,
                         "kind": ("上界" if bound_lines else "尺寸"), "speaks": speaks})
    return rows


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    rows = scan()
    silent = [r for r in rows if not r["speaks"]]
    if "--silent" in a:
        for r in silent:
            print(f"{r['file']}:{r['line']} {r['name']}={r['value']}")
        return 0
    print(f"{'文件:行':<44} {'容量':<16} {'值':>10}  越界时")
    for r in rows:
        where = f"{r['file']}:{r['line']}"
        print(f"{where:<44} {r['name']:<16} {r['value']:>10}  "
              f"{'有话' if r['speaks'] else '**沉默**'}")
    print(f"\n容量 {len(rows)} 处；**有话 {len(rows) - len(silent)}，沉默 {len(silent)}**"
          f"（沉默的那些越界就是写穿 —— 自举侧的症状是段错误）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
