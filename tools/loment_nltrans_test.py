#!/usr/bin/env python3
"""loment_nltrans_test.py — **自然语言写法 -> Loment**（`docs/197`）。

## 这一门的判据与前六门**形状不同**，要说清为什么

前六门（C / Python / Java / C# / C++ / Go）判的是

> 翻译出来的 Loment 跑出的数 == **那份源语言自己的编译器**跑出的数

这一门**没有对面那个编译器** —— 它不是别人已有的语言，是我们自己发明的
（`docs/197` §1）。所以"对照组"换成**同一个程序的 Loment 写法**，判据因此是
`docs/188` §7.1.1 那一条的**原话**：

> **两份源码翻出来的 Loment 逐行完全相同** —— 比的是**同一个字符串**，不是"差不多"。

具体到这一门，两份是：

    loment/nltrans/Sample.nl   自然语言写法
    loment/nltrans/Sample.lomt 标准 Loment 写法（同一个程序）

`front_door(Sample.nl)` 出来的字节要**一个不差**地等于 `Sample.lomt` 的字节。

**为什么这比"数相等"更硬**：数相等只说明"两边算到了同一个结果"，而它容许过程里
有一百处拼法漂移；逐字节相等说明**根本没有漂移**。反过来说，`Sample.lomt` 也必须
**自己**能编能跑 —— 否则它可能只是一串"长得像 Loment"的字节，那样逐字节相等就
退化成了"比两份文本"，第三、四条判据钉的就是这一点。

## 期望值是**推出来的，不是抄的**

`WANT_RC` 下面那三行就是推导，与 `Sample.lomt` 头注里那份一致。

用法: python tools/loment_nltrans_test.py   （无 WSL 时真跑那两条 SKIP，退出码 0）
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import nltrans     # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "nltrans"

#: **独立推出来的期望值**（推法与 `Sample.lomt` 头注里那份相同）：
#:   `sum_to(3)`   = 1+2                       = 3
#:   `gcd(48,18)`  = 6
#:   `label(8)`    = 8 是偶数 -> 1
#:   `tally(100)`  = 100 -> 50 -> 25 -> 12 -> 6 -> 3 -> 1 -> 0，共 7 步
#:   `LIMIT * 10`  = 30
#:                                            ----
#:                                            47
WANT_RC = (1 + 2) + 6 + 1 + 7 + (3 * 10)


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- 跑那一侧

def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _run_elf(exe: Path) -> tuple[int, str]:
    r = subprocess.run(
        ["wsl", "-e", "bash", "-lc",
         f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n \";$?\""],
        capture_output=True, text=True, timeout=300, shell=False)
    out, _, rc = r.stdout.rpartition(";")
    return int(rc.strip() or -1), out


def _build(src: Path) -> tuple[Path, str]:
    """参考实现那条路：`load` -> `check` -> LLVM -> ELF。**不碰安装版那个 CLI**。

    刻意绕开 `loment build` 是有理由的：安装版的自举驱动**收不了**
    `choose write grammar natural`（`docs/188` §7.2 那条"别的写法那一半阻塞在翻译器的
    Loment 孪生上"）。它拒得挺清楚（会说"请用参考实现编译"），但那是**另一条判据**的
    对象 —— 这一门现在验的是**前端翻出来的东西对不对**，所以走参考实现。
    """
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    out = Path(tempfile.gettempdir()) / "nltrans_test" / (src.stem + ".elf")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return out, _no_ws(blob)


def _no_ws(blob: bytes) -> str:
    """ELF 里那一串可读的字符串（只在出错时给人看，判据不依赖它）。"""
    return "".join(chr(b) if 32 <= b < 127 else " " for b in blob[-512:])


# ---------------------------------------------------------------- 判据

@test
def test_the_natural_program_runs_to_the_derived_number():
    """**自然语言写法 -> Loment -> 真编真跑**，跑出的数 == 独立推出来的 47。

    这一条里有 `sum_to` / `gcd` / `label` / `tally` 四个函数、`for` 与 `while`
    两种循环、`when`/`otherwise`、比较、取模、整除 —— 任一处翻错都会把 47 改掉。
    另外它还真的从标准输出念了一行（`sample: 47 painted 42`），所以 `say` 那两句
    也是被验过的，不是"编过了就算"。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL 来跑 ELF")
        return
    exe, dump = _build(EX / "Sample.nl")
    rc, out = _run_elf(exe)
    assert rc == WANT_RC, (
        f"跑出来是 {rc}，推出来的期望值是 {WANT_RC}。" + dump)
    assert out == "sample: 47 painted 42\n", f"标准输出不对: {out!r}" + dump
    print(f"      自然语言写法 -> {rc}，标准输出 {out!r}（与推导一致）")


