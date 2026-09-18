#!/usr/bin/env python3
"""loment_comefor_test.py — `comefor`/`byuse` 展开的判据（S4.1，`docs/184` §9）。

**核心那条是 `test_minimal_dialect_expands_to_handwritten`**：一段用自定义语法的源，
编出的 IR 与"手写展开后"的源**逐字节相同**。那是 `docs/184` §9 给 S4.1 定的证伪判据
—— 不是"能跑通"，是**逐字节**。

其余几条钉的是**边界**：没有 `comefor` 的文件必须逐 token 原样通过（否则 S4.1 就悄悄
改了所有既有程序的产物）、`comefor` 不是保留字、体里的 `use` 有明确的拒绝、`byuse`
收错名字要报错、宏体多吃要报错。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import loment_comefor  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CF = ROOT / "loment" / "comefor"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _ir(p: Path) -> str:
    mod, deps = lomentc.load_unit(p, ROOT)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{p.name} 检查不过: {errs[:2]}"
    return lomentc.emit_llvm(mod, ROOT, deps)


#: 没有 `comefor` 的文件 —— 逐个都要**逐 token 原样通过**。
_IDENTITY_FILES = ("loment/examples/bytes.lomt", "loment/selfhost/switches.lomt",
                   "loment/ct/strs.lomt", "loment/ct/offsets.lomt")


@test
def test_no_comefor_is_identity():
    """没有 `comefor` 的文件，展开前后**逐 token 同一批对象**。

    这条是"S4.1 不改变任何既有程序产物"的保证。展开器要是顺手 normalize 了什么
    （重建 token、丢字段、动顺序），这里当场红 —— 而 IR 判据未必看得出来
    （`docs/182` 的"消费方轴"：判据只跑它跑的那些）。
    """
    for rel in _IDENTITY_FILES:
        p = ROOT / rel
        txt = p.read_text(encoding="utf-8")
        a = lomc.lex(txt)
        b = loment_comefor.expand(list(a), txt)
        assert len(a) == len(b), f"{rel}: token 数变了 {len(a)} -> {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            assert x is y, (f"{rel}: 第 {i} 个 token 不是同一个对象"
                            f"（{x.kind} {x.val!r} -> {y.kind} {y.val!r}）")
    print(f"      恒等: {len(_IDENTITY_FILES)} 支无 comefor 的文件逐 token 原样通过")


@test
def test_minimal_dialect_expands_to_handwritten():
    """**S4.1 的核心判据**：方言源与手写展开源编出的 IR 逐字节相同（`docs/184` §9）。

    这是"展开器真的对"的唯一硬证据 —— 别的判据都只说明"没崩"。它同时覆盖 §3.2 的
    四个内建（`ct_tok`/`ct_out`/`ct_syn`/`ct_n` 里前三个都用到了）与 §4 的位置构造。
    """
    got = _ir(CF / "def_dialect.lomt")
    want = _ir(CF / "def_hand.lomt")
    if got != want:
        gl, wl = got.splitlines(), want.splitlines()
        diff = []
        for i in range(max(len(gl), len(wl))):
            x = gl[i] if i < len(gl) else "<无>"
            y = wl[i] if i < len(wl) else "<无>"
            if x != y and len(diff) < 8:
                diff.append(f"  L{i + 1}\n    方言: {x.strip()}\n    手写: {y.strip()}")
        raise AssertionError(
            f"方言源与手写展开源产出的 IR 不同（want {len(want)}B got {len(got)}B）:\n"
            + "\n".join(diff))
    print(f"      逐字节: 方言源 == 手写展开源（{len(got)}B）")


@test
def test_comefor_is_shape_not_reserved_word():
    """`comefor`/`byuse`/`done`/`to` **都不是保留字**（`docs/158` §4 第 12 条）。

    判形状而不是判词 —— `let comefor: u32 = 1;` 与 `fn byuse() -> u32` 都该照编不误。
    按词判会把它们误伤，而这类误伤只有真写了才看得见。
    """
    txt = ("module shape_not_word\n"
           "fn byuse() -> u32 { return 7; }\n"
           "fn to() -> u32 { return 1; }\n"
           "fn done() -> u32 { return 2; }\n"
           "fn comefor(a: u32) -> u32 { return a; }\n"
           "fn main() -> u64 {\n"
           "    let comefor: u32 = 3;\n"
           "    return (byuse() + to() + done() + comefor(4) + comefor) as u64;\n"
           "}\n")
    toks = loment_comefor.expand(lomc.lex(txt), txt)
    mod = lomentc.Parser(toks, txt).parse()
    assert [f.name for f in mod.funcs] == ["byuse", "to", "done", "comefor", "main"], \
        [f.name for f in mod.funcs]
    print("      形状判定: 四个词都能当标识符用")


@test
def test_body_rejects_use():
    """体里写 `use` 要**明确报错**，不是静默忽略（`docs/184` §11）。

    `use` 要的是文件级的依赖解析（相对路径 + 开关表），嵌在一段体里没有显然语义。
    静默忽略才是最坏的 —— 帮手找不到会变成 `undef`，指到一个看不出原因的地方。
    """
    txt = ("module body_use\n"
           "comefor let \"x\" to {\n"
           "    use bytes\n"
           "    fn main() -> u64 { return 0; }\n"
           "}\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)
    except lomc.LomError as e:
        assert "use" in str(e), e
        print("      体里的 use: 明确拒绝")
        return
    raise AssertionError("体里写 `use` 应当报错，实际过了")


@test
def test_byuse_unknown_name_is_rejected():
    """`byuse "没定义过的" done` 要报错 —— 收一个不在用的方言是笔误，静默收下会掩盖它。"""
    txt = ("module byuse_unknown\n"
           "fn main() -> u64 { return 0; }\n"
           "byuse \"nope\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)
    except lomc.LomError as e:
        assert "nope" in str(e), e
        print("      byuse 收错名字: 明确拒绝")
        return
    raise AssertionError("`byuse` 收一个没定义的名字应当报错，实际过了")


@test
def test_overconsumption_is_rejected():
    """宏体说它吃的比游标后面剩的还多 —— 要报错，不是让 `i` 跑过头。

    这条挡的是**宏体的笔误**（返回值写错）。放过去的话，游标会跳过文件尾，
    症状是"编译完了但产物是空的"，比一条指得出位置的错难查得多。
    """
    txt = ("module overconsume\n"
           "comefor let \"x\" to {\n"
           "    fn main() -> u64 { return 99; }\n"
           "}\n"
           "x 1;\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)
    except lomc.LomError as e:
        assert "99" in str(e), e
        print("      宏体多吃: 明确拒绝")
        return
    raise AssertionError("宏体报的消费数超过剩余 token 应当报错，实际过了")


@test
def test_definition_must_be_top_level():
    """`comefor` 写在块里要报错（`docs/184` §1："这套块不能嵌套"）。

    写在函数体里定义出来的方言，作用域会从**函数的中间**开始，而收它的 `byuse` 在文件
    别处 —— 读的人看不出这两件事有关系。**定义**限顶层；**使用**不限（方言构造用在函数
    体里正是它的用处）。
    """
    txt = ("module nested_def\n"
           "fn helper() -> u64 {\n"
           "    comefor let \"x\" to {\n"
           "        fn main() -> u64 { return 0; }\n"
           "    }\n"
           "    return 1;\n"
           "}\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)
    except lomc.LomError as e:
        assert "顶层" in str(e), e
        print("      块里的定义: 明确拒绝")
        return
    raise AssertionError("`comefor` 写在块里应当报错，实际过了")


@test
def test_entry_takes_no_params():
    """`fn main` 带形参要报错 —— 它要吃的东西全在游标上（`docs/184` §3.1）。

    不挡的话症状是跑起来报 `undef`（形参没有实参可绑），指在**体里面**；而真正的原因
    在签名上。**错要指在错的地方。**
    """
    # **必须真的用一次** —— 这条守卫在 `_run` 里（要跑才谈得上形参），
    # 只定义不使用的话它根本不经过。第一版就是这么写的，判据红了才知道。
    txt = ("module param_entry\n"
           "comefor let \"x\" to {\n"
           "    fn main(n: u64) -> u64 { return n; }\n"
           "}\n"
           "x 1 ;\n"
           "fn main() -> u64 { return 0; }\n"
           "byuse \"x\" done\n")
    try:
        loment_comefor.expand(lomc.lex(txt), txt)
    except lomc.LomError as e:
        assert "形参" in str(e), e
        print("      main 带形参: 明确拒绝")
        return
    raise AssertionError("`fn main` 带形参应当报错，实际过了")


@test
def test_domain_is_the_builtin_table():
    """宏体里**表外的名字调不到** —— 这就是"编译期域"（`docs/184` §5.1，S4.2 的证伪判据）。

    `docs/184` §9 给 S4.2 定的判据是"`syscall4` 写在宏体里 -> **被拒**"。这条钉它。

    **域不是新加的一层**：两个解释器都只有**一张固定内建表**，表外的名字不是"被检查
    出来"，是**根本不存在**。所以 §5 原来担心的"宏体能不能 syscall / 能不能开文件"
    在这一版是**调不到**。

    这条的价值在于**它会拦住"内建表被顺手加宽"**：哪天真让 `syscall4` 可用了，
    这里当场红 —— 而那是**换了一个安全模型**，得有人明确决定，不能顺手带进来。
    """
    # 同一个体，换掉那一个名字：`alloc` 在内建表里（过），`syscall4` 不在（拒）。
    def body(call: str) -> str:
        return ("module dom\n"
                "comefor let \"d\" to {\n"
                "    fn main() -> u64 {\n"
                f"        let x: u64 = {call};\n"
                "        return x;\n"
                "    }\n"
                "}\n"
                "d 1 ;\n"
                "fn main() -> u64 { return 0; }\n"
                "byuse \"d\" done\n")

    ok = loment_comefor.expand(lomc.lex(body("alloc(16) as u64")), body("alloc(16) as u64"))
    assert ok, "在表里的内建应当跑得通"
    for call, why in (("syscall4(60, 0 as u64, 0 as u64, 0 as u64)", "起进程/退出"),
                      ("open(1) as u64", "开文件")):
        txt = body(call)
        try:
            loment_comefor.expand(lomc.lex(txt), txt)
        except lomc.LomError as e:
            assert "nobuiltin" in str(e), (call, e)
            continue
        raise AssertionError(f"{call}（{why}）不该在宏体里可用 —— 它不在内建表里，"
                             f"而这张表**就是**编译期域（`docs/184` §5.1）")
    print("      域 = 内建表: 表外的名字调不到（syscall / 开文件）")


def main() -> int:
    failed: list[str] = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_comefor_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
