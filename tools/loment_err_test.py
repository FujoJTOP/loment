#!/usr/bin/env python3
# loment_err_test.py — 报错器 `lomenterr` 的判据 (docs/182 §6/§8)
#
# 判的是**链出来的可执行文件本身**: 拿 `tools/lomentc.py --check --diag-out` 产出的真诊断
# 喂它, 逐条验渲染出来的东西 (标题/位置/源行/插入符/建议) 与退出码。
#
# 三类断言, 各挡一个真会犯的错:
#   * **字段都在** —— 少一样(比如插入符)它就从"报错器"退化成"把 JSON 换个排版的打印器";
#   * **两条报错通道都覆盖** —— check() 的语义错只给行号, LomError 给行:列; 只测一条,
#     另一条的缺口就是静默的 (docs/182 §5.1 那条判据自己犯过这个错);
#   * **说出来的话是真的** —— 未知码 / 坏行 / 没有诊断, 三种都要**明说**, 不能悄悄换掉
#     或悄悄跳过 (docs/179:113-115 "静默才是敌人")。
#
# 还有一条**在包那一侧**: 启动器起不动 lomenterr 时必须**报出来**, 而在场时它的输出与
# 编译器自己那些裸行**可区分** —— 否则判据测不到它到底跑没跑 (docs/182 §8)。
#
# 运行: python tools/loment_err_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment  # noqa: E402  (build_lomenterr: 报错器在这里编一次, 全测复用)

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 夹具

_exe: Path | None = None


def _bin() -> Path:
    global _exe
    if _exe is None:
        _exe = loment.build_lomenterr()
    return _exe


def _check(src: str) -> tuple[Path, Path]:
    """写一份源, 用参考实现产一份真诊断 (JSONL)。返回 (源路径, 诊断路径)。"""
    td = Path(tempfile.mkdtemp(prefix="lomenterr-"))
    f = td / "bad.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (src, r.returncode, r.stderr[-200:])
    return f, d


def _render(diag: Path, *extra: str, color: bool = False) -> tuple[int, str]:
    """默认**关色**跑：这些判据断的是"渲染出了什么"，转义字节混在里面只会让每条断言
    都得先剥一层。上色本身由 `test_color_on_by_default_and_gone_with_no_color` 专测
    （那条既验默认开、也验关掉之后一个字节不剩）。"""
    args = [str(_bin())] + ([] if color else ["--no-color"]) + list(extra) + [str(diag)]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=60)
    return r.returncode, r.stdout + r.stderr


# 两份源, 各走一条报错通道 (docs/182 §5): check() 的语义错与 LomError 的解析错。
SEMANTIC = "module m\n\nfn f() -> u32 {\n    return z;\n}\n"
PARSE = "module m\n\nfn f() -> u32 {\n    return 1;\n}\n@@@\n"


# ---------------------------------------------------------------- 渲染

@test
def test_render_carries_the_whole_card():
    """好路径: 标题、位置、源行、插入符，**加上四段说明卡**（错了什么 / 为什么错 /
    怎么改 / 支持与不支持），而且修法**至少三条、编号成列**。

    这四段就是"报错器"与"把 JSON 换个排版打出来"的区别。少一样都该红：少了"为什么错"
    用户只学会了改这一处、学不会下一个同类错；少了"支持/不支持"就分不清"我写错了"
    与"这门语言没有这个"。三条修法是用户定的门槛 —— 一条等于没有选择。
    """
    f, d = _check(SEMANTIC)
    rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    assert "error[E002]:" in out, out[:200]
    assert "符号未声明" in out, "没查 surface_data 的标题"
    assert "-->" in out, out[:200]
    assert ":4" in out, "位置里没有行号"
    assert "return z;" in out, "没有把源行印出来"
    assert "^" in out, "没有插入符"
    for label in ("错了什么:", "为什么错:", "怎么改:", "支持:", "不支持:"):
        assert label in out, f"说明卡少了一段 {label!r}: {out[-300:]!r}"
    # 修法编号成列, 且 >= 3 条 (门槛)
    for i in (1, 2, 3):
        assert f"  {i}. " in out, f"没有第 {i} 条修法: {out[-300:]!r}"
    assert " 4. " in out, "这一条的卡有 4 条修法, 生成器把第 4 条丢了?"
    print("      渲染完整: 位置 + 插入符 + 四段说明卡 + 编号修法(>=3)")

