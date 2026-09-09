#!/usr/bin/env python3
# loment_diag.py — 诊断分类 + 修复建议 (M64, docs/148)
#
# 判据: 10 类错误各有错误码与修复建议。
# 实现: 对 lomentc.check() 的原始消息做模式分类 (不改编译器消息本身),
#       每类给出稳定错误码 (E0xx) 与可执行建议。
#
# 用法:
#   python tools/loment_diag.py FILE [--json]
# 退出码: 0 = 无错误 / 1 = 有错误 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# (错误码, 模式, 标题, 修复建议)
RULES = [
    ("E001", r"类型 .*应为|表达式类型|返回类型 .*声明|赋值 .* = 表达式类型",
     "类型不匹配", "检查两侧类型；整型字面量按上下文定宽，必要时用 `as` 显式转换。"),
    ("E002", r"未定义的函数|未声明的变量|未声明$|未知结构体|未知枚举|类型 .*未声明",
     "符号未声明", "先声明后使用；跨模块调用需要 `pub`，或补 `use \"*.lomt\"`。"),
    ("E003", r"需要 \d+ 个实参",
     "实参数量不符", "对照函数签名补/删实参；方法调用的 self 不计入实参。"),
    ("E004", r"guard 引用了未声明的能力|能力 .*域|能力 .*重复",
     "能力域非法", "在模块顶层声明 `capability name : space[lo..hi]`，并保证 lo ≤ hi。"),
    ("E005", r"使用了 excluded 的空间",
     "与出界声明冲突", "要么删掉 `excluded \"<space>: ...\"`，要么换一个能力空间。"),
    ("E006", r"已被移动",
     "移动后使用", "移动后改用引用（`&`/`&mut`）传参，或让类型实现 `Copy` 语义（全字段为整型）。"),
    ("E007", r"既被可变借用又被借用|被可变借用两次",
     "借用冲突", "同一次调用里对同一变量只保留一种借用；先复制到临时变量再传。"),
    ("E008", r"无变体|重复模式|不是枚举|match 主体|不穷尽",
     "match 模式非法", "模式必须覆盖所有变体且不重复；主体必须是枚举类型的值。"),
    ("E009", r"无字段|缺字段|重复初始化|与基类型同名|为空",
     "结构体/类型定义非法", "字段名与顺序按声明补齐；类型名不要与 `u32` 等基类型重名。"),
    ("E010", r"\? 只能用于",
     "`?` 误用", "`?` 只能出现在 `let x: T = expr?;` 的绑定位置，且函数返回 `Result`。"),
    ("E011", r"只读切片不能写|形参需声明 mut",
     "只读切片写入", "把形参改成 `mut [T]`，调用处用 `&mut 数组`。"),
    ("E012", r"悬垂",
     "悬垂借用", "不要返回局部变量的引用；把值返回（按值），或在调用方分配。"),
    ("E013", r"重复定义|重名|参数 .*重复",
     "重名", "同一作用域内重命名其中之一。"),
]


def classify(msg: str) -> tuple[str, str, str]:
    for code, pat, title, hint in RULES:
        if re.search(pat, msg):
            return code, title, hint
    return "E999", "未分类", "请报告此消息以便补充分类规则。"


def diagnose(errs: list[str]) -> list[dict]:
    out = []
    for e in errs:
        code, title, hint = classify(e)
        out.append({"code": code, "title": title, "message": e, "hint": hint})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_diag", description="诊断分类 + 修复建议")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lom-root", default=None)
    a = ap.parse_args(argv)
    root = Path(a.lom_root) if a.lom_root else ROOT
    p = Path(a.file)
    try:
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, root, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
    except lomentc.LomError as e:
        errs = [str(e)]
    diags = diagnose(errs)
    if a.json:
        print(json.dumps(diags, ensure_ascii=False, indent=2))
    else:
        for d in diags:
            print(f"{d['code']} [{d['title']}] {d['message']}")
            print(f"      建议: {d['hint']}")
        if not diags:
            print("[OK] 无错误")
    return 1 if diags else 0


if __name__ == "__main__":
    sys.exit(main())
