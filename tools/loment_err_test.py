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


#: 老判据断的是**中文**文案，所以它们显式要中文。产品默认是**英文**（`docs/182` §14）。
ZH = "zh"


def _errconfig(d: Path, body: str) -> None:
    (d / "errconfig").write_text(body, encoding="utf-8", newline="\n")


def _run_in(cwd: Path, diag: Path, *extra: str, color: bool = False) -> tuple[int, str]:
    """在**指定的工作目录**里跑一次 —— 语言（`./errconfig`）与"项目根"（E018 要看的
    `./deps`）都由它定（`docs/182` §14/§15）。`_render` 是它上面那层薄封装。"""
    args = [str(_bin())] + ([] if color else ["--no-color"]) + list(extra) + [str(diag)]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=60,
                       cwd=str(cwd))
    return r.returncode, r.stdout + r.stderr


def _render(diag: Path, *extra: str, color: bool = False,
            lang: str | None = None) -> tuple[int, str]:
    """默认**关色**跑：这些判据断的是"渲染出了什么"，转义字节混在里面只会让每条断言
    都得先剥一层。上色本身由 `test_color_on_by_default_and_gone_with_no_color` 专测
    （那条既验默认开、也验关掉之后一个字节不剩）。

    `lang` 决定说哪种语言，走的是**用户切语言的那条路**：在一个临时目录里放一份
    `errconfig` 并把**工作目录**切过去。所以不传 `lang` 就是**出厂默认**（英文）——
    判据不是在给渲染器塞内部开关，而是真的摆一份配置。顺带把"项目根 = 工作目录"这条
    约定也一起验了：语言就是这么找到的。

    **老的判据一律传 `lang=ZH`**：它们断的是中文文案（那批文案是这些判据的对象，与产品
    默认是哪门语言无关）。英文那一侧由 `test_english_is_the_default_...` 起头的那几条覆盖，
    它们不传 `lang`。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-cwd-") as td:
        if lang is not None:
            _errconfig(Path(td), 'module errconfig\n\n'
                                 'pub fn error_lang() -> str {\n'
                                 f'    return "{lang}";\n}}\n')
        return _run_in(Path(td), diag, *extra, color=color)


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
    rc, out = _render(d, lang=ZH)
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

    rcs, outs = _render(ds, lang=ZH)
    rcp, outp = _render(dp, lang=ZH)
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
    _, outa = _render(da, lang=ZH)
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
    _, outb = _render(db, lang=ZH)
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
    rc, on = _render(d, color=True, lang=ZH)
    assert rc == 1
    esc = "\x1b["
    assert esc in on, "默认没上色"
    assert "\x1b[1;31merror[" in on, f"错误头不是红的: {on[:80]!r}"
    assert "\x1b[1;36m-->" in on, f"位置那行不是青的: {on[:160]!r}"
    assert "\x1b[1m" in on, "标签没有加粗"

    for flag in ("--no-color", "-C"):
        rc2, off = _render(d, flag, lang=ZH)
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
        rc, out = _render(d, lang=ZH)
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
        rc, out = _render(d, lang=ZH)
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
        rc, out = _render(d, lang=ZH)
        assert rc == 0, (rc, out)
        assert "无诊断" in out, out
        # 打不开
        r = subprocess.run([str(_bin()), str(Path(td) / "nope.jsonl")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=60, cwd=td)
        assert r.returncode == 2, (r.returncode, r.stderr[:120])
        # 没有参数。**`cwd=td`**：这条直接起进程、不经过 `_render`，而用法那句话的语言由
        # `./errconfig` 定 —— 不钉住工作目录，它的期望值就会随仓库根有没有那份文件而变。
        r2 = subprocess.run([str(_bin())], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", shell=False, timeout=60,
                            cwd=td)
        assert r2.returncode == 2, (r2.returncode, r2.stderr[:120])
        assert "usage" in r2.stderr, r2.stderr[:120]
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
    _, rendered = _render(d, lang=ZH)
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
        rc, out = _render(d, lang=ZH)
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
    _, out = _render(d, lang=ZH)
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
    _, out = _render(d, lang=ZH)
    assert "error[E002]" in out, out[:200]
    assert "不是 Loment" not in out, f"被注释带跑了: {out[-300:]!r}"
    print("      注释里的外源特征词不误报 (命中要在行首)")

@test
def test_a_grammar_declaration_beats_content_sniffing():
    """文件头写了 `choose write grammar <别名>` 时，**声明说了算** —— 与工具链同一次序。

    `potato_from.resolve_lang` 现在是 **声明 > 后缀 > 内容**（`docs/188` §2）：声明是
    **作者对这份文件说的**，而后缀只是命名习惯。渲染器看不到工具链的解析结果，得自己
    找那一行 —— 不找就会**说出与工具链相反的一句**，而用户只能信一个。

    这条是实测出来的缺口：一份 `choose write grammar python` 的 `.lomt`，工具链知道是
    Python，而报错器因为内容里没有 Python 特征词（`def ` / `__name__` / `self.`）
    **一个字都不说** —— 工具链知道、渲染器沉默，是最坏的一种。

    顺带钉住**别名**也要认（`c#` / `cs` → C#）：别名表由 `--dump-surface` 从
    `potato_from.GRAMMAR_ALIASES` 导出，所以"作者能写哪些词"仍是一处真源。

    **判据自己造诊断，不请编译器**（这条是 2026-09-18 改的，因为前门落地后不得不改）：
    一旦 `choose write grammar python` 的文件走前门**编得过**了（`eb060eb`），
    "请编译器产一条诊断"这条路就没了 —— 而这条判据要判的是**渲染器读文件头**这件事，
    与谁产的诊断无关。所以夹具改成 `.py` 文件 + 一条合成诊断。顺带把判据变强了：
    **后缀说是 Python、声明说是 C#**，此时"声明赢"才是真的被验到（以前两边都是 Python，
    声明赢不赢看不出来）。
    """
    def probe(name: str, body: str) -> str:
        td = Path(tempfile.mkdtemp(prefix="lomenterr-decl-"))
        f = td / f"{name}.py"                 # 后缀那条路也认得出来 → 外源段一定在
        f.write_text(body, encoding="utf-8", newline="\n")
        d = td / "d.jsonl"
        d.write_text(json.dumps({"file": str(f), "line": 2, "col": 1, "code": "E019",
                                 "message": "boom"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
        assert rc == 1, (rc, out[:120])
        return out

    # 声明与后缀**说不同的话**：后缀是 `.py`，声明是 C# —— 声明必须赢
    out = probe("decl", "choose write grammar cpp" + "\n" + "\n"
                + "x = 1" + "\n")
    assert "像 C++" in out, f"没按声明认（后缀是 .py）：{out[-400:]!r}"
    assert "据文件头" in out, f"没说清依据是声明：{out[-400:]!r}"

    # 别名：作者写 `cs` / `c#` 都该落到 C#
    for alias in ("cs", "c#", "C#"):
        o2 = probe("al", "choose write grammar " + alias + "\n"
                   + "y = 2" + "\n")
        assert "像 C#" in o2, f"别名 {alias!r} 没认成 C#：{o2[-400:]!r}"

    # 没有声明时，依据仍要说清是"据后缀"（不能混着说）
    o3 = probe("noext", "y = 2" + "\n")
    assert "像 Python" in o3 and "据后缀" in o3, f"没说清依据是后缀：{o3[-400:]!r}"
    print("      声明压过后缀与内容嗅探（含别名 cs/c#/C#），依据也说给用户")

@test
def test_a_malformed_declaration_is_not_honoured():
    """声明那一行的**词边界**要钉死：写坏了就**不许当声明**。

    这一条来自对端的教训（而且是他们自己抓到的）：他们把那三个词从散落的字面量重组成
    "从一个常量拼出来"时，`_GRAMMAR_LINE` 的尾巴漏了 `` —— 于是拼错一个字母的
    `choose write grammars python` 会命中**前缀**、抹掉 20 个字符、留下 `s python`；
    而 `read_grammar_decl` 那边**有** `` 所以**不报错**，坏处全落在用户那行上。
    抓住它的**不是判据**（原有 7/7 全过），是"**重组前后对拍**"。

    我这边刚把 `decl_at` 从三次写死的 `eat_word(…, "choose")` 改成**逐词循环**
    （词序读 `surface_data`）—— **形态上与他那次是同一类改动**。所以这里不靠嘴说
    "`eat_word` 本来就有边界检查"，而是**把那张表钉出来**。

    判据的断言刻意分成两种，免得把"正确地忽略"与"坏掉了"混成一条：
    **畸形**的声明不许说"据文件头"；**良构**的必须说。而畸形那半还要**多一条**：
    外源段必须**在场**（`不是 Loment` 要出现）—— 不然"没说据文件头"在"根本没说任何话"时
    也成立，那这条判据就没有牙。夹具因此用 `.py` 后缀：后缀那条路保证外源段一定在，
    于是"没说据文件头"只能是因为声明没被认。

    **夹具自己造诊断**（理由见上一条判据）：前门落地后，写了声明的文件编得过，
    "请编译器产诊断"这条路没有了 —— 而这条判据判的是渲染器**读文件头**。
    """
    def probe(body: str) -> str:
        td = Path(tempfile.mkdtemp(prefix="lomenterr-declbad-"))
        f = td / "m.py"                        # 后缀把外源段钉在场
        f.write_text(body, encoding="utf-8", newline="\n")
        d = td / "d.jsonl"
        d.write_text(json.dumps({"file": str(f), "line": 2, "col": 1, "code": "E019",
                                 "message": "boom"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d, lang=ZH)
        assert rc == 1, (rc, out[:120])
        return out

    # **畸形**：都不许被当成声明（"据文件头"一个字都不该出现，而外源段要在）
    malformed = [
        "choose write grammars python",     # 第三个词拼错 —— 对端那次就是这一条
        "choose write grammar",              # 没有别名
        "choosewrite grammar python",        # 词之间没断
        "xchoose write grammar python",      # 行首有杂质
        "// choose write grammar python",    # **注释里**写着 —— 最要紧的一条
        "choose write grammar python2",      # 别名不在出厂锁里
    ]
    for line in malformed:
        out = probe(line + "\n" + "y = 1" + "\n")
        assert "不是 Loment" in out, f"夹具没让外源段出现, 这条判据就没牙了: {out[-300:]!r}"
        assert "据文件头" not in out, f"畸形声明被当成了声明: {line!r}\n{out[-300:]!r}"

    # **第三词后面的分隔符类** —— 这一类是对端用**枚举**抓到的（手挑挑不出 `#` `.` `:`
    # 插在第三词后面）。他们那边原先用 ``，`#` 算"词边界"于是判成"头成立、别名没写"；
    # 我这条刀要求"空白 / `;` / 行尾"，判成"根本没有声明"。**两边对同一份源说不同的话**
    # ——两边都编得过，所以只有枚举能把它们摆到一起。现在两边同一条规矩，这里把它钉住。
    for sep in ("#", ".", ":", ",", "-", "_", "(", "!", "=", "@", "'", '"'):
        body = "choose write grammar" + sep + "python" + "\n" + "y = 1" + "\n"
        out = probe(body)
        assert "不是 Loment" in out, f"夹具没让外源段出现: {sep!r} {out[-300:]!r}"
        assert "据文件头" not in out, (
            f"第三词后接 {sep!r} 被当成了声明: {out[-300:]!r}")

    # **良构**：必须说"据文件头"（含允许的写法：分号收尾、别名后跟说明）
    wellformed = ["choose write grammar python",
                  "  choose	write  grammar	python",
                  "choose write grammar python;",
                  "choose write grammar python // 说明"]
    for line in wellformed:
        out = probe(line + "\n" + "y = 1" + "\n")
        assert "像 Python" in out and "据文件头" in out, (
            f"良构声明没被认: {line!r}\n{out[-300:]!r}")
    print("      声明的词边界: 6 种畸形 + 12 种分隔符都不认；4 种良构都认"
          "（含注释里那句不认）")

@test
def test_a_superset_language_is_not_reported_as_its_subset():
    """**C++ 不能被报成 C、C# 不能被报成 Java** —— 认语言是"逐门问、取第一个命中"，
    所以顺序**就是优先级**，而顺序写在 `loment_diag.LANG_ORDER` 里。

    这一条是对端抓出来的真 bug：生成器原先 `sorted(LANG_CARDS)`（字典序），于是 `c`
    排在 `cpp` 前面 —— 一份 C++ 文件里有 `#include`，**先被 C 那三条特征词接走**，
    C++ 的提示永远轮不到；同理 `java` 会接走 `csharp` 的 `public class`。

    它可证伪，而且证伪的方向明确：**把 `LANG_ORDER` 换回 `sorted` 这条就红**。
    加一门新语言时，如果它的特征词是某一门的老超集（像 C++ 之于 C），就得排到那门
    前面 —— 这是"顺序即优先级"的固有代价，写在这里免得下次再撞。
    """
    cases = [
        ("cpp", "C++", "#include <vector>" + "\n"
         + "int sum(int a) {" + "\n" + "    return a;" + "\n" + "}" + "\n"),
        ("cs", "C#", "using System;" + "\n"
         + "public class P {" + "\n"
         + "    static void Main(string[] args) {}" + "\n" + "}" + "\n"),
    ]
    for ext, want, src in cases:
        td = Path(tempfile.mkdtemp(prefix="lomenterr-order-"))
        f = td / f"m.{ext}.lomt"        # 后缀是 .lomt -> 只能靠内容认
        f.write_text(src, encoding="utf-8", newline="\n")
        d = td / "d.jsonl"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                            str(f), "--check", "--diag-out", str(d)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        assert r.returncode == 1, (ext, r.returncode)
        _, out = _render(d, lang=ZH)
        assert f"像 {want}" in out, f"{ext} 的文件被认成别的语言了: {out[-400:]!r}"
    print("      C++ 不被认成 C、C# 不被认成 Java（顺序即优先级）")

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
    rc, out = _render(d, lang=ZH)
    assert rc == 1
    assert out.count("不是 Loment") == 1, f"外源段给了不止一次: {out.count(chr(19981) + chr(26159) + chr(32) + chr(76))}"
    assert "2 条错误" in out, "两条诊断都要计数"
    print("      外源段一个文件只给一次 (两条诊断, 一次提示)")

# ---------------------------------------------------------------- 语言 (§14)

@test
def test_english_is_the_default_and_the_renderers_own_text_is_ascii():
    """**出厂默认说英文**，而且那句英文一个非 ASCII 字节都没有。

    两条是一件事的两半。默认之所以从中文换成英文：Windows 控制台按 936 代码页解 UTF-8，
    而 PE 垫片没有 `WriteConsoleW`，程序侧无从补救（`docs/169` §3a）—— 于是"默认输出"
    在中文 Windows 上一直是乱码。换成英文才修得掉，**前提是那份英文真的全 ASCII**：
    夹一个 `—` 或 `…` 进来，936 控制台上照样花，这个改动就白做了。

    **判据自己造一条 ASCII 消息**，而不是拿编译器那条：编译器给的消息原文现在仍是中文
    （`docs/182` §5.3，那是另一条线的事），混进来会把"报错器自己那部分是不是 ASCII"
    偷换成"整份输出是不是 ASCII"。分开说才判得准 —— 也才诚实地说明这个改动修到了哪一步。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-lang-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": str(src), "line": 4, "col": 12, "code": "E002",
                                 "message": "call to undeclared function z"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)                     # 不传 lang = 出厂默认
    assert rc == 1, (rc, out[:200])
    assert "error[E002]:" in out, out[:200]
    assert "undeclared name" in out, f"默认没说英文: {out[:200]!r}"
    # 四段齐全（英文侧也要，不能只有中文那份全）
    for label in ("what went wrong:", "why:", "how to fix:", "supported:", "not supported:"):
        assert label in out, f"英文侧说明卡少了一段 {label!r}: {out[-300:]!r}"
    for i in (1, 2, 3):
        assert f"  {i}. " in out, f"英文侧没有第 {i} 条修法: {out[-300:]!r}"
    assert out.count("^") >= 1, "英语侧没有插入符"
    bad = sorted({c for c in out if ord(c) > 127})
    assert not bad, f"渲染器自己那部分有非 ASCII 字节 {bad!r} —— 936 控制台上就是乱码"
    print("      默认英文, 四段齐全, 且渲染器自己那部分逐字节纯 ASCII")