@test
def test_the_loment_twin_runs_to_the_same_number_by_itself():
    """**孪生那一份自己也能跑** —— 否则下一条"逐字节相等"就退化成"比两份文本"。

    这一条刻意**不经过任何翻译器**：`Sample.lomt` 就是一份普通 Loment 源。
    它跑出 47，下一条比的才是"两种拼法给出同一串字节"。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL 来跑 ELF")
        return
    exe, dump = _build(EX / "Sample.lomt")
    rc, out = _run_elf(exe)
    assert rc == WANT_RC, f"孪生那份跑出来是 {rc}，期望 {WANT_RC}。" + dump
    assert out == "sample: 47 painted 42\n", f"标准输出不对: {out!r}" + dump
    print(f"      标准 Loment 写法 -> {rc}（同一个程序，同一个数）")


@test
def test_two_spellings_one_program_translate_the_same():
    """**同一个程序、两种拼法，出来的是同一串字节**（`docs/188` §7.1.1 的原话）。

    比的是 `front_door(Sample.nl)` 的产物与 `Sample.lomt` 的**原始字节** ——
    差一个空格就说明有一条翻译规则顺手改了别的东西。

    `Sample.lomt` 头上那三行注释**是产物的一部分**（`lomt_from.emit_lomt` 生成头），
    所以它们在那份文件里；这不是"测试写死了实现"，而是这一条判据的**对象**就是
    "前端交给编译器的那串字节"。谁改了生成头，这条就红。
    """
    got = potato_from.front_door(EX / "Sample.nl").source
    want = (EX / "Sample.lomt").read_bytes().decode("utf-8")
    if got != want:
        g, w = got.splitlines(), want.splitlines()
        first = next((i for i, (a, b) in enumerate(zip(g, w)) if a != b), min(len(g), len(w)))
        raise AssertionError(
            f"两种拼法翻出来的 Loment 不同（第 {first + 1} 行起）\n"
            f"  自然语言那一侧: {g[first] if first < len(g) else '<没有这一行>'!r}\n"
            f"  Loment 那一侧  : {w[first] if first < len(w) else '<没有这一行>'!r}\n"
            f"  行数 {len(g)} vs {len(w)}")
    print(f"      两种拼法 -> 逐字节同一份 Loment（{len(want.encode())} 字节）")


@test
def test_the_front_door_reads_a_lomt_that_declares_the_grammar():
    """**`choose write grammar natural` 那条路是通的**（`docs/188` §7）。

    这一条与上面那条问的**不是同一件事**：上面是"按后缀 `.nl` 认出来"，这一条是
    "一份 `.lomt` 里声明了自己用哪种写法"。两者的入口不同（`resolve_lang` 与
    `front_door` 的声明那一支），一条判据盖不住两个入口 —— `docs/182` §1.9 的老账。
    """
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "declared.lomt"
        p.write_text("choose write grammar natural\n"
                     + (EX / "Sample.nl").read_bytes().decode("utf-8"),
                     encoding="utf-8", newline="\n")
        u = potato_from.front_door(p)
        assert u.grammar == "natural" and u.translated, (u.grammar, u.translated)
        # 声明那一行**不属于那份源**，所以产物里不许留下它的影子
        assert "choose write grammar" not in u.source, u.source[:200]
        # 而且产物与 `.nl` 那条路**是同一串字节** —— 两个入口不许给出两样东西
        assert u.source == potato_from.front_door(EX / "Sample.nl").source
    print("      声明入口：`choose write grammar natural` 与后缀入口给出同一串字节")


@test
def test_three_spellings_of_the_name_all_mean_natural():
    """`nl` / `natural` / `lument` 三种拼法都收，**进对象的只有一个规范名**。

    `docs/188` §1.2：源侧宽松、对象侧只许一个拼法，否则同一份源出两串字节。
    规范名选 `natural` 而不是 `lument` 是有理由的（`lument` 与 `loment` 只差一个字母，
    而 `grammar loment` 是合法的）—— 这条判据把"三个拼法同一个规范名"钉住。
    """
    for word in ("nl", "natural", "lument"):
        assert potato_from.GRAMMAR_ALIASES[word] == "natural", word
    assert "natural" in __import__("potato").GRAMMARS
    with tempfile.TemporaryDirectory() as t:
        for word in ("nl", "lument"):
            p = Path(t) / f"{word}.lomt"
            p.write_text(f"choose write grammar {word}\n"
                         + (EX / "Sample.nl").read_bytes().decode("utf-8"),
                         encoding="utf-8", newline="\n")
            assert potato_from.front_door(p).source == \
                potato_from.front_door(EX / "Sample.nl").source, word
    print("      `nl` / `natural` / `lument` -> 规范名 `natural`，产物一致")


@test
def test_the_verbs_land_on_the_builtins_they_name():
    """**`say` / `talk` / `paint` 各自落在哪个内建上** —— 用户点名的那三个动词。

    这一条盯的是"降级"那一层：`talk` 与 `paint` 会**补转换**（`as u64` / `as u32` /
    `as u8`），因为 `syscall4` 与 `store8` 的形参就是那些宽度 —— 作者那一句里
    没有写宽度这回事，所以补它是**翻译的一部分**，不是让作者去补。
    """
    t = nltrans.translate(
        "to f with p as a buffer and n as a whole number\n"
        "    say \"hi\"\n"
        "    say the number n\n"
        "    talk to the machine 60 with n, 0, 0\n"
        "    paint 7 at n in p\n"
        "end\n")
    assert 'syscall4(1, 1, str_ptr("hi") as u64, str_len("hi") as u64);' in t, t
    assert "nl_write_num(1, n as i64);" in t, t
    assert "syscall4(60 as u64, n as u64, 0 as u64, 0 as u64);" in t, t
    assert "store8(p, n as u32, 7 as u8);" in t, t
    # `say the number` 那个辅助函数**只在用到时才发** —— 没用到就不该进产物
    assert "fn nl_write_num" in t
    without = nltrans.translate(
        "to g with s as text\n    say s\nend\n")
    assert "nl_write_num" not in without, without
    print("      say -> write / say the number -> nl_write_num / talk -> syscall4 / "
          "paint -> store8（各补各的宽度）")


@test
def test_word_operators_and_symbol_operators_are_the_same_token():
    """**词形与符号形是同一件事**（`plus` ≡ `+`，`is above` ≡ `>`）。

    这是"拼法可以有别名"那条纪律在**运算符**上的落实。两边翻出来必须**逐字节相同**，
    否则同一句话就有了两种意思。
    """
    words = nltrans.translate(
        "to f with a as a whole number and b as a whole number giving a whole number\n"
        "    when a is above b and a is not 0\n"
        "        give back a times 2 plus b minus 1\n"
        "    end\n"
        "    give back a modulo b over 2\n"
        "end\n")
    syms = nltrans.translate(
        "to f with a as a whole number and b as a whole number giving a whole number\n"
        "    when a > b && a != 0\n"
        "        give back a * 2 + b - 1\n"
        "    end\n"
        "    give back a % b / 2\n"
        "end\n")
    assert words == syms, f"词形与符号形翻出来不同:\n{words}\n---\n{syms}"
    print("      词形与符号形：同一句话、同一串字节")


@test
def test_a_call_with_no_arguments_is_just_its_name():
    """**没有实参的调用就写名字本身**（`say the number run`）。

    分辨靠**声明表**（两遍读出来的）：名字是个函数、又不是局部量 ⇒ 它是调用。
    这一条同时钉住"局部量优先"，否则一个与函数同名的变量会被悄悄改成调用。
    """
    t = nltrans.translate(
        "to called giving a whole number\n"
        "    give back 42\n"
        "end\n"
        "to f giving a whole number\n"
        "    let other be called\n"
        "    give back other plus called\n"
        "end\n"
        "to g giving a whole number\n"
        "    let called be 7\n"
        "    give back called\n"
        "end\n")
    # `called` 是函数 ⇒ 两处都是**调用**（`let` 的类型也是从声明表查出来的）
    assert "let other: i64 = called();" in t, t
    assert "return (other + called());" in t, t
    # 而在 `g` 里 `called` 被一个局部量遮住 ⇒ 那两句是**变量**（局部量优先）
    assert "let called: i64 = 7;" in t, t
    assert "return called;" in t, t
    print("      无参调用写名字；与局部量同名时以局部量为准")


@test
def test_out_of_subset_is_loud_and_points_at_the_line():
    """**读得通但这一版不翻**的，一条都不许沉默过去，而且**行号要指回原文件**。

    最后两例是 `docs/188` §7.2 那条老账：正文是**拼起来**再交给翻译器的，拼接不带
    行偏移的话，报的行号是"按函数体"算的 —— 用户在源码里按它**找不到东西**
    （实测那边报的是第 2 行，而那一句在文件第 3 行）。
    """
    cases = [
        ("program p\n\nuse json\n", "还不收"),
        ("program p\n\nto f with a as a myst\n    give back a\nend\n", "类型短语"),
        ("program p\n\nto f giving a whole number\n"
         "    give back nope of 1\nend\n", "没有这个函数"),
        ("program p\n\nto f\n    set x to 1\nend\n", "没有声明过"),
        ("program p\n\nto f\n    talk to the machine 60 with 1, 2\nend\n", "三个"),
        ("program p\n\nto f\n    let x be a plus b\nend\n", "说不出"),
        # 调用一个 `giving` 没写的函数：**读得通、却不是一个值** —— 拦在这里，
        # 不让它变成"编译到一半报类型 `()`"那种隔着一层的错
        ("program p\n\nto g\nend\n\nto f\n    let x be g\nend\n", "不返回值"),
        ("program p\n\nto f\n    when 1\nend\n", "end"),
        ("program p\n\nto f\n    give back 1\n", "没有 `end`"),
        ("program p\n\nto f\n    match x\nend\n", "不认识的句子"),
        ("program p\n\nto f\n    to g\n    end\nend\n", "顶层"),
    ]
    for src, want in cases:
        try:
            nltrans.translate(src)
        except (nltrans.Unsupported, nltrans.NaturalError) as e:
            assert want in str(e), f"要点名 `{want}`：{e}"
        else:
            raise AssertionError(f"{src!r} 在子集外，却一个字都没报（该点 `{want}`）")
    print(f"      {len(cases)} 档子集外各报各的，都点到了点子上")


@test
def test_line_number_of_a_defect_is_the_file_line_not_the_body_line():
    """**正文拼起来之后，行号仍然指回原文件**（`docs/188` §7.2 那条老账）。

    `lomt_from` 是把各函数**正文拼成一份临时源**再交给翻译器的（`_join_bodies`），
    所以翻译器数出来的行号天然是"按正文"算的。修法是拿 `body_line` 垫空行 ——
    这条判据钉的就是那一垫。

    **夹具自证能分辨两种读法**：缺陷在文件第 6 行，而按"只算这一个函数体"是第 4 行。
    两个数**必须不同**，否则这条判据测的是一个"两种读法都对"的输入。
    """
    src = ("program p\n"                            # 1
           "\n"                                     # 2
           "to f with n as a whole number\n"        # 3  <- 函数体从这里开始
           "    let x be n\n"                       # 4  (= 体内第 2 行)
           "    let z be n\n"                       # 5
           "    set y to n\n"                       # 6  <- 缺陷在这里 (= 体内第 4 行)
           "    give back x plus z\n"               # 7
           "end\n")                                 # 8
    want_line = 6
    body_line = 4
    assert want_line != body_line, "夹具分辨不了两种读法"
    assert src.splitlines()[want_line - 1].strip().startswith("set y"), "夹具自己错了"

    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "defect.nl"
        p.write_text(src, encoding="utf-8", newline="\n")
        try:
            potato_from.front_door(p)
        except Exception as e:  # noqa: BLE001 - 具体类型下面再断
            msg = str(e)
        else:
            raise AssertionError("这一份本该被拒")
    assert "set y" in msg or "没有声明过" in msg, f"拒得不是那一处: {msg}"
    assert f"第 {want_line} 行" in msg, (
        f"报的是**正文里**的行号，不是文件里的（`body_line` 那一步漏了？）: {msg}")
    assert f"第 {body_line} 行" not in msg, f"报成了按函数体算的那一行: {msg}"
    print(f"      缺陷在文件第 {want_line} 行，报出来的也是 {want_line} "
          f"（按函数体算是 {body_line}，两个数不同）")


@test
def test_content_detection_needs_both_signals():
    """**内容识别要两条一起**（`program <名字>` 整行 + `give back`）—— 认不出比认错好。

    这一门的句子全是日常英文词，单看某一个词会与别的语言的注释/标识符撞上。
    所以判据是"两条齐了才认"，代价是"只有一条时不认"，而那条代价是**故意的**：
    认错的症状是"拿一份 C 按自然语言去读"，比认不出坏得多（`docs/188` §2 那条兜底
    从"猜"变"拒绝"）。
    """
    both = "program demo\n\nto f\n    give back 1\nend\n"
    lang, why = potato_from.detect_lang(both)
    assert lang == "natural", (lang, why)
    # 只有 `program` 那一行：**不认**（这多半是别的什么语言里一个叫 program 的东西）
    only_one = "program demo\n\nint f(void) { return 1; }\n"
    lang2, _ = potato_from.detect_lang(only_one)
    assert lang2 != "natural", f"只有一条信号就认了: {lang2}"
    print("      两条齐了才认自然语言写法；只有一条不认")


@test
def test_a_dot_nl_file_is_read_by_extension():
    """后缀那一格也是**登记的**（`EXT[".nl"] == "natural"`），与 `LANGS` 对得上。

    `docs/188` §7.1 那张登记表里"不加会怎样"最隐晦的一格就是这张表 ——
    它是 `lomt_from._TOOLS` 与报错器那几张卡共同的对齐点。
    """
    assert potato_from.EXT[".nl"] == "natural"
    assert "natural" in potato_from.LANGS
    import lomt_from
    mod, errs, tool, hint = lomt_from._TOOLS["natural"]
    assert mod == "nltrans" and tool == "tools/nltrans.py"
    assert "NaturalError" in errs
    import loment_diag
    card = loment_diag.LANG_CARDS["natural"]
    assert card.exts == (".nl",) and card.key == "natural"
    assert "natural" in loment_diag.LANG_EDGE_EN
    assert "natural" in loment_diag.LANG_ABI_EN
    print("      后缀表 / LANGS / _TOOLS / 语言卡 四处对齐")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_nltrans_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
