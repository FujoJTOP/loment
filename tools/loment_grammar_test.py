#!/usr/bin/env python3
"""loment_grammar_test.py — **`choose write grammar`：读法由声明决定，不由嗅探**（`docs/188`）。

用户 2026-09-18 要的那个"伟大的开关"：

> 或许，我们加入那个伟大的开关 / choose / 它回来了 / choose write grammar 语言
> 硬写法，和 std/no_std 同级，直接 lock 锁住，弱的地方终于能动了
> **grammar py 和 grammar python 都是完全可以写的**，Potato 做错了，但为 let 语句铺了路

## 它治的是什么

`docs/188` §2：源侧**可选**、对象侧**必填**。没写就是 Loment —— 99% 的文件是 Loment，
每份写一遍是噪声，而"缺 = Loment"**没有歧义**。这一下把 `detect_lang` 的**嗅探**
（"有 `#include` 之类预处理指令"）换成**声明**；兜底从"猜"变"拒绝"。

## `lock` 是**出厂锁**，不是 `docs/182` §3 那个"装进本机"

| | `docs/182` §3 的 `lock` | 这里的出厂锁 |
|---|---|---|
| 装到哪 | 用户目录（`.lomlock`） | 无 —— 编译进工具链 |
| 谁改得动 | 用户（`chooseunlock`） | **没人** |
| 阶段 | C3，**未实现** | 现在就能做 |

**所以 grammar 落地不依赖 C3** —— 出厂锁没有"本机状态"这回事，而
"本机状态"正是那条线卡住的地方。`docs/182` §3.1 那条冲突（锁与本机绑定、破坏
"同一份源 → 同一串字节"）**同样不适用**：这张表在每台机器上都是同一份。

用法: python tools/loment_grammar_test.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf       # noqa: E402
import lomentc      # noqa: E402
import lomt_from    # noqa: E402
import potato       # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


#: **出厂锁的那张表**（`docs/188` §1.1）—— 在这里**钉死**一份字面量。
#:
#: 这不是"测试写死了实现"：**锁的意思就是它不该随便动**。谁要加一门语言，
#: 就得**同时**改这里 —— 那一步是"改锁"，而不是"顺手加个分支"。
#: （用户提的"将来可以用哈希把它钉死"，钉的就是这一刻；现在先用一条判据钉。）
LOCKED_ALIASES = {
    "loment": "loment",
    "c": "c",
    "py": "python", "python": "python",
    "java": "java",
    "cs": "csharp", "csharp": "csharp", "c#": "csharp",
    "cpp": "cpp", "c++": "cpp", "cxx": "cpp", "cc": "cpp",
    "go": "go", "golang": "go",
    "rs": "rust", "rust": "rust",
}


@test
def test_the_factory_lock_table_is_exactly_this():
    """**出厂锁的取值表就是这一份** —— 加一门语言要**改这里**，那是"改锁"。

    `docs/188` §1.1：这张表由官方给，**用户不能扩展、不能覆盖、不能解锁**。
    所以判据把它当成一份**锁住的值**来核对，而不是"从别处推出来"。
    """
    got = potato_from.GRAMMAR_ALIASES
    diff = [(k, LOCKED_ALIASES[k], got[k])
            for k in sorted(set(got) & set(LOCKED_ALIASES)) if got[k] != LOCKED_ALIASES[k]]
    assert got == LOCKED_ALIASES, (
        "出厂锁的取值表被改了。**如果是有意加语言**，那就同时把上面那份字面量一起改"
        "（那一步就是「改锁」，得是显式的一笔）；**如果不是**，改回去：\n"
        f"  多了: {sorted(set(got) - set(LOCKED_ALIASES))}\n"
        f"  少了: {sorted(set(LOCKED_ALIASES) - set(got))}\n"
        f"  改值: {diff}")
    print(f"      出厂锁的取值表 {len(got)} 条拼法，与钉住的那份一致")


@test
def test_canonical_names_are_exactly_the_object_side_vocabulary():
    """**规范名只许是 `potato.GRAMMARS` 那个集合** —— 别名表只有一处，规范名也只有一处。

    `docs/188` §1.2：源侧宽松（`py` / `python` 都收）、**对象侧只许一个拼法**，
    否则同一份源出两串字节，判据当场红。而那一个拼法是谁定的？是 `potato` 的
    `GRAMMARS`（v6 校验器认的那几个）。两处对不上的症状是：源里能写、对象却**非法**。
    """
    canon = set(potato_from.GRAMMAR_ALIASES.values())
    want = set(potato.GRAMMARS)
    assert canon == want, (
        f"别名表的规范名与 `potato.GRAMMARS` 不一致\n"
        f"  别名表里有而 GRAMMARS 没有: {sorted(canon - want)}\n"
        f"  GRAMMARS 里有而别名表没有: {sorted(want - canon)}")
    print(f"      规范名 {len(canon)} 个，与 `potato.GRAMMARS` 逐字一致")


@test
def test_aliases_spell_the_same_canonical_name():
    """**同一门的几种拼法进对象只许剩一个**（用户点名的那一条）。

    用户原话："**grammar py 和 grammar python 都是完全可以写的**" —— 源侧随意，
    进对象必须是规范名，否则同一份源在两台机器上会出两串字节。
    """
    for spellings, canon in ((("py", "python"), "python"),
                             (("c++", "cpp", "cxx", "cc"), "cpp"),
                             (("c#", "cs", "csharp"), "csharp"),
                             (("golang", "go"), "go"),
                             (("rs", "rust"), "rust"),
                             (("loment",), "loment"),
                             (("c",), "c"),
                             (("java",), "java")):
        for s in spellings:
            g, err, declared = potato_from.read_grammar_decl(f"choose write grammar {s}\n")
            assert (g, err, declared) == (canon, None, True), (s, g, err, declared)
    # 大小写不收人 (源侧宽松) —— 但进对象的一定是小写规范名
    g, err, _ = potato_from.read_grammar_decl("choose write grammar Python\n")
    assert (g, err) == ("python", None), (g, err)
    print("      `py`/`python` -> python；`c++`/`cpp`/`cxx`/`cc` -> cpp；大小写不收人")


@test
def test_the_declaration_decides_the_reading_not_the_sniff():
    """**读法由声明决定** —— 同名后缀、不同内容，声明说了算。

    三件事：

    1. 一份 `.lomt` 装着 Python，**不写声明**时靠嗅探（`def` 那条）认出 python；
    2. 同一份内容**写了声明**时按声明走（这里是把它指到 `c`，而它是 Python 写法 ——
       **声明就是要压过嗅探**，压不过的话这个开关等于没有）；
    3. **声明压过后缀**：一份 `.py` 写着 `grammar c`，读法就是 `c` ——
       后缀是命名习惯、会说谎，而声明是**作者对这份文件说的**。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        py_src = "def f(a: int) -> int:\n    return a + 1\n"

        # ① 没声明 -> 嗅探出 python
        a = td / "a.lomt"
        a.write_text(py_src, encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(a)[0] == "python", potato_from.resolve_lang(a)

        # ② 有声明 -> 按声明（**故意指到 c，看它压不压得过嗅探**）
        b = td / "b.lomt"
        b.write_text("choose write grammar c\n" + py_src, encoding="utf-8", newline="\n")
        got = potato_from.resolve_lang(b)
        assert got[0] == "c", f"声明压不过嗅探: {got}"
        assert "声明" in got[1], got

        # ③ 声明压过后缀
        c = td / "c.py"
        c.write_text("choose write grammar c\n" + py_src, encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(c)[0] == "c", potato_from.resolve_lang(c)

        # ④ 没声明时后缀仍然说话（`.py` -> python）
        d = td / "d.py"
        d.write_text(py_src, encoding="utf-8", newline="\n")
        assert potato_from.resolve_lang(d)[0] == "python", potato_from.resolve_lang(d)
    print("      声明压过嗅探、也压过后缀；没声明时后缀与嗅探照旧")


@test
def test_bad_declarations_are_loud():
    """**写错要报出来，而且指的要对** —— 位置、次数、取值三档各有各的话。"""
    cases = [
        ("module m\nchoose write grammar python\n", "之前"),      # 在 module 之后
        ("choose write grammar py\nchoose write grammar c\n", "一次"),  # 写两次
        ("choose write grammar rustc\n", "出厂锁"),                # 拼法不在表里
        ("choose write grammar\n", "语法名"),                      # 后面没写
    ]
    for src, want in cases:
        g, err, declared = potato_from.read_grammar_decl(src)
        assert err is not None, f"{src!r} 该报错却过了"
        assert want in err, f"要点名 `{want}`: {err}"
        assert declared is False, declared
    # 取值那一档**要把可写的列出来**（不然用户只知道"不行"、不知道"写什么行"）
    _, err, _ = potato_from.read_grammar_decl("choose write grammar rustc\n")
    for alias in ("python", "cpp", "csharp", "go"):
        assert alias in err, f"报错里该列出可写拼法: {err}"
    print("      `module` 之后 / 写两次 / 拼法不在表里 / 后面没写 —— 四档都报得出")


@test
def test_declaration_is_stripped_before_the_target_parser_sees_it():
    """**声明要在交给目标语言的解析器之前抹掉，而且行号不许动。**

    `choose write grammar python` **不是合法的 Python**（也不是合法的 C / Go / …）——
    声明是**读法**，不是那份源的一部分。不抹的话，一份写得好好的 Python 会在第一行
    就被目标解析器判成语法错。

    抹法是**等长空白**（保留换行），所以目标语法报错的行号仍然指回这份文件里的那一行。

    **这一条还有下半截**：抹完真的要能**跑起来** —— 一个数，两边对得上。
    """
    src = "choose write grammar python\n" + "def f(a: int) -> int:\n    return a + 1\n"
    stripped = potato_from.strip_grammar_decl(src)
    assert len(stripped) == len(src) and stripped.count("\n") == src.count("\n"), \
        "抹声明不能改长度/行数（改了就说明行号会漂）"
    assert "choose" not in stripped, "声明没抹干净"
    assert "python" not in stripped.split("\n")[0], "别名那一截没抹掉"

    # **下半截**：端到端跑一遍（声明在 -> 认得出来 -> 翻成 Loment -> 跑出 42）
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "unit.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        doc, rep = potato_from.transcribe(p, "auto", "strict")
        assert not rep.skipped, rep.skipped
        assert doc["grammar"] == "python", doc["grammar"]
        text, skipped = lomt_from.emit_lomt(doc, impl=True)
        assert not skipped, skipped
        l = td / "l.lomt"
        l.write_text(text + "\nfn _start() {\n    syscall4(60, f(41) as u64, 0, 0);\n}\n",
                     encoding="utf-8", newline="\n")
        mod = lomentc.load(l)
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=l)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
        blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
        exe = td / "l.elf"
        exe.write_bytes(blob)
        if not subprocess.run(["wsl", "-e", "true"], capture_output=True).returncode == 0:
            print("      （没有 WSL，跳过跑那一步）")
            return
        s = str(exe.resolve()).replace("\\", "/")
        r = subprocess.run(["wsl", "-e", "bash", "-lc",
                            f"chmod +x {s if s.startswith('/') else '/mnt/' + s[0].lower() + s[2:]}"
                            f" && {'/mnt/' + s[0].lower() + s[2:]}; echo -n $?"],
                           capture_output=True, text=True, timeout=300)
    assert r.stdout.strip() == "42", f"跑出来该是 42，得到 {r.stdout.strip()!r}"
    print("      声明抹成等长空白、行号不动；端到端跑出 42")


@test
def test_the_declaration_word_order_is_the_shared_contract():
    """**词序是与报错器共享的契约** —— 那三个词和"读到空白或 `;` 为止"。

    `loment/tools/lomenterr.lomt` 的 `decl_at` 是个**独立的 Loment 程序**：它问不到
    `potato_from`，只能逐词匹配 `choose` / `write` / `grammar`，找不到就**退回嗅探**。
    破了不会报错，只会**静默失配** —— 症状正是当初补它要治的那个：

    > 一份 `choose write grammar python` 的 `.lomt`，**工具链知道是 Python，
    > 而报错器一个字都不说**。工具链知道、渲染器沉默，是最坏的一种。

    **这个契约现在走导出管线**（loment-dev-86 的 `14cb902`）：

        常量 -> 我的正则 -> `--dump-surface` 的 `decl_word(i)` -> 渲染器逐词吃

    所以"改词序"只有**一处**可改。这条判据钉的是**这个常量的语义**（词序 + 别名边界
    + 词边界），并顺手钉住**产物里就是这三个词、按这个顺序** ——
    它与对方那条"产物必须新鲜"的判据合起来，等于"渲染器拿到的就是常量里的词序"。
    对方那条（`loment_err_test::test_a_grammar_declaration_beats_content_sniffing`）
    钉的是另一件事："渲染器读得到"。**两条都在才有意义。**
    """
    assert potato_from.GRAMMAR_DECL_WORDS == ("choose", "write", "grammar"), (
        f"词序变了：{potato_from.GRAMMAR_DECL_WORDS}。**改了它就要同时改报错器的 "
        f"`decl_lang`**（`loment/tools/lomenterr.lomt`），否则它静默退回嗅探。")
    # 别名那一格的边界**也是契约**：读到空白或 `;` 为止（报错器那处同一刀切法）。
    # 下面四条把这条边界钉死 —— 尤其 `;` 那条：它同时保证 `c#` 里的 `#` **不是**注释头。
    for src, want in (
        ("choose write grammar python;\n", "python"),     # 分号收下（C 系语言的写法）
        ("choose write grammar python \n", "python"),     # 行尾空白
        ("choose write grammar c#\n", "csharp"),          # `#` 不是注释头
        ("choose write grammar python // 说明\n", "python"),
    ):
        g, err, declared = potato_from.read_grammar_decl(src)
        assert (g, err, declared) == (want, None, True), (src, g, err, declared)
    # **词边界那条也钉上**：拼错一个字母不能被当成声明，更**不能**被抹掉半行。
    # 这条是随后的**对拍**抓出来的真 bug（见 `_GRAMMAR_LINE` 的注解）：少了 `\b`，
    # `choose write grammars python` 会被切成 `                    s python` ——
    # 而预扫那边**不报错**（它不认这是声明），于是坏处全落在用户那一行上，
    # 他拿到的是一行残缺的源 + 一句指不到点子的语法错。
    for s in ("choose write grammars python\n", "choose writes grammar c\n"):
        g, err, declared = potato_from.read_grammar_decl(s)
        assert (g, err, declared) == ("loment", None, False), (s, g, err, declared)
        assert potato_from.strip_grammar_decl(s) == s, f"拼错的那行被抹了半截: {s!r}"

    # **导出那半边也钉上**：产物里就是这三个词、按这个顺序。它 + 对方那条"产物必须新鲜"
    # 的判据合起来，等于"渲染器拿到的就是常量里的词序" —— 改常量而忘了重新生成，
    # 或者有人改了导出而没改常量，这条当场红，而不是等渲染器静默退回嗅探。
    #
    # **读产物文件，不 import `loment_diag`** —— 后者模块级 `import lomentc`，而那个文件
    # 常有别的会话在改；读产物既够用，也不把这条判据绑上"编译器此刻可导入"。
    surface = (ROOT / "loment" / "tools" / "surface_data.lomt").read_text(encoding="utf-8")
    words = potato_from.GRAMMAR_DECL_WORDS
    assert f"decl_word_count() -> u32 {{ return {len(words)}; }}" in surface, (
        f"导出里的词数与常量对不上（常量 {len(words)} 个）—— 改完常量要重新生成 "
        f"`loment/tools/surface_data.lomt`（`python tools/loment_diag.py --dump-surface`）")
    for i, w in enumerate(words):
        arm = f'    if i == {i} {{ return "{w}"; }}'
        assert arm in surface, f"导出里没有这一条（顺序也要对）: {arm!r}"
    print("      词序/别名边界/词边界 == 共享契约，且产物里就是这三个词、按这个顺序")


def _ref_find(src: str) -> tuple[bool, str | None, int]:
    """**独立写的参照实现**：按字符走一遍，**一行正则都不用**。

    它对着的是 `potato_from.find_decl` —— **只管"找"**那一层（有没有声明头、别名是什么、
    第几行），**不管**"必须在 `module` 之前 / 只许写一次 / 别名在不在出厂锁里"。
    分层是必要的：不分层的话，拿"融合了三条规矩的结果"去比"只管找的结果"，
    对拍会满屏**假分歧**（实测：`module m` 在前那一档，融合层报"必须在 module 之前"、
    找层说 python —— 两边都对，只是**答的不是同一个问题**）。

    规则（与报错器 `decl_at` 同一刀）：行首可跳空白 → `choose` ␣ `write` ␣ `grammar`
    （三个**空白分隔**的词）→ 第三个词后面只能是 **空白 / `;` / 行尾** → 别名是
    **跳过空白之后**到空白/`;`/行尾为止的一串；跳完空白就撞上 `;` 或行尾 ⇒ **有头没别名**。
    """
    line = 1
    for raw in src.split("\n"):
        i, n = 0, len(raw)
        while i < n and raw[i] in " \t":
            i += 1
        ok = True
        for word in ("choose", "write", "grammar"):
            if raw[i:i + len(word)] != word:
                ok = False
                break
            i += len(word)
            if i < n and raw[i] not in " \t;":
                ok = False                       # 词后面贴了别的字符 ⇒ 不是这个词
                break
            j = i
            while j < n and raw[j] in " \t":
                j += 1
            if word != "grammar" and j == i:
                ok = False                       # 词与词之间必须有空白
                break
            i = j
        if ok:
            if i >= n or raw[i] == ";":
                return True, None, line           # 有头、没写别名
            j = i
            while j < n and raw[j] not in " \t;":
                j += 1
            return True, raw[i:j], line
        line += 1
    return False, None, 0


@test
def test_the_scanner_agrees_with_an_independently_written_reference():
    """**扫一批输入，与一个独立写的参照实现逐条对拍** —— 这是"重组是保义的吗"的答法。

    loment-dev-86 那句话说到点子上：**"改实现"的证据标准比"写实现"高** ——
    写的时候你只要证明新东西对，改的时候你要证明新旧**一样**，而"原有判据全过"
    恰恰是最容易骗人的那种证据（它们覆盖的是**行为**，不是**改动**）。

    这条就是那个标准的落地：不动手挑几条，而是**枚举**一批（原样 / 分隔符 / **每处
    单字符替换** / 别名各种拼法 / 前缀杂质 / 两次声明），拿**按字符走**写的参照实现与
    正则实现逐条比。手挑的表只能覆盖"想得到的写法"；枚举能覆盖到想不到的那些。

    **它当场抓到了两件真东西**：

    1. 参照自己写错过一处（`w[3:]` 应为 `w[len("grammar"):]`）—— 对拍**不保证谁对**，
       它保证**分歧会浮出来**；
    2. `choose write grammar#python` 这种"头后面贴着非空白非 `;`"的写法，
       **我的扫描器与报错器 `decl_at` 说不同的话**（我认成"有头没别名"→报错，
       它认成"没有声明"→退回嗅探）。于是把边界从 `\\b` 收紧成 **空白/`;`/行尾**
       —— 两边现在同一条规矩。

    **同时钉住最要命的那半**：参照说"根本没有声明"的行，`strip_grammar_decl` **一个字
    都不许动** —— 漏边界那次正是这里出的错（把 `choose write grammars python` 切成了
    `                    s python`，而预扫**不报错**，坏处全落在用户那行上）。
    """
    base = "choose write grammar python"
    lines: set[str] = {base, "  " + base, "\t" + base, "x" + base, "#" + base,
                       "// " + base, "/* " + base, "choose write grammars python",
                       "choosewrite grammar python", "choose writes grammar c",
                       "choose write grammar", "choose write grammar ",
                       "choose write grammar;", "choose write grammar python;",
                       "choose write grammar python // 说明",
                       "choose write grammar\tpython",
                       "choose write grammar python2", "choose write grammar Python",
                       base + "\n" + base,
                       "module m\n" + base, base + "\nmodule m"}
    # **分隔符 × 别名**：三处间隔换成空白/制表，别名换成每一种。
    for sep in (" ", "  ", "\t"):
        for alias in ("py", "python", "c#", "c++", "go", "python2", "", "Python"):
            lines.add(f"choose{sep}write{sep}grammar{sep}{alias}".rstrip())
    # **每处单字符替换**：这是"覆盖想不到的写法"的那一半 —— 手挑挑不出这些。
    for i in range(len(base)):
        for ch in ("x", " ", "\t", ";", "#", "."):
            lines.add(base[:i] + ch + base[i + 1:])

    bad = []
    for ln in sorted(lines):
        src = ln + "\n"
        mine, ref = potato_from.find_decl(src), _ref_find(src)
        if mine != ref:
            bad.append((ln, mine, ref))
            continue
        # **参照说"根本没有声明头"时，那一行一个字都不许被抹掉。**
        # 只对这一档断言 —— "**有**头但写错了"（比如没写别名）那一档，`read_grammar_decl`
        # 会当场报错、走不到抹除，抹不抹都无害；而"根本没有头"那一档**抹了就是把用户的
        # 行切坏**，漏边界那次正是这里出的错。**这一档才要命。**
        if not ref[0]:
            after = potato_from.strip_grammar_decl(src)
            if after != src:
                bad.append((ln, "strip 动了不该动的行", repr(after)))
    assert not bad, (
        f"{len(lines)} 条里 {len(bad)} 条与参照对不上（前 5 条）：\n"
        + "\n".join(f"  {ln!r}\n    find_decl={m}\n    参照={r}" for ln, m, r in bad[:5]))
    print(f"      枚举 {len(lines)} 条，与独立写的参照逐条一致（含行号）；且无声明头时一个字未动")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_grammar_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