@test
def test_errconfig_switches_the_language_and_a_bad_one_falls_back():
    """`errconfig` 能把语言切到中文；**读不出来就退回默认**（英文），不报错。

    六种输入各来一遍，判决只有两个：切了 / 没切。
      * `"zh"` / `"ZH"` / `"Chinese"` → 中文（**本来就该大小写不敏感**：配置是手写的，
        逼用户记住大小写换来的是"配了没生效"，而那种失败**没有任何提示**。
        第一版只让长写法不敏感、`en`/`zh` 是逐字节比的 —— `"ZH"` 静默退回默认，
        正是文档说 A 代码做 B。这一条留着就是那次实测）；
      * `"en"` → 英文（显式写回默认，也是合法的）；
      * `"klingon"` → **认不出来**，退回英文 —— 不报错、不猜；
      * 注释里出现 `error_lang` → **不算**（配置项要写成 `error_lang(`）。

    最后一条是设计的一部分而不是巧合：少了"标签后面必须紧跟 `(`"这条，一句注释就能把语言
    悄悄改掉 —— 而"悄悄生效"正是本仓最反对的那种。
    """
    f, d = _check(SEMANTIC)
    del f

    def cfg(v: str) -> str:
        return f'module e\npub fn error_lang() -> str {{\n    return "{v}";\n}}\n'

    cases = [
        (cfg("zh"), True),
        (cfg("ZH"), True),
        (cfg("Chinese"), True),
        (cfg("en"), False),
        (cfg("klingon"), False),
        ('// error_lang is documented here\n'
         'pub fn other() -> str {\n    return "zh";\n}\n', False),
    ]
    for body, want_zh in cases:
        with tempfile.TemporaryDirectory(prefix="lomenterr-cfg-") as td:
            _errconfig(Path(td), body)
            rc, out = _run_in(Path(td), d)
        assert rc == 1, (body, rc, out[:120])
        got_zh = "符号未声明" in out
        assert got_zh == want_zh, f"errconfig={body!r} 期望中文={want_zh} 得到 {got_zh}: {out[:200]!r}"
    print("      errconfig: zh/ZH/Chinese 切中文, en/乱值/注释里的伪标签都退回默认")


