#!/usr/bin/env python3
# loment_rule_parity.py — 规则覆盖率探针 (M85 后半, docs/145 / docs/150)
#
# 目的: 量化"自举 checker 与参考实现是否等价" —— 这是"脱离 Python"的第一道门
#       (谁在当规范的执行者)。做法: 每条参考规则配一个**最小负例**, 两边各跑一遍,
#       按 loment_diag 的统一错误码口径比对码集。
#
# 用法:
#   python tools/loment_rule_parity.py            # 覆盖率表 + 门禁 (退出码 0/1)
#   python tools/loment_rule_parity.py --gaps     # 只列自举版还没实现的规则
#   python tools/loment_rule_parity.py --case X   # 只跑某条 (调试用)
#
# 退出码: 0 = 达到预算且无假阳性/漂移 / 1 = 未达标 (便于 CI 里"只紧不松"地卡住)
#
# 门禁 (ratchet): 允许的缺口是**有界、可数、只减不增**的 —— 判据是
#   eq >= BUDGET             (等价规则数不得低于预算)
#   extras == 0 && drift == 0 && nopy == 0   (假阳性/口径漂移/探针失效必须是 0)
# 补完一批就把 BUDGET 往上调; 只调低 BUDGET 是**放松**门禁, 等于隐瞒缺口。
#
# 判据: status 列
#   EQUAL   两边码集相同           (等价)
#   MISSING 自举版少报 (缺口, 要补) (⊂)
#   EXTRA   自举版多报 (假阳性)     (⊃)  —— 这个更糟, 会误拒合法程序
#   DIFF    两边都不空但码不同     (码口径漂移)
#   NOPY    参考实现没报错 (探针本身写错了, 案例不算数)

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

import loment_p8_test as H  # noqa: E402  # 复用已验过的构建/运行夹具 (避免第二份漂移)

# 门禁预算: 批次 1 (声明级规则) 24/60; 批次 2 第一段 (语句级类型比对) 32/60;
# 批次 2 第二段 (复合类型表 + 表达式遍历器 + 字段/下标/数组/`as`/实参/方法/match/`?`/
# 移动借用) 后 **63/63** —— 自举 checker 与参考实现在这 63 条规则上完全等价
# (含 M13 移动 E006 与 M17 悬垂 E012)。
# 2026-09-17 项目模式 `choose` (E022) 进语言: 检查器那两条 (至多一次 / 模式名合法)
# 补完, **65/65**。第三条 ("库不许 choose") 是装载器规则, 不在这个数里。
# 每补完一批就**往上调** —— 只调低是放松门禁, 等于隐瞒缺口。
BUDGET = 65

# --------------------------------------------------------------------------- 案例表
#
# 每条 = (规则名, 源码)。规则名里的 `E0xx` 是 loment_diag.RULES 的期望口径,
# 只在注释里做提示 —— 但**判据不比注释, 只比两边实测码集**, 所以注释写错不会误判。
# 源码一律是**单编译单元**(无 use): 自举 checker 不解析模块导入 (docs/150 边界)。

