#!/usr/bin/env python3
"""loment_extblock_test.py — 外部代码块的**字节保真**（S1，`docs/185`）。

**核心那条是 `test_raw_body_is_byte_faithful`**：块里的正文是一个 raw token，
它的 `(off, len)` 圈到的源字节与**原文逐字节相同** —— 含 `"`、`\\`、`\\0`、非 ASCII。
那是 `docs/185` §2 要证明的东西：正文**不能**走字符串字面量（转义表只有 4 条、
未知转义静默退化、没有非 UTF-8 通道），所以它走新词法模式。

其余几条钉**消歧**（`docs/185` §3）：不带 `let` 的写法只在"声明过的语言名 + 顶层"下才认，
否则 `struct S {` / `fn f() -> u32 {` / `if c { }` 全会被误当成块。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 一段**故意的恶意正文** —— 它把字符串字面量那条路能踩的坑全踩一遍：
#: `"` 要转义、`\\0` 是未知转义（会静默退化成两个字符）、非 ASCII 没有非 UTF-8 通道。
#: 正文里还各带一个**不配对的** `{` / 引号，说明"朴素计数只数花括号"这条边界在哪。
_EVIL = ('#include <stdio.h>\n'
         'const char *s = "a\\"b\\\\c\\0d";\n'      # " 与 \ 与 \0
         'int f(void) { return 1; }\n'
         '/* 中文注释 · 不全角 */\n')


def _src(form: str) -> str:
    """把 `_EVIL` 按某种写法包成一份源。`form` 是 `let` / `bare`。"""
    head = "module m\n\ncommand c\n\n" if form == "bare" else "module m\n\n"
    return f"{head}{'c' if form == 'bare' else 'let c'} {{\n{_EVIL}}}\n\nfn main() -> u64 {{ return 0; }}\n"


@test
def test_raw_body_is_byte_faithful():
    """**正文逐字节相同** —— `docs/185` §6 给 S1.0 定的证伪判据。

    断言的是"raw token 的 `(off, len)` 圈到的源字节 == 原文"，**不是** `val` 与某个
    Python 字符串相等 —— 后者会把"两个实现都把转义解成同一个错"当成通过。
    这里比的是**字节**，而源是 UTF-8 读进来的，所以非 ASCII 也在里面。
    """
    # **正文是 `{` 与 `}` 之间的原始字节**（`docs/185` §4.1）—— 所以它**含**紧跟在
    # `{` 后面那个换行。这一条是刻意的：保真说的是"花括号之间有什么就是什么"，
    # 而不是"Loment 帮你把首尾空白修掉"（修掉就不是保真了）。
    want_c = ("\n" + _EVIL).encode("utf-8")
    for form in ("let", "bare"):
        src = _src(form)
        raws = [t for t in lomc.lex(src) if t.kind == "raw"]
        assert len(raws) == 1, f"{form}: 应当恰好一个 raw token, 实得 {len(raws)}"
        r = raws[0]
        got = src[r.off:r.off + r.len].encode("utf-8")
        want = want_c
        assert got == want, (
            f"{form}: 正文与原文不同（{len(got)}B vs {len(want)}B）\n"
            f"  原文: {want!r}\n  实得: {got!r}")
        # `val` 与跨度是**同一份**（参考侧两边都存，必须一致）
        assert r.val.encode("utf-8") == want, form
    print(f"      字节保真: 正文 {len(want_c)}B（含 \" \\ \\0 非 ASCII）两种写法都逐字节相同")


@test
def test_two_forms_are_equivalent():
    """`let c { … }` 与声明过的 `c { … }` 产出的 **token 流逐字段相同**（`docs/185` §6 S1.1）。

    `docs/183` §8.5 用户已定"两种写法都要、且等价"。这条把"等价"从一句话变成判据 ——
    比的是 kind/off/len（**不含 val**：两者 `val` 本来就相等，但 `off` 会因为
    `command c` 那一行而整体位移，所以比的是**形状**，不是绝对偏移）。
    """
    # **从块开始比**，不比声明头 —— 两种写法的头本来就不同（`let` vs `command c`），
    # 那是消歧规则的一部分，不是"等价"要覆盖的东西。
    def from_block(ts):
        k = next(i for i, t in enumerate(ts) if t.kind == "raw")
        return ts[k:]

    a = from_block(lomc.lex(_src("let")))
    b = from_block(lomc.lex(_src("bare")))
    shape = lambda ts: [(t.kind, t.len) for t in ts]  # noqa: E731
    sa, sb = shape(a), shape(b)
    assert sa == sb, f"两种写法的 token 形状不同:\n  let  {sa}\n  bare {sb}"
    ra = next(t for t in a if t.kind == "raw")
    rb = next(t for t in b if t.kind == "raw")
    assert ra.val == rb.val, "两种写法的正文不同"
    print(f"      {len(sa)} 个 token 形状相同；正文 {ra.len}B 相同")


@test
def test_bare_form_needs_a_declaration():
    """不带 `let` 的写法**只在声明过的语言名上认**（`docs/185` §3）。

    没声明就认的话，`struct S {` / `fn f() -> u32 {` 会被当成外部块 —— 那两处的 `{`
    前面也是标识符。这条同时钉住**反方向**：声明了之后才认。
    """
    undeclared = "module m\n\nc {\nint x = 1;\n}\n"
    assert not [t for t in lomc.lex(undeclared) if t.kind == "raw"], \
        "没声明过 `c`，`c {` 不该被当成外部块"
    declared = "module m\n\ncommand c\n\nc {\nint x = 1;\n}\n"
    assert len([t for t in lomc.lex(declared) if t.kind == "raw"]) == 1, \
        "声明过 `c` 之后，`c {` 应当是外部块"
    print("      消歧: 未声明不认、声明后认")


@test
def test_common_constructs_do_not_false_trigger():
    """`struct` / `fn` / `if` 的块**不该**被当成外部块（`docs/185` §3 的反面）。

    这三处都是"标识符后面跟 `{`"，正是消歧规则要挡的。第三个用例最刁：`c` **既是**
    声明过的语言名、又是个局部变量，`if c {` 在**函数体里**（非顶层）所以不认。
    """
    cases = {
        "struct": "module m\n\nstruct S {\n    a: u32,\n}\n",
        "fn": "module m\n\nfn f() -> u32 {\n    return 1;\n}\n",
        "if-同名的语言名": ("module m\n\ncommand c\n\nfn g() -> u32 {\n"
                          "    let c: u32 = 1;\n    if c > 0 {\n        return c;\n    }\n"
                          "    return 0;\n}\n"),
        "let 绑定（`:` 与 `=`）": ("module m\n\nfn h() -> u32 {\n    let c: u32 = 1;\n"
                              "    let d = 2;\n    return c + d;\n}\n"),
    }
    for name, src in cases.items():
        got = [t.kind for t in lomc.lex(src)]
        assert "raw" not in got, f"{name}: 误判成外部块 -> {got}"
    print(f"      反例: {len(cases)} 种常见构造都不误触发")


def main() -> int:
    failed: list[str] = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_extblock_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