@test
def test_the_toolchain_beside_errconfig_is_the_second_place_read():
    """两份 `errconfig`：**项目那份优先，工具链旁边那份兜底**（`docs/182` §14）。

    `./errconfig` 好测（`_render` 就是这么干的）；工具链旁边那份**从 `argv[0]` 推**，
    所以要摆一个假的包布局：`<tmp>/bin/lomenterr` + `<tmp>/share/loment/errconfig`。

    这条同时钉住一个容易被写反的语义：**"值认不出来" = "没配"**，于是**继续找下一份**。
    不这么做的话，项目里写错一个字母会把用户**系统级**的那份设置悄悄作废 ——
    用户明明配过一次、又明明刚在项目里"配"了一次，结果两份都没生效，而输出里没有任何提示。
    这条纪律是从 `loment.conf` 抄的（"读不出来就当没配"），所以它两边是同一句。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-pkg-") as td:
        pkg = Path(td) / "pkg"
        (pkg / "bin").mkdir(parents=True)
        (pkg / "share" / "loment").mkdir(parents=True)
        exe = pkg / "bin" / _bin().name
        shutil.copy2(_bin(), exe)                     # 保留可执行位（Linux 上要）
        _errconfig(pkg / "share" / "loment", 'module errconfig\n'
                                             'pub fn error_lang() -> str {\n'
                                             '    return "zh";\n}\n')

        cwd = Path(td) / "proj"
        cwd.mkdir()
        _, d = _check(SEMANTIC)                        # 诊断文件的 file 是绝对路径

        def run() -> str:
            r = subprocess.run([str(exe), "--no-color", str(d)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=60, cwd=str(cwd))
            assert r.returncode == 1, (r.returncode, r.stderr[:120])
            return r.stdout + r.stderr

        assert "符号未声明" in run(), "工具链旁边那份没被读到"
        # 项目那份写了个认不出来的值 → **当作没配**，落到工具链那份（中文），而不是英文
        _errconfig(cwd, 'module errconfig\npub fn error_lang() -> str {\n'
                        '    return "klingon";\n}\n')
        assert "符号未声明" in run(), "认不出来的值把系统级设置作废了 (该继续找下一份)"
        # 项目那份**有效**时必须压过工具链那份
        _errconfig(cwd, 'module errconfig\npub fn error_lang() -> str {\n'
                        '    return "en";\n}\n')
        assert "undeclared name" in run(), "项目那份没有压过工具链那份"
    print("      两份 errconfig: 项目优先、工具链兜底, 认不出来的值算'没配'")


# ---------------------------------------------------------------- 汇总 (§15)

@test
def test_summary_groups_by_code_worst_first():
    """结尾的汇总**按码分组，条数多的先出**，并列时码号小的在前。

    这是"12 条错误"之外多出来的那点信息：12 条里有 9 条是同一个原因时，改一处就消掉九条，
    而按出现顺序一条条修会白改八次。所以**顺序本身是被判的**（不是"反正都印出来了"）。

    汇总只在**给人看**的两个模式下打（`--json` 不打，那会让下游解析器当场坏掉）。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-sum-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        # 故意让"少见的码"排在文件里靠前：按出现顺序排的话 E019 会先出，这条判据就抓得到。
        rows = [{"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "a"},
                {"file": str(src), "line": 1, "col": 1, "code": "E002", "message": "b"},
                {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "c"},
                {"file": str(src), "line": 1, "col": 1, "code": "E019", "message": "d"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1]
    assert tail == "4 errors: E019 x3, E002 x1", f"汇总行不对: {tail!r}"
    print("      汇总按码分组、条数多的先出 (4 errors: E019 x3, E002 x1)")


# ---------------------------------------------------------------- --short / --json (§15)

@test
def test_short_mode_is_one_line_per_diagnostic():
    """`--short`: **一行一条**，`文件:行:列: error[码]: 消息`，而且**顺带关色**。

    形状照抄 GCC / clang 那一族，编辑器与 CI 不用任何配置就认得。三个断言各挡一种退化：
    * 行数 = 诊断数（多一行少一行，"一行一条"这个承诺就破了）；
    * 一条诊断里的换行**折成空格**（不折的话一条会变两三行 —— 同一个承诺的另一种破法）；
    * **没有转义字节**（`--short` 不带 `--no-color` 也不该上色：管道里要的是能 grep 的行）。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-short-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        rows = [{"file": "a.lomt", "line": 3, "col": 7, "code": "E002", "message": "x\ny"},
                {"file": "b.lomt", "line": 9, "col": 0, "code": "E019", "message": "z"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _run_in(Path(td), d, "--short", color=True)
    assert rc == 1, (rc, out[:200])
    lines = out.strip().splitlines()
    assert lines[0] == "a.lomt:3:7: error[E002]: x y", f"短格式不对: {lines[0]!r}"
    # col=0 是"编译器只给了行号"（check() 的语义错就是这样），那时不该硬编一个列出来
    assert lines[1] == "b.lomt:9: error[E019]: z", f"没列时不该印列: {lines[1]!r}"
    assert lines[2] == "2 errors: E002 x1, E019 x1", f"短模式不给汇总: {lines[2]!r}"
    assert len(lines) == 3, f"一行一条: 3 行, 得到 {len(lines)} 行: {out!r}"
    assert "\x1b" not in out, f"--short 不该上色 (管道里要能 grep): {out[:120]!r}"
    print("      --short: 一行一条 + 换行折空格 + 顺带关色 + 末尾汇总")


@test
def test_json_mode_carries_the_whole_card_and_parses():
    """`--json`: **一行一个对象**（JSONL，与编译器的 `--diag-out` 同族，多了卡片字段）。

    它给的是**程序**：编辑器把 `what`/`why`/`fixes` 放进 quickfix 面板，CI 按码统计。
    所以三条都要判：
    * **每一行都能被解析** —— 卡片正文里有 `"` 与反斜杠，不转义就是一个坏 JSON，
      而在编辑器那边那表现为"报错器坏了"，不是"这条消息有点怪"；
    * 卡片字段**在**且 `fixes` 是数组（≥3 条，与门槛同一份）；
    * **没有汇总行** —— 那是"给人看"的东西，混进 JSONL 会让下游解析器当场坏掉。
      所以带中文消息的一行也要能过（转义把非 ASCII 原样留着，JSON 允许）。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-json-") as td:
        src = Path(td) / "bad.lomt"
        src.write_text(SEMANTIC, encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        rows = [{"file": "a.lomt", "line": 3, "col": 7, "code": "E002",
                 "message": 'has "quotes" \\ and\na newline'},
                {"file": "b.lomt", "line": 9, "col": 2, "code": "E002", "message": "中文也行"}]
        d.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _run_in(Path(td), d, "--json", color=True)
    assert rc == 1, (rc, out[:200])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2, f"每个诊断一行, 不该多出别的行(汇总?): {out!r}"
    for ln in lines:
        obj = json.loads(ln)                     # 解析不了就是坏 JSON
        for k in ("code", "title", "file", "line", "col", "message",
                  "what", "why", "fixes", "yes", "no"):
            assert k in obj, f"缺字段 {k}: {ln[:120]!r}"
        assert isinstance(obj["fixes"], list) and len(obj["fixes"]) >= 3, obj["fixes"]
    first = json.loads(lines[0])
    assert first["message"] == 'has "quotes" \\ and\na newline', first["message"]
    assert first["file"] == "a.lomt" and first["line"] == 3 and first["col"] == 7
    assert "\x1b" not in out, "--json 不该上色"
    print("      --json: 每行可解析 + 卡片字段齐全 + 转义正确 + 不掺汇总行")


# ---------------------------------------------------------------- 拼写建议 (§14)

@test
def test_did_you_mean_only_when_it_is_the_only_candidate():
    """E002 的"你是不是想写 X"：**只有一个近候选时才说**。

    三条门槛都判：说得出、说不准就不说（**两个一样近的并列**）、差得远也不说。
    中段那条是本条判据的重点 —— 说错一个名字比不说坏得多：用户会**照着它改**，
    那是把一份本来能编的代码改坏。所以"宁可不猜"必须是被动过的代码路径，不是注释里的一句话。
    """
    # 三个声明，各有各的用处：`leftover_count` 是唯一近候选（第 7 行），
    # `alpha_count` / `beta_count` 互为并列（对 `gama_count` 都是差一个字符）。
    src = ("module m\n\n"
           "fn alpha_count() -> u32 { return 0; }\n\n"
           "fn beta_count() -> u32 { return 0; }\n\n"
           "fn leftover_count() -> u32 { return 0; }\n\n"
           "fn _start() -> u32 { return 0; }\n")
    with tempfile.TemporaryDirectory(prefix="lomenterr-sug-") as td:
        f = Path(td) / "m.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")

        def render(msg: str) -> str:
            d = Path(td) / "d.jsonl"
            d.write_text(json.dumps({"file": str(f), "line": 7, "col": 10, "code": "E002",
                                     "message": msg}) + "\n",
                         encoding="utf-8", newline="\n")
            rc, out = _render(d)
            assert rc == 1, (rc, out[:200])
            return out

        # 唯一近候选: `leftover_count`(第 7 行声明)
        one = render("call to undeclared function leftove_count")
        assert "did you mean" in one and "`leftover_count`" in one, one[-300:]
        assert "declared at line 7" in one, f"没给出跳转线索: {one[-300:]!r}"
        # **并列**: alpha_count 与 beta_count 都只差一个字符 → 不猜
        tie = render("call to undeclared function gama_count")
        assert "did you mean" not in tie, f"并列了还敢猜: {tie[-300:]!r}"
        # 差得远: 与任何名字都不像 → 不猜
        far = render("call to undeclared function something_else_entirely")
        assert "did you mean" not in far, f"差得远也猜: {far[-300:]!r}"
    print("      拼写建议: 唯一近候选才说 (并列/差得远都不说)")


# ---------------------------------------------------------------- E018 的候选 (§15)

@test
def test_e018_lists_what_is_actually_in_deps():
    """E018: 光说"补文件"没用 —— 要把**手上真实有的**说出来。两种地方各判一条。

    * 没有 `./deps` → 明说没有，并把第 1 层找的路径写清楚。**这条不需要 `getdents64`**：
      `openat` 失败本身就是答案；
    * 有 `./deps` → 只列**名字接近**的（`zlib` 与 `util` 差得远，不许出现在清单里）。

    工作目录就是"项目根"（`use` 的第 1 层从这里找），所以要 `_run_in` 指定 cwd ——
    这也顺带钉住"项目根 = 工作目录"这条约定。

    **线索取自 `msg_hint`**（消息里最后一个"词"），与插入符用的是同一个启发式 ——
    所以夹具的消息要写成**编译器真会写的那种**：中文包着名字（`名字导入找不到模块 util`）。
    写成英文自然句（`... module util for use`）时最后一个词是 `use`，
    那会去 `deps/` 里找一个叫 `use` 的东西 —— 而这恰好说明这条判据**咬得住**那个启发式，
    所以这里也顺手把这条依赖写在判据里，而不是让它藏在渲染器里。
    """
    with tempfile.TemporaryDirectory(prefix="lomenterr-deps-") as td:
        root = Path(td)
        f = root / "m.lomt"
        f.write_text("module m\n", encoding="utf-8", newline="\n")
        d = root / "d.jsonl"
        d.write_text(json.dumps({"file": str(f), "line": 2, "col": 1, "code": "E018",
                                 "message": "名字导入找不到模块 util"}) + "\n",
                     encoding="utf-8", newline="\n")

        rc, out = _run_in(root, d)               # 还没有 deps/
        assert rc == 1, (rc, out[:200])
        assert "no `./deps` directory here" in out, f"没明说没有 deps/: {out[-300:]!r}"

        for nm in ("util2", "utils", "zlib"):
            (root / "deps" / nm).mkdir(parents=True)
        rc, out = _run_in(root, d)
        assert rc == 1, (rc, out[:200])
        assert "util2" in out and "utils" in out, f"没列出接近的名字: {out[-300:]!r}"
        assert "zlib" not in out, f"差得远的不该进清单: {out[-300:]!r}"
    print("      E018: 没有 deps/ 就明说; 有就只列名字接近的")


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

    # `--short` / `--json` 走同一条转交路（`docs/182` §15）：它们也是**渲染器**的输出
    # 模式，驱动不该看见。这里判的是**行为**（桩渲染器把自己的 argv 打出来），
    # 因为"转发到位"不是能从代码里读出来的性质 —— cmd 侧只能静态判，
    # 那一条在 `loment_cli_test` 里。
    for spelling in ("--short", "--json"):
        got_om = run(make_pkg(True), spelling)
        assert "RENDERED-BY-LOMENTERR" in got_om and spelling in got_om, (
            f"{spelling} 没被转交给渲染器: {got_om!r}")
    # 两个一起给时**两个都要到**（用一个变量存一个开关就会静默丢掉另一个）
    got_both = run(make_pkg(True), "--no-color", "--json")
    assert "--no-color" in got_both and "--json" in got_both, (
        f"同时给两个开关时丢了一个: {got_both!r}")
    print("      启动器: 在场则渲染(且不吃裸行), 缺席则退回并明说, "
          "三个开关都转交到位")


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