_CASES: list[tuple[str, str]] = [
    # ---- 顶层重名 (E013) --------------------------------------------------
    ("dup-fn", "module m\n\nfn f() -> u32 {\n    return 1;\n}\n\nfn f() -> u32 {\n    return 2;\n}\n"),
    ("dup-struct", "module m\n\nstruct S {\n    a: u32,\n}\n\nstruct S {\n    b: u32,\n}\n"),
    ("dup-enum", "module m\n\nenum E {\n    A,\n}\n\nenum E {\n    B,\n}\n"),
    ("dup-const", "module m\n\nconst C: u32 = 1;\nconst C: u32 = 2;\n"),
    ("dup-fn-vs-const", "module m\n\nfn C() -> u32 {\n    return 1;\n}\n\nconst C: u32 = 2;\n"),
    ("dup-param", "module m\n\nfn f(a: u32, a: u32) -> u32 {\n    return a;\n}\n"),
    # ---- 结构体/枚举定义 (E009 / E013) -------------------------------------
    ("struct-field-dup", "module m\n\nstruct S {\n    a: u32,\n    a: u32,\n}\n"),
    ("struct-empty", "module m\n\nstruct S {\n}\n"),
    ("struct-base-name", "module m\n\nstruct u32 {\n    a: u32,\n}\n"),
    ("enum-variant-dup", "module m\n\nenum E {\n    A,\n    A,\n}\n"),
    ("enum-empty", "module m\n\nenum E {\n}\n"),
    ("enum-base-name", "module m\n\nenum u32 {\n    A,\n}\n"),
    # ---- 声明里的未声明类型 (E002) -----------------------------------------
    ("struct-field-type-unknown", "module m\n\nstruct S {\n    a: Foo,\n}\n"),
    ("enum-payload-type-unknown", "module m\n\nenum E {\n    A(Foo),\n}\n"),
    ("fn-ret-type-unknown", "module m\n\nfn f() -> Foo {\n    return 1;\n}\n"),
    ("fn-param-type-unknown", "module m\n\nfn f(x: Foo) -> u32 {\n    return 1;\n}\n"),
    ("let-type-unknown", "module m\n\nfn f() -> u32 {\n    let x: Foo = 1;\n    return x;\n}\n"),
    # ---- 常量 (E001 / E013) -----------------------------------------------
    # 注意: `const C: bool = true;` 在**解析期**就被拒 (常量值走 int_lit), 到不了 check;
    # 所以能触发类型规则的只有 "类型不是整型但值是整数字面量" 这个形状。
    ("const-non-int-type", "module m\n\nconst C: bool = 0;\n"),
    # ---- 能力域 (E004 / E005) ---------------------------------------------
    ("cap-dup", "module m\n\ncapability c : space[1..2]\ncapability c : space[1..2]\n"),
    ("cap-lo-gt-hi", "module m\n\ncapability c : space[5..2]\n"),
    ("cap-unknown-guard",
     "module m\n\nfn f() -> u32 {\n    guard nope(0);\n    return 1;\n}\n"),
    # ---- 调用 (E002 / E003 / E001) ----------------------------------------
    ("call-unknown-fn", "module m\n\nfn f() -> u32 {\n    return g(1);\n}\n"),
    ("call-arity", "module m\n\nfn g(a: u32) -> u32 {\n    return a;\n}\n\nfn f() -> u32 {\n    return g(1, 2);\n}\n"),
    ("call-arg-type", "module m\n\nfn g(a: u32) -> u32 {\n    return a;\n}\n\nfn f() -> u32 {\n    return g(true);\n}\n"),
    ("builtin-arity", "module m\n\nfn f() -> u32 {\n    return str_len();\n}\n"),
    ("builtin-arg-type", "module m\n\nfn f() -> u32 {\n    return str_len(1);\n}\n"),
    # ---- 表达式 (E001 / E008) ---------------------------------------------
    ("let-type-mismatch", "module m\n\nfn f() -> u32 {\n    let x: u32 = true;\n    return x;\n}\n"),
    ("return-type-mismatch", "module m\n\nfn f() -> u32 {\n    return true;\n}\n"),
    ("assign-undeclared", "module m\n\nfn f() -> u32 {\n    x = 1;\n    return 0;\n}\n"),
    ("assign-type-mismatch",
     "module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n    x = true;\n    return x;\n}\n"),
    ("assign-not-lvalue", "module m\n\nfn f() -> u32 {\n    1 = 2;\n    return 0;\n}\n"),
    # 条件类型用 `str` 变量触发 (整型字面量的类型推断是 None, 参考实现据此放行)
    ("if-cond-not-bool",
     "module m\n\nfn f(s: str) -> u32 {\n    if s {\n        return 1;\n    }\n    return 0;\n}\n"),
    ("while-cond-not-bool",
     "module m\n\nfn f(s: str) -> u32 {\n    while s {\n        return 1;\n    }\n    return 0;\n}\n"),
    # `for i in A..B` 的 A/B 必须是整型 (语法上 for 只吃 range, 没有 `for i in x` 形式)
    ("for-non-int",
     "module m\n\nfn f(s: str) -> u32 {\n    for i in s..2 {\n        return i;\n    }\n    return 0;\n}\n"),
    ("as-bad-target", "module m\n\nfn f() -> u32 {\n    return (1 as Foo);\n}\n"),
    ("as-bad-operand", "module m\n\nfn f(s: str) -> u32 {\n    return (s as u32);\n}\n"),
    # ---- 字段 / 下标 / 数组 (E002 / E009) ---------------------------------
    ("field-on-non-struct", "module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n    return x.a;\n}\n"),
    ("field-unknown-name",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f(p: S) -> u32 {\n    return p.z;\n}\n"),
    ("structlit-unknown-field",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f() -> u32 {\n    let p: S = S { z: 1 };\n    return p.a;\n}\n"),
    ("structlit-dup-field",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f() -> u32 {\n    let p: S = S { a: 1, a: 2 };\n    return p.a;\n}\n"),
    ("structlit-missing-field",
     "module m\n\nstruct S {\n    a: u32,\n    b: u32,\n}\n\nfn f() -> u32 {\n    let p: S = S { a: 1 };\n    return p.a;\n}\n"),
    ("array-empty", "module m\n\nfn f() -> u32 {\n    let a: [u32; 4] = [];\n    return a[0];\n}\n"),
    ("array-elem-type", "module m\n\nfn f() -> u32 {\n    let a: [u32; 2] = [1, true];\n    return a[0];\n}\n"),
    ("index-non-array", "module m\n\nfn f() -> u32 {\n    let x: u32 = 1;\n    return x[0];\n}\n"),
    ("index-non-int",
     "module m\n\nfn f(a: [u32; 4]) -> u32 {\n    return a[true];\n}\n"),
    ("slice-len-arity", "module m\n\nfn f() -> u32 {\n    return slice_len();\n}\n"),
    # 整型字面量的类型推断是 None, 参考实现据此放行 -> 用 str 变量才能触发
    ("slice-len-arg-type", "module m\n\nfn f(s: str) -> u32 {\n    return slice_len(s);\n}\n"),
    # ---- match (E008 / E001) ----------------------------------------------
    # 模式语法只吃 `Enum::Variant` 或 `_` (没有整型/字面量模式), 臂体必须是块。
    ("match-not-enum",
     "module m\n\nfn f() -> u32 {\n    match 1 {\n        _ => { return 1; }\n    }\n    return 0;\n}\n"),
    ("match-unknown-variant",
     "module m\n\nenum E {\n    A,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::Z => { return 1; }\n    }\n}\n"),
    ("match-dup-pattern",
     "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A => { return 1; }\n        E::A => { return 2; }\n    }\n}\n"),
    ("match-nonexhaustive",
     "module m\n\nenum E {\n    A,\n    B,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A => { return 1; }\n    }\n}\n"),
    ("match-payload-arity",
     "module m\n\nenum E {\n    A,\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A(x) => { return x; }\n    }\n}\n"),
    ("match-payload-type",
     "module m\n\nenum E {\n    A(u32),\n}\n\nfn f(e: E) -> u32 {\n    match e {\n        E::A(b) => { if b { return 1; } return 2; }\n    }\n}\n"),
    ("match-enum-lit-no-payload",
     "module m\n\nenum E {\n    A,\n}\n\nfn f() -> u32 {\n    let e: E = E::A(1);\n    return 0;\n}\n"),
    ("match-enum-lit-bad-payload",
     "module m\n\nenum E {\n    A(u32),\n}\n\nfn f() -> u32 {\n    let e: E = E::A(true);\n    return 0;\n}\n"),
    # ---- `?` / 借用 / 方法 (E010 / E007 / E011 / E002) ---------------------
    ("qmark-misuse", "module m\n\nfn f() -> u32 {\n    let x: u32 = 1?;\n    return x;\n}\n"),
    ("method-unknown",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f(p: S) -> u32 {\n    return p.nope();\n}\n"),
    ("slice-write-readonly",
     "module m\n\nfn f(xs: [u32]) -> u32 {\n    xs[0] = 1;\n    return 0;\n}\n"),
    ("borrow-mut-twice",
     "module m\n\nfn g(a: mut [u32], b: mut [u32]) -> u32 {\n    return 0;\n}\n\nfn f(xs: mut [u32]) -> u32 {\n    return g(&mut xs, &mut xs);\n}\n"),
    ("borrow-mut-and-share",
     "module m\n\nfn g(a: mut [u32], b: [u32]) -> u32 {\n    return 0;\n}\n\nfn f(xs: mut [u32]) -> u32 {\n    return g(&mut xs, &xs);\n}\n"),
    # ---- 移动 / 悬垂 (E006 / E012) ----------------------------------------
    # 非 Copy 类型 (struct / 数组) 的变量"传出去"就算移动: `let t: S = s;` 之后 s 不能再用。
    ("move-use-after",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn f() -> u32 {\n    let s: S = S { a: 1 };\n    let t: S = s;\n    return s.a;\n}\n"),
    ("move-by-arg",
     "module m\n\nstruct S {\n    a: u32,\n}\n\nfn take(p: S) -> u32 {\n    return p.a;\n}\n\nfn f() -> u32 {\n    let s: S = S { a: 1 };\n    let n: u32 = take(s);\n    return s.a;\n}\n"),
    # 返回局部变量的借用 = 悬垂 (`&arr` 借用局部数组)
    ("dangling-return",
     "module m\n\nfn f() -> [u32] {\n    let a: [u32; 2] = [1, 2];\n    return &a;\n}\n"),
    # ---- 项目模式 `choose` (E022, docs/143 §3.2) --------------------------
    # 这两条是**检查器**规则。第三条 ("库不许 choose") 是装载器规则, 装不进这里 ——
    # 套件喂的是自包含单文件源码, 没有"被 use 进来的那一层"。与 E018 同构,
    # 棘轮由驱动闸门 (`loment_p8_test` 的 neg_dep_choose) 承担, 这两条才进预算。
    ("choose-twice", "module m\n\nchoose std\nchoose no_std\n\nfn f() -> u32 {\n    return 1;\n}\n"),
    ("choose-bad-mode", "module m\n\nchoose fast\n\nfn f() -> u32 {\n    return 1;\n}\n"),
]


def _report(rid: str, src: str, exe: Path, td: str) -> tuple[str, str, list[int], list[int]]:
    """跑一条案例, 返回 (status, 明细, py 码, loment 码)。"""
    f = Path(td) / f"case_{rid}.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    py_msgs = lomentc.check(lomentc.load(f))
    py = H._classify_codes(py_msgs)
    lo, detail = H._loment_codes(exe, f)
    if not py:
        return "NOPY", "参考实现未报错", py, lo
    sp, sl = set(py), set(lo)
    if sp == sl:
        st = "EQUAL"
    elif sl < sp:
        st = "MISSING"
    elif sl > sp:
        st = "EXTRA"
    else:
        st = "DIFF"
    unclass = [m for m in py_msgs if H.loment_diag.classify(m)[0] == "E999"]
    det = f"py={sorted(sp)} loment={sorted(lo)}"
    if unclass:
        det += " | 未分类: " + unclass[0][:60]
    return st, det, py, lo


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    only: str | None = None
    gaps_only = False
    i = 0
    while i < len(args):
        if args[i] == "--case" and i + 1 < len(args):
            only = args[i + 1]
            i += 2
        elif args[i] == "--gaps":
            gaps_only = True
            i += 1
        else:
            print(__doc__)
            return 2
    clang = H._clang()
    if not clang:
        print("[SKIP] 无 clang, 无法构建自举 checker")
        return 0
    cases = [(r, s) for r, s in _CASES if only is None or r == only]
    if not cases:
        print(f"[ERR] 没有案例匹配 {only!r}")
        return 2
    counts: dict[str, int] = {}
    gaps: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as td:
        exe = H._build_checker(td)
        for rid, src in cases:
            st, det, _py, lo = _report(rid, src, exe, td)
            counts[st] = counts.get(st, 0) + 1
            if st == "NOPY":
                gaps.append((rid, "探针失效: 参考实现未报错"))
            elif st != "EQUAL":
                gaps.append((rid, det))
            if not gaps_only or st != "EQUAL":
                print(f"  {st:8s} {rid:28s} {det}")
    total = sum(counts.values())
    eq = counts.get("EQUAL", 0)
    extra = counts.get("EXTRA", 0)
    drift = counts.get("DIFF", 0)
    nopy = counts.get("NOPY", 0)
    print(f"\n规则覆盖率: {eq}/{total} 等价 (预算 {BUDGET})"
          f"  (缺口 {counts.get('MISSING', 0)}"
          f" / 假阳性 {extra} / 口径漂移 {drift} / 探针失效 {nopy})")
    if gaps and not gaps_only:
        print("  待补:")
        for rid, det in gaps:
            print(f"    - {rid}: {det}")
    # 单条调试模式: 这一条不是 EQUAL 就算失败 (与预算无关)
    if only is not None:
        return 0 if eq == 1 else 1
    bad: list[str] = []
    if extra or drift or nopy:
        bad.append(f"假阳性 {extra} / 口径漂移 {drift} / 探针失效 {nopy} (必须为 0)")
    if eq < BUDGET:
        bad.append(f"等价规则 {eq} < 预算 {BUDGET} (回退; 补完一批才把 BUDGET 往上调)")
    if bad:
        for b in bad:
            print(f"[FAIL] {b}")
        return 1
    print(f"[OK] 达到预算 {eq}/{BUDGET} 且无假阳性/漂移")
    return 0


if __name__ == "__main__":
    sys.exit(main())