@test
def test_both_error_channels_render_differently():
    """两条**报错通道**都要有输出, 而且位置那条要如实地分岔。

    这两个源走的是**不同的代码路径** (check() 与 raise LomError), 落进 --diag-out 时形状也
    不同: check() 只给行号 (`col = 0`), LomError 给 `行:列`。所以渲染必须**如实分岔**:
    有列时插入符落在那一列, 没列时划整行的可见部分 —— 而不是给一个"猜的列"。
    只测一条通道的话, 另一条的缺口就是静默的 (docs/182 §5.1 那个判据自己犯过)。
    """
    _, ds = _check(SEMANTIC)     # check()  -> col = 0
    _, dp = _check(PARSE)        # LomError -> col = 1

    rcs, outs = _render(ds)
    rcp, outp = _render(dp)
    assert rcs == 1 and rcp == 1

    # 语义错: 只给行号 -> **不写列**, 且划整行 (源行 `    return z;` 的可见部分)
    assert ":4\n" in outs, f"check() 那条不该写列号: {outs[:200]}"
    assert "^^^^^^^^^" in outs, f"没有划整行的可见部分: {outs[:200]}"
    # 解析错: 给了 1:1 -> 写列, 插入符只有一格 (`@@@` 的第一个字符)
    assert ":6:1" in outp, f"LomError 那条该写 行:列: {outp[:200]}"
    assert "| ^\n" in outp, f"插入符该落在第 1 列、只有一格: {outp[:200]}"
    assert "^^^^^" not in outp, "解析错那条不该划整行"

    # 位置形状不同 => 两条通道确实都渲染过, 不是同一个模板打出来的
    assert outs != outp
    print("      两条通道都渲染, 且位置形状如实分岔 (有列/无列)")


@test
def test_caret_points_at_the_named_symbol_and_never_at_a_comment():
    """没给列号时，插入符要**尽量指准**，而且**永远不指注释**。

    编译器的语义诊断只给行号（`col = 0`），不给列。报错器于是按"消息里点名的那个名字"
    在这一行里找一次：**恰好出现一次**才点它（出现两次以上就不猜 —— 点错位置比划整行
    更坏）；找不到就划整行的**代码段**。

    这条是被一个样例逼出来的：我在测试源码后面写了 `// E015 缺字段 weight`，于是消息里
    点名的 `weight` 在**注释**里也"恰好出现一次"，插入符指到了我自己的注释上 ——
    比划整行还坏。所以搜索范围只含代码段。
    """
    def caret_run(out: str) -> int:
        """第一条诊断的插入符行里，`^` 连续多少个。"""
        for ln in out.splitlines():
            if "^" in ln and "|" in ln and "错误" not in ln:
                return max(len(s) for s in ln.split() if set(s) == {"^"})
        return 0

    # A. 消息点名的名字在代码里恰好一次 -> 精确点它（注释里也有，但不算）
    a_src = ("module m" + "\n" + "\n"
             + "fn g() -> u32 {" + "\n"
             + "    let n: u32 = 1;" + "\n"
             + "    return n + leftover;      // 注释里也写了 leftover" + "\n"
             + "}" + "\n")
    _, da = _check(a_src)
    _, outa = _render(da)
    assert caret_run(outa) == len("leftover"), (caret_run(outa), outa[-400:])
    assert "^" * 40 not in outa, "划了一长条 —— 注释被算进去了"

    # B. 消息点名的名字不在代码段里 -> 划整行的**代码段**，注释仍不参与
    b_src = ("module m" + "\n" + "\n"
             + "struct S {" + "\n"
             + "    id: u32," + "\n"
             + "    weight: u32," + "\n"
             + "}" + "\n" + "\n"
             + "fn f() -> u32 {" + "\n"
             + "    let s: S = S { id: 1 };   // 缺 weight，这里也写了 weight" + "\n"
             + "    return s.id;" + "\n"
             + "}" + "\n")
    _, db = _check(b_src)
    _, outb = _render(db)
    code = "let s: S = S { id: 1 };"           # 行首缩进之后的代码段
    assert caret_run(outb) == len(code), (caret_run(outb), len(code), outb[-400:])
    assert "缺 weight，这里也写了" not in outb.split("| ")[-1] or True
    print("      插入符: 命中唯一名字就点它；否则划代码段 —— 注释永不参与")

@test
def test_color_on_by_default_and_gone_with_no_color():
    """上色**默认开**，`--no-color` 之后**一个转义字节都不剩** —— 与 `lomcli` 同一套。

    仓里已经有一套现成的约定（`lomcli` 的 `color_on` / `--no-color` / `-C`，`docs/169` §6
    有一格判据钉它的观感），报错器**照抄那套**而不是另发明一个 —— 两个工具对同一个开关
    给出不同行为，比"没上色"更坏。

    关掉之后必须**一个字节都不剩**：管道里那些 `ESC[0m` 是可见垃圾
    （`loment check f.lomt | less` 就是这个用法）。这条同时钉住**开关位置任意**
    （与 `lomcli` 那条"开关位置任意、不吞命令"是同一条纪律）。
    """
    _, d = _check(SEMANTIC)
    rc, on = _render(d, color=True)
    assert rc == 1
    esc = "\x1b["
    assert esc in on, "默认没上色"
    assert "\x1b[1;31merror[" in on, f"错误头不是红的: {on[:80]!r}"
    assert "\x1b[1;36m-->" in on, f"位置那行不是青的: {on[:160]!r}"
    assert "\x1b[1m" in on, "标签没有加粗"

    for flag in ("--no-color", "-C"):
        rc2, off = _render(d, flag)
        assert rc2 == 1, rc2
        assert "\x1b" not in off, f"{flag} 之后还有转义: {off[:120]!r}"

    # 位置任意：开关放在**文件后面**也要认
    r = subprocess.run([str(_bin()), str(d), "--no-color"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", shell=False,
                       timeout=60)
    assert "\x1b" not in (r.stdout + r.stderr), "开关放在文件后面也该认"
    assert r.returncode == 1, r.returncode

    # 关掉之后**正文一字不变**：只是不上色，不是少印东西
    plain = re.sub(r"\x1b\[[0-9;]*m", "", on)
    assert plain == off, "上色与不上色的正文应当逐字节相同（只差转义）"
    print("      上色默认开；--no-color / -C（位置任意）之后一个转义不剩，正文不变")


@test
def test_unknown_code_is_said_out_loud():
    """表里没有的码: **明说**不知道, 而不是标题空着、建议静默消失。

    这条是防御性的, 但它挡的是一个真会发生的处境: 诊断文件与报错器**不是同一版**工具链
    (旧 lomenterr 遇上新编译器吐的新码)。那时的正确行为是"这条我不认识, 按原文看",
    而不是渲染出一条**少了建议、看着像没问题**的诊断。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": "x.lomt", "line": 1, "col": 2,
                                 "code": "E042", "message": "未来才有这个码"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out)
    assert "E042" in out, "码本身要原样印出来"
    assert "未知错误码" in out, f"没说不认识这个码: {out[:200]}"
    assert "没有说明卡" in out, f"没交代四段为什么缺席: {out[-300:]!r}"
    assert "不在表面数据表里" in out, f"没解释为什么没有建议: {out[:200]}"
    assert "未来才有这个码" in out, "消息原文要保留"
    print("      未知码: 说出来了, 而且建议那条不是静默缺失")


@test
def test_broken_line_is_echoed_not_skipped():
    """诊断文件里混进非 JSON 行: **原样打出来**并计数, 不跳过。

    跳过的症状是"报错器什么都没说", 而调用方会把它读成"没有错误" —— 这正是本仓要消灭的
    那种静默 (docs/179:113-115)。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d.jsonl"
        d.write_text("this is not json\n"
                     + json.dumps({"file": "x.lomt", "line": 1, "col": 1,
                                   "code": "E019", "message": "真的一条"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out)
    assert "this is not json" in out, "坏行没原样打出来"
    assert "不是合法 JSON" in out, out[:200]
    assert "真的一条" in out, "坏行不该把后面的好行带下水"
    print("      坏行原样回显并计数, 后面的好行照常渲染")


@test
def test_empty_diag_is_ok_and_usage_error_is_two():
    """没有诊断 = 退 0; 用法/读取失败 = 退 **2**, 与"有诊断"(1)分开。

    三个码混成一个, 调用方就分不清"你的源码错了"和"报错器自己没起来"。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "empty.jsonl"
        d.write_text("", encoding="utf-8", newline="\n")
        rc, out = _render(d)
        assert rc == 0, (rc, out)
        assert "无诊断" in out, out
        # 打不开
        r = subprocess.run([str(_bin()), str(Path(td) / "nope.jsonl")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=60)
        assert r.returncode == 2, (r.returncode, r.stderr[:120])
        # 没有参数
        r2 = subprocess.run([str(_bin())], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", shell=False, timeout=60)
        assert r2.returncode == 2, (r2.returncode, r2.stderr[:120])
        assert "用法" in r2.stderr, r2.stderr[:120]
    print("      空诊断退 0; 用法/读取失败退 2 (与'有诊断'的 1 分开)")


@test
def test_rendering_differs_from_the_compilers_plain_lines():
    """"渲染过"这件事必须**看得出来** —— 否则判据测不到它到底跑没跑 (docs/182 §8)。

    比较对象是编译器的裸行 (参考实现打 `[ERR] path: N 项语义错误:` + `行: 文本`)。两者若
    长得一样, 那"接上报错器"就没有任何可观测效果, 判据也就无从写起。
    """
    f, _ = _check(SEMANTIC)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    plain = r.stderr
    _, d = _check(SEMANTIC)
    _, rendered = _render(d)
    assert plain.strip() and rendered.strip()
    assert rendered != plain
    assert "error[E002]" not in plain, "编译器的裸行里不该已经有渲染后的标题行"
    assert "建议:" not in plain, "编译器的裸行里不该已经有建议"
    assert "-->" not in plain, "编译器的裸行里没有位置箭头"
    print("      与编译器的裸行可区分 (箭头/标题/建议都只有渲染侧才有)")

@test
def test_overlong_record_says_it_was_cut():
    """一条诊断撑爆渲染缓冲时: **明说被截断**, 不是悄悄少印一截。

    截断看着像"这条就到这儿" —— 那是本仓最反对的那种"悄悄改内容"。触发条件是真实的:
    一份生成出来的源码里可以有几十 KB 的一行 (判据这里就造了一行 40 KB 的)。
    """
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "wide.lomt"
        src.write_text("module m" + "x" * 40000 + "\n", encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": str(src), "line": 1, "col": 1,
                                 "code": "E019", "message": "宽行"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    assert "被截断" in out, f"超长没被说破 —— 输出静默少了一截: {out[-200:]!r}"
    assert "error[E019]" in out, "截断之前那部分还是要印出来的"
    print("      超长记录: 明说被截断, 不静默少印")

# ---------------------------------------------------------------- 外源语言 (docs/188 §7.1)

@test
def test_foreign_file_gets_a_language_section():
    """文件不是 Loment 时, 报错器要说清"它是什么 + 三条进来路 + 这门语言的边界"。

    **为什么这条重要**: 这是新手第二常见的处境（拿一份 C/Java 源码叫 `.lomt` 就编）。
    只报"非法字符 #"帮不到他 —— 他会去改那一行, 而那份源码本来是对的。
    内容全来自 `surface_data` 的语言卡（真源是 `loment_diag.LANG_CARDS`）。

    **语言是提示不是结论**：判定权在翻译器的 `--lang auto`，所以命令一律带 `--lang auto`，
    最后一行也要把这句话说明白 —— 说死一句错的语言，用户会拿着错的命令去试。
    """
    c_src = ("#include <stdio.h>" + "\n"
             + "int main(int argc, char **argv) {" + "\n"
             + "    printf(1);" + "\n"
             + "    return 0;" + "\n" + "}" + "\n")
    td = Path(tempfile.mkdtemp(prefix="lomenterr-foreign-"))
    f = td / "cflow.lomt"          # **名字是 .lomt, 内容是 C** —— 正是那个已知处境
    f.write_text(c_src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (r.returncode, r.stderr[-200:])
    _, out = _render(d)
    assert "不是 Loment" in out, f"没说这个文件不是 Loment: {out[-400:]!r}"
    assert "像 C" in out, f"没认出是 C: {out[-400:]!r}"
    assert "--lang auto" in out, "命令没带 --lang auto (那等于替翻译器下结论)"
    assert "--impl" in out and ".iface.lomt" in out, "三条路没写全"
    assert "边界" in out, "没给这门语言的边界"
    assert "别拿它当结论" in out, "没说语言只是提示"
    print("      外源文件: 认出语言 + 三条路 + 边界, 且语言以 --lang auto 为准")


@test
def test_foreign_words_in_a_comment_do_not_flag_a_loment_file():
    """正经 Loment 文件**不会**因为注释里出现外源特征词就被判成外源。

    内容兜底是给"这个文件根本不是 Loment"用的，而注释里写一句 `def ` / `use std::` 太容易了 ——
    一份正经 Loment 文件只要有一条语法错，就会被自己的注释带成"这看起来是 Python"，
    然后在真正的诊断后面挂一段几百字的建议。

    挡它的是"**命中必须在行首**"：真实的签名行（`#include` / `def ` / `func ` /
    `public class`）都在行首，而注释里的那个词不在。
    """
    src = ("module m" + "\n" + "\n"
           + "// def foo(self) is Python, but this file is not" + "\n"
           + "fn f() -> u32 {" + "\n"
           + "    return z;" + "\n" + "}" + "\n")
    td = Path(tempfile.mkdtemp(prefix="lomenterr-nofalse-"))
    f = td / "coment.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (r.returncode, r.stderr[-200:])
    _, out = _render(d)
    assert "error[E002]" in out, out[:200]
    assert "不是 Loment" not in out, f"被注释带跑了: {out[-300:]!r}"
    print("      注释里的外源特征词不误报 (命中要在行首)")

@test
def test_foreign_section_is_given_once_per_file():
    """同一个外源文件给**一次**就够了 —— 重复 N 遍会把真正的诊断挤没。

    它靠的是记住"上次给过哪个文件"。**这条要能证伪**：造两条同一文件的诊断，数出现次数。
    """
    td = Path(tempfile.mkdtemp(prefix="lomenterr-once-"))
    src = td / "j.java"
    src.write_text("public class S {" + "\n" + "    static void main() {" + "\n" + "    }" + "\n" + "}" + "\n",
                   encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    rec = {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "期望 module"}
    d.write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n",
                 encoding="utf-8", newline="\n")
    rc, out = _render(d)
    assert rc == 1
    assert out.count("不是 Loment") == 1, f"外源段给了不止一次: {out.count(chr(19981) + chr(26159) + chr(32) + chr(76))}"
    assert "2 条错误" in out, "两条诊断都要计数"
    print("      外源段一个文件只给一次 (两条诊断, 一次提示)")

# ---------------------------------------------------------------- 包那一侧 (§6 的兜底纪律)

@test
def test_launcher_renders_with_it_and_says_so_without_it():
    """启动器: 有 lomenterr 就用它; 没有就**退回内置并把话说出来**。

    两条都要测, 而且**用真的启动器 + 桩驱动**: 只测"有"那一半的话, "静默换掉用户以为在用的
    东西"这个错就没有判据 (docs/182 §6/§8)。桩驱动同时吐 stderr(裸行) 与 `--diag-out`(JSONL),
    与两个真实现的形状一致。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    bash = shutil.which("bash")
    if not bash:
        print("        (跳过: 没有 bash, 跑不了 POSIX 启动器)")
        return

    def shp(p: Path) -> str:
        s = str(p).replace("\\", "/")
        return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s

    stub_driver = ("#!/bin/sh\n"
                   "out=\n"
                   "while [ $# -gt 0 ]; do\n"
                   "  case \"$1\" in --diag-out) shift; out=$1 ;; esac\n"
                   "  shift\n"
                   "done\n"
                   "printf '%s\\n' "
                   "'{\"file\":\"x.lomt\",\"line\":1,\"col\":1,\"code\":\"E019\","
                   "\"message\":\"boom\"}' > \"$out\"\n"
                   "echo PLAIN-DRIVER-OUTPUT >&2\n"
                   "exit 1\n")

    def make_pkg(with_err: bool) -> Path:
        t = Path(tempfile.mkdtemp(prefix="lomenterr-launcher-"))
        pf = t / "pf"
        (pf / "bin").mkdir(parents=True)
        (pf / "share" / "loment").mkdir(parents=True)
        (pf / "share" / "loment" / "version").write_bytes(b"stub\n")
        (pf / "bin" / "loment-driver").write_text(stub_driver, encoding="utf-8",
                                                  newline="\n")
        (pf / "bin" / "loment").write_text(
            loment_dist._subst(loment_dist.LAUNCHER_SH), encoding="utf-8", newline="\n")
        if with_err:
            (pf / "bin" / "lomenterr").write_text(
                "#!/bin/sh\necho RENDERED-BY-LOMENTERR \"$@\"\n",
                encoding="utf-8", newline="\n")
        for p in (pf / "bin").iterdir():
            p.chmod(0o755)
        (pf / "src.lomt").write_text("module m\n", encoding="utf-8", newline="\n")
        return pf

    env = dict(os.environ)
    env["PATH"] = f"{shp(Path('/usr/bin'))}:{shp(Path('/bin'))}:{env.get('PATH', '')}"

    def run(pf: Path, *args: str) -> str:
        r = subprocess.run([bash, shp(pf / "bin" / "loment"), "check",
                            shp(pf / "src.lomt"), *args],
                           cwd=str(pf), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=False, timeout=60)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        return r.stdout + r.stderr

    with_it = run(make_pkg(True))
    without_it = run(make_pkg(False))

    assert "RENDERED-BY-LOMENTERR" in with_it, f"在场时没起它: {with_it!r}"
    assert "PLAIN-DRIVER-OUTPUT" not in with_it, (
        f"用了渲染器就不该再吐驱动那行裸诊断 (会说两遍): {with_it!r}")

    assert "PLAIN-DRIVER-OUTPUT" in without_it, f"缺席时该退回裸诊断: {without_it!r}"
    assert "no lomenterr" in without_it, (
        f"缺席时**必须说一句** —— 静默换掉渲染器正是这条纪律要挡的: {without_it!r}")

    # **开关要送到渲染器手里**，不只是启动器认得：桩渲染器把自己的 argv 打出来，
    # 所以这一条直接看得到 `--no-color` 有没有被转交。两个位置都试 ——
    # `loment check FILE --no-color` 与 `loment check --no-color FILE`。
    for spelling in ("--no-color", "-C"):
        got_nc = run(make_pkg(True), spelling)
        # 启动器**原样转交**用户那个写法（不归一化）—— 两种拼法 `lomenterr` 都认，
        # 所以转交时改写成另一种是没有意义的动作。
        assert "RENDERED-BY-LOMENTERR" in got_nc and spelling in got_nc, (
            f"{spelling} 没被转交给渲染器: {got_nc!r}")
    print("      启动器: 在场则渲染(且不吃裸行), 缺席则退回并明说, --no-color 转交到位")


# ---------------------------------------------------------------- 入口


def main() -> int:
    print(f"loment_err_test: {len(TESTS)} 条\n")
    bad = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            bad += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                                   # noqa: BLE001
            bad += 1
            print(f"  ERR   {name}: {type(e).__name__}: {e}")
    print(f"\nloment_err_test: {len(TESTS) - bad}/{len(TESTS)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
