#!/usr/bin/env python3
"""loment_ctrans_test.py — **C 翻成 Loment，跑出来的数一样**（`docs/186` 的 Stage A 判据）。

用户 2026-09-17 给这条链定的判据就是这个：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接用 clang 编那份 C 跑出的结果」

**比的是数，不是文本。** 比文本的话，翻译器把 `a % b` 写成 `a - (a / b) * b` 也算过 ——
那不是判据，那是 golden file。所以这里两边都真编真跑：

    C  ──clang(-ffreestanding -nostdlib)──> ELF ──┐
                                                   ├─> 退出码比一比
    C ──potato_from──> Potato ──lomt_from --impl──> Loment ──lomentc/lomelf──> ELF ──┘

两边都用 freestanding 的 `_start`（不借 libc）—— 这样"跑出来的数"只由那份 C 决定，
与两边各自的 C 运行时无关。

## 这一组判据覆盖的四个面

1. **`test_translation_runs_same_as_clang`** —— 就是上面那条判据本身。
   语料 `loment/ctrans/sample.c` 里每一段都钉住一个要害（见那个文件的头注）。
2. **`test_body_is_byte_faithful`** —— Potato 里 `functions[i].body` 必须是**原文**。
   这一条同时钉住 `potato_from` 里那个"剥离注释要保长度"的改动：原先把一整段多行注释
   压成一个空格，**长度与行号双双失真**，按那个下标回原文切出来的"正文"是别处的字节。
3. **`test_interface_unit_is_unchanged`** —— **不开 `--impl` 时，带正文的函数照旧发
   `pub extern fn`**。这不是顺手测的：`loment_multilang_test` 那条链靠 `extern fn`
   把实现在编好的 `.o` 里的符号接进来（`docs/179` §2），发了体就变成同一个符号定义两遍。
   正文进了 Potato 之后，"默认那条路会不会被带歪"变成了一个**真问题**，所以有这一条。
4. **`test_out_of_subset_is_loud`** —— 子集外的写法必须**报出来**，而且报得出是哪个函数。
   静默跳过在这里等于产出一份少算一步却照样能编的单元。

用法: python tools/loment_ctrans_test.py     （无 clang/WSL 时 SKIP，退出码 0）
"""
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
import cpptrans    # noqa: E402  (共享核的四门之一, 见 test_bitwise_not_is_loud_*)
import cstrans     # noqa: E402
import ctrans      # noqa: E402
import jtrans      # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato      # noqa: E402
import potato_from  # noqa: E402
import trans_core  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "ctrans"

#: sample.c 的期望退出码：gcd(48,18)=6、classify(0)=0、score(100)=73
#: （1..100 里 15 的倍数 6 个 +3、能 3 不能 5 的 27 个 +1、能 5 不能 3 的 14 个 +2）
#: ⇒ 6*10 + 0 + 73 = 133。**推出来的，不是抄的** —— 抄一个数会把"两边都错成一样"放过去。
WANT_RC = 6 * 10 + 0 + (6 * 3 + 27 * 1 + 14 * 2)


def _coerce_expected() -> int:
    """`loment/ctrans/coerce.c` 里 `f(3, 2)` 的**独立**算法（Python 照 C 语义再写一遍）。

    为什么要多算这一遍：那份语料专门混用 `int` 与条件，两侧的转换**错了也照样编得过**，
    只是数不对。有了这一算，出错时能分清"是翻译错了"还是"两边一起错了"。

    **别小看它**：`while (b)` 那个循环**改掉了 `b`**，而后面的 `a ^ b` 与 `b >> 1`
    都用到它 —— 第一次手算期望值就漏了这个，正是这一条把它兜住的。
    """
    a, b = 3, 2
    x = int(bool(a) and bool(b))
    y = int(bool(a) or bool(b))
    z = int(not a)
    w = int(a < b) + int(a > b) * 2
    if a:
        x += 1
    while b:
        y += 1
        b -= 1
    xorv = a ^ b          # b 已经是 0 了
    shifted = (a << 2) | (b >> 1)
    return (x + y + z + w + xorv + shifted) % 256


def _scoping_expected() -> int:
    """`loment/ctrans/scoping.c` 里 `main()` 的独立算法。

    三样各有各的坑，都推一遍：

    * `grid(4)`  = Σᵢ Σⱼ (4i + j) = Σᵢ (16i + 6) = 16·6 + 4·6 = **120**
    * `shadow(3)`= 循环那个 `i` 遮蔽了外层的 100；累加 0+1+2 = 3，回来再加**外层**
                   的 100 ⇒ **103**（这里最容易错：外层那个 `i` 循环之后还要用）
    * `precedence(5,6,7)` —— C 的优先级是 `*` > `+ -` > `&` > `^` > `|`：
                   `(5 + 6*7 - (5|6)) & 7 ^ 2` = `(47 - 7) & 7 ^ 2` = `40 & 7 ^ 2`
                   = `0 ^ 2` = **2**
    """
    grid = sum(4 * i * 4 + sum(range(4)) for i in range(4))
    shadow = sum(range(3)) + 100
    a, b, c = 5, 6, 7
    precedence = ((a + b * c - (a | b)) & 7) ^ 2
    return (grid + shadow + precedence) % 256


def _numeric_expected() -> int:
    """`loment/ctrans/numeric.c` 里 `main()` 的独立算法（照 C 语义再写一遍）。

    **七个函数各写一遍**，不是把 C 的结果抄过来 —— 抄的话，"翻译器与 clang 一起
    错成同一副样子"就穿不过去了（这一份的五个子结果都得对，最后那个才对）：

    * `isqrt(i * 100)` 求和（i = 1..20）= **610** —— 二进制搜索的上下界收法
    * `digit_sum(987654)` = 9+8+7+6+5+4 = **39**
    * `collatz_steps(27)` = **111**（那个著名的长链）
    * `popcount_range(0, 64)` = Σ_{k<64} popcount(k) = 6·2⁵ = **192**
    * `div_sum(50)` = Σ_{k≤50} d(k) = **207**（50 以内每个数的约数个数之和）

    累起来 1159，落进 `> 800` 那支 ⇒ 1159 − 800 = 359，再 `% 256` = **103**。
    """

    def isqrt(n):
        lo, hi, best = 0, n, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if mid * mid <= n:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def digit_sum(n):
        s = 0
        while n > 0:
            s += n % 10
            n //= 10
        return s

    def collatz_steps(n):
        steps = 0
        while n != 1:
            if n % 2 == 0:
                n //= 2
            else:
                n = n * 3 + 1
            steps += 1
        return steps

    def popcount(x):
        c = 0
        while x != 0:              # 只喂非负数：C 的算术右移在负数上不退位
            c += x & 1
            x >>= 1
        return c

    def popcount_range(lo, hi):
        return sum(popcount(k) for k in range(lo, hi))

    def divisors(n):
        return sum(1 for d in range(1, n + 1) if n % d == 0)

    def div_sum(n):
        return sum(divisors(k) for k in range(1, n + 1))

    total = sum(isqrt(i * 100) for i in range(1, 21))
    total += digit_sum(987654)
    total += collatz_steps(27)
    total += popcount_range(0, 64)
    total += div_sum(50)
    if total > 1500:
        total -= 1500
    elif total > 800:
        total -= 800
    else:
        total -= 400
    return total % 256


#: 语料表：`(文件名, 期望退出码)`。两边都真编真跑，各自的数还要与这里对一次 ——
#: 否则"翻译器与 clang 一起错成同一副样子"会被当成通过。
CORPUS = [
    ("sample.c", WANT_RC),
    ("coerce.c", _coerce_expected()),
    ("scoping.c", _scoping_expected()),
    ("numeric.c", _numeric_expected()),
]

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


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


def _run(exe: Path) -> int:
    r = subprocess.run(
        ["wsl", "-e", "bash", "-lc", f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    return int(r.stdout.strip() or -1)


#: C 侧的入口。**自己写 `_start` 走 60 号系统调用**，不借 libc ——
#: 判据比的是"这份 C 算出什么"，所以两边都不能被各自的运行时影响。
_C_ENTRY = """
static void _loment_exit(long code) {
    __asm__ volatile("syscall" : : "a"(60L), "D"(code) : "rcx", "r11", "memory");
}
void _start(void) { _loment_exit(main()); }
"""

#: Loment 侧的入口。与上面那个语义一样（60 号 = exit），只是写法换成本语言。
_L_ENTRY = """
fn _start() {
    syscall4(60, main() as u64, 0, 0);
}
"""


def _build_c(td: Path, src: str) -> Path:
    """C -> freestanding Linux ELF。`--target` 那一条不能省：Windows 上裸 clang 出 COFF，
    而 WSL 里跑不了（与 `loment_multilang_test` 同一处坑）。"""
    (td / "c_side.c").write_text(src + _C_ENTRY, encoding="utf-8", newline="\n")
    exe = td / "c_side.bin"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-O1",
         "-ffreestanding", "-nostdlib", "-fno-stack-protector",
         "-o", str(exe), str(td / "c_side.c")],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"C 编译失败: {r.stderr[-400:]}"
    return exe


def _build_loment(td: Path, doc: dict) -> Path:
    """Potato ->（`--impl`）-> Loment -> freestanding ELF。"""
    text, _skipped = lomt_from.emit_lomt(doc, impl=True)
    (td / "l_side.lomt").write_text(text + _L_ENTRY, encoding="utf-8", newline="\n")
    p = td / "l_side.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, _info = lomelf.compile_ll(ir, [])
    exe = td / "l_side.elf"
    exe.write_bytes(blob)
    return exe


@test
def test_translation_runs_same_as_clang():
    """**翻译出来的 Loment 跑出的结果 == 直接用 clang 编那份 C 跑出的结果**。

    两边编出来的是**同一种东西**（freestanding ELF，各自一个走 exit 的 `_start`），
    所以两个退出码直接可比。每一份语料还各自与一个**独立算出来的**期望值对一次 ——
    少了那一步的话，"翻译器与 clang 一起错成同一副样子"会被当成通过。

    两份语料分工：`sample.c` 走正常写法（`while` 改形参、`else if`、`for` + `&&`），
    `coerce.c` **专挑 `int`/`bool` 那条缝**（那处的转换错了照样编得过，只是数不对）。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        for name, want in CORPUS:
            src = (EX / name).read_text(encoding="utf-8")
            td = Path(t) / name.replace(".", "_")
            td.mkdir()
            doc, rep = potato_from.from_c(src, name, "strict")
            assert not potato.validate(doc), (name, potato.validate(doc)[:3])
            assert not rep.skipped, f"{name}: 转写那一步就丢了东西: {rep.skipped[:3]}"
            c_rc = _run(_build_c(td, src))
            l_rc = _run(_build_loment(td, doc))
            assert c_rc == want, (
                f"{name}: clang 那边就不对: {c_rc} != {want} —— 语料或期望值错了"
                f"（期望值在 `_coerce_expected` / `WANT_RC` 里，是推出来的）")
            assert l_rc == c_rc, (
                f"{name}: **翻译出来的 Loment 与 clang 编的 C 结果不同**: {l_rc} != {c_rc}")
            print(f"      {name}: clang -> {c_rc}，翻译成 Loment -> {l_rc}（相等）")


@test
def test_body_is_byte_faithful():
    """Potato 里 `functions[i].body` 是**原文逐字节**，不是重新拼出来的。

    拼出来的话，`unsigned` 与 `unsigned int` 都会被 Potato 记成 `u32`，回推必然丢掉
    用户写的那一个拼法（`docs/186` §正文为什么存整段）。这一条同时钉住
    `potato_from` 里那个"剥注释要保长度"的改动 —— 压成空格的话下标就指到别处了。
    """
    src = (EX / "sample.c").read_text(encoding="utf-8")
    doc, _rep = potato_from.from_c(src, "sample.c", "strict")
    got = {f["name"]: f.get("body") for f in doc["functions"]}
    assert set(got) == {"gcd", "classify", "score", "main"}, sorted(got)
    for name, body in got.items():
        assert body, f"{name} 没带上正文"
        # 原文里必须**一字不差**地出现（含缩进与换行）
        assert body in src, f"{name} 的正文不是原文里的一段"
        assert body.startswith(f"int {name}("), body[:40]
        assert body.rstrip().endswith("}"), body[-20:]
    # 只声明不给体的那种**不该**有 body 字段
    decl_only = "int helper(int x);\nint use(int x) { return helper(x); }\n"
    d2, _r2 = potato_from.from_c(decl_only, "d.c", "strict")
    by = {f["name"]: f.get("body") for f in d2["functions"]}
    assert "body" not in by or by.get("helper") is None, by
    assert by.get("use"), "有体的那个必须带上"
    print("      body 与原文逐字节相同；只有声明的不带 body")


@test
def test_c_is_a_grammar_not_a_foreign_module():
    """**一份 C 写法写的单元是 Loment，不是外国货**（`docs/188` §3）。

    ## 2026-09-18：这条测试的前提整个反过来了，所以改写了

    原来叫 `test_interface_unit_is_unchanged`，钉的是："不开 `--impl` 时，带正文的
    函数照旧只发 `pub extern fn`（接口单元）"。那是**旧模型**（把 C 当外源代码）。

    按新模型：C 是**表层语法**之一，用 C 写法写的就是 Loment 函数 —— `language`
    是 `"loment"`、`grammar` 是 `"c"`、**函数不带 `abi`**。所以：

    * 接口那条路对**表层语法**没有意义（没有"外面"这回事），走它会得到"没带正文"；
    * 该走的是 `--impl`，出来的是真 `pub fn`。

    这一条同时钉住那个 bug 的**根因**：`potato_from` 一旦又开始"按文件后缀推 ABI"，
    这里立刻红。
    """
    doc, _rep = potato_from.from_c((EX / "sample.c").read_text(encoding="utf-8"),
                                   "sample.c", "strict")
    # ① 自我描述：**它是 Loment**，只是写法是 C
    assert doc["language"] == "loment", doc["language"]
    assert doc["grammar"] == "c", doc["grammar"]
    for f in doc["functions"]:
        assert "abi" not in f, f"按后缀推出来的 abi 又回来了: {f}"

    # ② 接口那条路：一条 `extern fn` 都不该有 —— 它不是外国函数
    iface, skipped = lomt_from.emit_lomt(doc, impl=False)
    assert "pub extern fn " not in iface, iface[:400]
    assert "pub fn " not in iface, iface[:400]
    for n in ("gcd", "classify", "score", "main"):
        assert n in [x for x, _ in skipped], skipped

    # ③ `--impl`：真 `pub fn`，**一条 `extern fn` 都没有**
    impl, _s2 = lomt_from.emit_lomt(doc, impl=True)
    assert "pub extern fn " not in impl, impl[:400]
    for n in ("gcd", "classify", "score", "main"):
        assert f"pub fn {n}(" in impl, f"{n} 的实现没发出来"
    print("      C 写法 = Loment（language/grammar 对、无 abi）；库走 --impl 出 pub fn")


@test
def test_out_of_subset_is_loud():
    """子集外的写法**报出来**，而且报得出是哪个函数。

    静默跳过在这里等于"产出一份少算一步却照样能编"的单元 —— 那比拒绝坏得多
    （`docs/167`）。所以这一条比的是**报出来了没有**、以及**报的话指不指得到点上**。
    """
    src = (EX / "unsupported.c").read_text(encoding="utf-8")
    doc, _rep = potato_from.from_c(src, "unsupported.c", "strict")
    # 转写那一步照收不误（它只认签名），能不能翻是下一步的事
    assert potato.validate(doc) == [], potato.validate(doc)[:3]
    try:
        lomt_from.emit_lomt(doc, impl=True)
    except lomt_from.NotRepresentable as e:
        msg = str(e)
        assert "switch" in msg, f"报的话里要指出是 `switch`，实得: {msg}"
        # 一份文件里往往十几个函数，只说"子集外"用户不知道该去改哪一个
        assert "带正文的" in msg or "1 个" in msg, f"要说得清范围: {msg}"
        print(f"      子集外报得出: {msg[:90]}…")
        return
    raise AssertionError("`switch` 在子集之外，却一个字都没报 —— 这正是要消灭的静默")


#: 四门共用 `trans_core` 的方言表 —— 共享核里的一处守卫要**四门都验**：
#: 某一门单独"收下"它，就是漏（那正是 `~` 原先的样子）。
_BRACE = [
    ("C", ctrans, "int f(int v) { return ~v; }\n"),
    ("C++", cpptrans, "int f(int v) { return ~v; }\n"),
    ("Java", jtrans,
     "public class T {\n    public static int f(int v) {\n        return ~v;\n    }\n}\n"),
    ("C#", cstrans,
     "class T {\n    static int F(int v) {\n        return ~v;\n    }\n}\n"),
]


@test
def test_bitwise_not_is_loud_in_every_brace_dialect():
    """`~` 在四门**共享核**里都要点名拒 —— 不许"前端收下、产物死在后面"。

    2026-09-18 写 `loment/jtrans/Bits.java` 时撞出来的：`trans_core.unary()` 原先**收下** `~`
    并照发 `(~v)`，而 **Loment 没有按位取反**（一元只有 `-` 与 `!`）。于是产物死在
    **编译器**的词法那一步 —— `非法字符 '~'`，报出来还带着**生成出来那个单元**的行号，
    用户在源码里根本找不到那一行。同一个文件里 `>>>` 却是**点名拒 + 给出路**的：
    一个能过前端、在后面才炸的洞，形状与 `docs/167` 要消灭的那类正是同一种。

    这一条钉三件（两个入口各一次 —— `docs/182` §1.9：一条判据盖不住多个入口）：

      * **四门都拒**：它住在共享核 `trans_core._REJECT_NAMED` 里，某一门单独收下就是漏；
      * **报的话点名 `~`，并给出路**：`~v` 是"与全 1 异或"，要它得**按宽度**写
        （`v ^ -1` / `v ^ 255`）—— 不认识它的人要能照做；
      * **报的是前端**（`Unsupported`），不是翻译完之后编译器报的词法错。
    """
    for label, mod, src in _BRACE:
        try:
            mod.translate(src)
        except trans_core.Unsupported as e:
            msg = str(e)
            assert "~" in msg, f"[{label}] 要点名 `~`: {msg}"
            assert "^ -1" in msg and "^ 255" in msg, (
                f"[{label}] 要给出**按宽度**的两条出路（`v ^ -1` / `v ^ 255`）: {msg}")
            assert "非法字符" not in msg, f"[{label}] 报成了编译器那条: {msg}"
        else:
            raise AssertionError(
                f"[{label}] `~` 本语言没有，前端却一个字都没报 —— 产物会在后面炸")
    # 另一处入口：**前门**（`.java` 文件 → 该交给编译器的 Loment 源）。它走的不是
    # `translate()` 那条路，所以另钉一次 —— 症状正是从这里冒出来的（错的行号 + 词法错）。
    with tempfile.TemporaryDirectory() as tds:
        f = Path(tds) / "Tilde.java"
        f.write_text(_BRACE[2][2], encoding="utf-8", newline="\n")
        try:
            potato_from.front_door(f)
        except lomt_from.NotRepresentable as e:
            assert "~" in str(e) and "^ -1" in str(e), f"前门也要点名并给出路: {e}"
        else:
            raise AssertionError("前门把 `~` 放过去了（应当在这一步就拒）")
    print(f"      `~` 四门共享核都点名拒 + 给出路；前门那一处也拒")



# ---------------------------------------------------------------- Loment 版（S1 第十六格）

TWIN = ROOT / "loment" / "tools" / "lompotc.lomt"

#: 判据自己带的那批小输入 —— 每一份钉住一条**扫描规则**（语料那五份没覆盖到的那些）：
#: struct（含一行写完、多字段、字段是数组、字段是另一个 struct）、enum（不带值 / 带值 /
#: 带负值）、修饰词（`static` / `inline` / `const` 形参）、指针与 `void *`、`size_t`/`uint32_t`
#: 这类别名、全局变量（跳过）、预处理指令、注释（抹成等长空白）、只有声明的函数、
#: 跨行的签名、`(void)`、空单元、花括号不配平、`unsigned` / `signed` 单独写。
_BATTERY = {
    "struct": "struct P { int a; int b; };\nint f(struct P p) { return p.a; }\n",
    "struct_line": "struct P { int a; int b; };\n",
    "struct_arr": "struct B { unsigned char data[64]; int n; };\n",
    "struct_multi": "struct A { int x; };\nstruct B { struct A a; int y; };\n",
    "enum_plain": "enum Color { RED, GREEN, BLUE };\nint f() { return RED; }\n",
    "enum_val": "enum E { A = 3, B, C = 7 };\nint g() { return A; }\n",
    "enum_neg": "enum E { X = -1, Y };\n",
    "modifiers": "static int s(int a) { return a; }\ninline int i(int a) { return a; }\n",
    "const_param": "int f(const int v) { return v; }\n",
    "pointers": "char *name(void) { return 0; }\nvoid *raw(void) { return 0; }\n",
    "typedef_std": "uint32_t f(size_t n) { return n; }\n",
    "global_var": "int g = 3;\nint f() { return 1; }\n",
    "preproc": "#include <stdio.h>\n#define X 1\nint f() { return X; }\n",
    "comments": "// leading\nint f(int a) {\n    /* mid */ return a; // tail\n}\n",
    "decl_only": "int helper(int x);\nint use(int x) { return helper(x); }\n",
    "multiline_sig": "int f(\n    int a,\n    int b\n) {\n    return a;\n}\n",
    "void_param": "int f(void) { return 1; }\n",
    "empty_unit": "// nothing here\n",
    "incomplete": "int f(int a) {\n    return a;\n",
    "unsigned_variants": "unsigned f() { return 1; }\nunsigned int g() { return 2; }\nlong h() { return 3; }\n",
    "keyword_like": "int if_like(int a) { return a; }\n",
    "nested_braces": "int f(int a) { if (a) { return 1; } return 0; }\n",
}


#: C++ 那一侧的小输入。**与 C 那张表只差 `char` 那三个与 `long long` 两条**：
#: C++ 的 `char` 符号性实现定义，所以**不映**（让它出声）；`long long` 那两条 C 没有。
_CPP_BATTERY = {
    "cpp_char_undef": "char f(char c) { return c; }\n",
    "cpp_char_ptr_undef": "const char *s(void) { return 0; }\n",
    "cpp_longlong": "long long f(long long n) { return n; }\n",
    "cpp_ull": "unsigned long long f() { return 1; }\n",
    "cpp_signed_unsigned_char": "signed char a(signed char c) { return c; }\nunsigned char b() { return 2; }\n",
    "cpp_void_ptr": "void *raw(void) { return 0; }\n",
    "cpp_struct": "struct P { long long a; int b; };\nint f(struct P p) { return p.b; }\n",
    "cpp_enum": "enum E { A, B };\nint f() { return A; }\n",
    "cpp_undef_char_param": "int f(char c) { return 1; }\n",
}


def _twin_exe(td: Path) -> Path:
    """把 `lompotc.lomt` 链成可执行文件（走仓库自己的原生后端，不经 clang）。

    它只吃源码、只用 open/read/write/brk/exit 五个系统调用，所以能在本机**原生**跑。
    """
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lompotc.lomt 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / ("lompotc.exe" if os.name == "nt" else "lompotc")
    exe.write_bytes((lomelf.compile_pe(ir) if os.name == "nt" else lomelf.compile_ll(ir))[0])
    exe.chmod(0o755)
    return exe


@test
def test_lompotc_twin_matches_from_c():
    """**C 前端有了 Loment 版**（S1 第十六格，§4.1 那根轴的第一半）。

    同一份 C 源喂两边：`potato_from.from_c` 与 `lompotc.lomt`（链成可执行文件后跑），
    比的是**产出的 Potato JSON 逐字节相同**。

    **这一格比的是文本，不是数** —— 与这个文件上面那条"比数"的判据分工不同：
    那条问"翻出来的程序跑出什么"，这条问"前端把源读成了什么"。两者都要有：
    文本一致而数不对 = 后面的发射器错了；数对而文本不同 = 前端在自作主张。

    覆盖面：`loment/ctrans/` 五份语料 + `_BATTERY` 那 22 份小输入。
    **只有前端那一半**：`lomt_from`（Potato -> Loment 源）还没有 Loment 版，
    所以这一格不碰它；`lompotc.lomt` 的文件头写着同一句话。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str]] = []
        for f in sorted(EX.glob("*.c")):
            cases.append((f.name, f.read_text(encoding="utf-8")))
        cases += [(k + ".c", v) for k, v in sorted(_BATTERY.items())]
        bad = []
        for name, src in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            want = json.dumps(potato_from.from_c(src, name, "strict")[0],
                              ensure_ascii=False)
            r = subprocess.run([str(exe), str(fp)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=False, timeout=120)
            if r.returncode != 0 or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (f"{len(bad)}/{len(cases)} 份与 from_c 不同（前 2）:\n"
                         + "\n".join(f"  {n}: rc={rc}\n    py={w!r}\n    tw={g!r}"
                                      for n, rc, w, g in bad[:2]))
        print(f"      {len(cases)} 份 C：Loment 前端与 `potato_from.from_c` "
              f"产出的对象逐字节相同")


@test
def test_lompotc_twin_matches_from_cpp():
    """**C++ 那一门也在同一份孪生里**（`lompotc --cpp`）。

    上游两门是同一个引擎（`_from_c(..., grammar, types)`），差异只有**类型表**那一张：
    C++ 把 `char` / `char *` / `const char *` 映成**无映射**（`char` 的符号性在 C++ 里是
    实现定义的，表示层不该猜），另外多认 `long long` / `unsigned long long`。
    所以这一条同时钉住"**命中但无映射**"与"命中且有效"必须分开报 ——
    两者混了的话，`char` 会被后面的指针分支接走判成 `str`，而那正是那一格要拦住的猜测。

    覆盖面：`loment/cpptrans/` 的语料 + `_CPP_BATTERY`。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str]] = []
        for f in sorted((ROOT / "loment" / "cpptrans").glob("*.cpp")):
            cases.append((f.name, f.read_text(encoding="utf-8")))
        cases += [(k + ".cpp", v) for k, v in sorted(_CPP_BATTERY.items())]
        bad = []
        for name, src in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            want = json.dumps(potato_from.from_cpp(src, name, "strict")[0],
                              ensure_ascii=False)
            r = subprocess.run([str(exe), "--cpp", str(fp)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               shell=False, timeout=120)
            if r.returncode != 0 or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (f"{len(bad)}/{len(cases)} 份与 from_cpp 不同（前 2）:\n"
                         + "\n".join(f"  {n}: rc={rc}\n    py={w!r}\n    tw={g!r}"
                                      for n, rc, w, g in bad[:2]))
        print(f"      {len(cases)} 份 C++：Loment 前端与 `potato_from.from_cpp` "
              f"产出的对象逐字节相同")


#: Java 那一门（`_from_class_lang`）自己带的输入。每一份钉住一条**类体切分**或
#: **方言表**的规则 —— 语料那三份是"真程序"，这些小输入是"规则的边界"。
_JAVA_BATTERY = {
    # ---- 类体切分：成员只收**深度 0** 上成段的那些
    "cls_field": ("class A {\n    public int x;\n    private String s;\n}\n"),
    "cls_no_field": ("class A {\n    public int f() { return 1; }\n}\n"),
    "cls_field_unknown": "class A {\n    private Foo f;\n}\n",
    "cls_inner_class": ("class A {\n    class B {\n        public int x;\n    }\n}\n"),
    "cls_two": ("class A {\n    public int x;\n}\nclass B {\n    public int y;\n}\n"),
    "cls_nested_brace_field": "class A {\n    public int[] xs = {1, 2, 3};\n}\n",
    "cls_annot": ("class A {\n    @Override\n    public int f() { return 1; }\n}\n"),
    "cls_unbalanced": "class A {\n    public int x;\n",
    "cls_trailing_junk": "class A {\n    public int x;\n    \n",
    "cls_pkg_import": ("package p;\n\nimport java.util.List;\n\n"
                       "public class A {\n    public int x;\n}\n"),
    "cls_interface_ignored": "public interface I {\n    int f();\n}\n",
    "cls_abstract": "public abstract class A {\n    public int x;\n}\n",
    "cls_final": "public final class A {\n    public int x;\n}\n",
    "cls_final_static_mods": "class A {\n    static final class B {\n        public int x;\n    }\n}\n",
    # ---- 类型表
    "ty_scalars": ("class A {\n    public boolean b;\n    public char c;\n    public byte y;\n"
                   "    public short s;\n    public long l;\n    public int i;\n}\n"),
    "ty_arrays": "class A {\n    public int[] xs;\n    public String[][] names;\n}\n",
    "ty_generic_field": ("class A {\n    public List<String> xs;\n"
                         "    public Map<String, int> m;\n}\n"),
    "ty_boxed_field": "class A {\n    public Integer n;\n    public double d;\n}\n",
    "ty_unknown_field": "class A {\n    public int ok;\n    public Foo bad;\n}\n",
    "ty_mods": ("class A {\n    private transient int t;\n"
                "    private volatile boolean v;\n    public static int k;\n}\n"),
    "ty_loment_names": "class A {\n    public u32 x;\n    public i64 y;\n}\n",
    # ---- 常量
    "k_plain": "class A {\n    public static final int K = 7;\n}\n",
    "k_neg": "class A {\n    static final int K = -12;\n}\n",
    "k_zeros": "class A {\n    private static final long Z = 0000;\n}\n",
    "k_big": "class A {\n    static final long B = 4294967296;\n}\n",
    "k_str": "class A {\n    static final String S = \"hi\";\n}\n",
    "k_dup": ("class A {\n    static final int K = 1;\n}\n"
              "class B {\n    static final int K = 2;\n}\n"),
    "k_char_const": "class A {\n    static final char C = 65;\n}\n",
    "k_void_const": "class A {\n    static final void V = 1;\n}\n",
    # ---- 方法
    "m_native_semi": ("class A {\n    public native int j_native(int a);\n"
                      "    public int x;\n}\n"),
    "m_abstract_semi": "class A {\n    public abstract int f();\n}\n",
    "m_ctor": ("class A {\n    public A(int v) { }\n    public int f() { return 1; }\n}\n"),
    "m_overload_ctor": "class A {\n    A() { }\n}\n",
    "m_void": "class A {\n    public synchronized void f() { }\n    static void g() { }\n}\n",
    "m_multi_param": ("class A {\n    public int f(int a, String b, char c) { return a; }\n}\n"),
    "m_bad_param": "class A {\n    public int f(int) { return 1; }\n}\n",
    "m_final_param": "class A {\n    public int f(final int x) { return x; }\n}\n",
    "m_generic_method": "class A {\n    public <T> T get() { return 0; }\n}\n",
    "m_generic_ret": "class A {\n    public List<String> get() { return 0; }\n}\n",
    "m_array_ret": "class A {\n    public int[] f(int[] a) { return a; }\n}\n",
    "m_bad_ret": "class A {\n    public double f() { return 1; }\n}\n",
    # ---- 字符串字面量与注释：抹它们的**顺序**（先换字面量、后抹注释）
    "s_strlit": ("class A {\n    public int f() {\n"
                 "        String s = \"a{b}c;\";\n        return 1;\n    }\n}\n"),
    "s_cmt_brace": ("class A {\n    // } 这一段里的花括号不该配平\n"
                    "    public int f() { /* { */ return 1; }\n}\n"),
    "s_cmt_cjk": ("/* 中文注释，方法在下面 */\nclass A {\n"
                  "    public int f(int a) {\n        return a;\n    }\n}\n"),
    "s_strlit_cjk": ("class A {\n    public int f() {\n"
                     "        String s = \"中文\";\n        return 1;\n    }\n}\n"),
    # ---- 枚举
    "e_plain": "class A {\n    public enum St { ON, OFF }\n    private St s;\n}\n",
    "e_ctor_args": "class A {\n    enum E { A(1), B(2), C { void f() {} } }\n}\n",
    "e_empty": "class A {\n    enum E { }\n}\n",
    "e_after_class": "class A {\n    private St s;\n}\nenum St { ON }\n",
    "e_public_static": ("class A {\n    public static enum E { X, Y }\n}\n"),
    "e_trailing_comma": "class A {\n    enum E { A, B, }\n}\n",
}


@test
def test_lompotc_twin_matches_from_java():
    """**Java 那一门也有 Loment 版了**（`lompotc --java`，S1 第十六格的续）。

    上游 `_from_class_lang` 是"函数住在 `class X { … }` 里"这一族的共用引擎：
    类体按花括号**配平切成员**（深度 0 上遇 `;` 或一段完整的 `{…}` 收一个），
    成员按 **常量 -> 方法 -> 字段** 的顺序判（方法在前是硬要求：抽象/native 方法
    以 `;` 结尾却带参数表，反过来判就会把它们**静默丢掉**），类型走另一张表。

    两处**不能想当然**的地方，这一条各钉一份：
      * `body_line` —— 上游 `_body_at` 用的是**剥过字面量的 body** 的**字符**下标，
        数的却是**原文**里的换行；`jtrans/*.java` 里有中文，字节与字符口径差着
        每个多字节字符多出来的字节，`s_cmt_cjk` / `s_strlit_cjk` 就是钉这个的。
      * `_STR_LIT` 先于 `_C_COMMENT`（字面量换成 `""` 会**改长度**），`s_strlit` 钉它。

    覆盖面：`loment/jtrans/` 三份语料 + `_JAVA_BATTERY`。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str]] = []
        for f in sorted((ROOT / "loment" / "jtrans").glob("*.java")):
            cases.append((f.name, f.read_text(encoding="utf-8")))
        cases += [(k + ".java", v) for k, v in sorted(_JAVA_BATTERY.items())]
        bad = []
        for name, src in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            want = json.dumps(potato_from.from_java(src, name, "strict")[0],
                              ensure_ascii=False)
            r = subprocess.run([str(exe), "--java", str(fp)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               shell=False, timeout=120)
            if r.returncode != 0 or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (f"{len(bad)}/{len(cases)} 份与 from_java 不同（前 2）:\n"
                         + "\n".join(f"  {n}: rc={rc}\n    py={w!r}\n    tw={g!r}"
                                      for n, rc, w, g in bad[:2]))
        print(f"      {len(cases)} 份 Java：Loment 前端与 `potato_from.from_java` "
              f"产出的对象逐字节相同")


#: C# 那一门（与 Java **同一份引擎**，只是方言表不同）自己带的输入。每一份钉住
#: **两门真正不同的那一处**：`const` 对 `static final`、`struct` 也在形状里、
#: 修饰词表那一张（`internal` / `sealed` / `partial` / `async` …），以及 **`byte` 的符号性**。
_CS_BATTERY = {
    # ---- 形状：class / struct / namespace / Allman
    "cs_class": "public class A {\n    public int x;\n}\n",
    "cs_struct": "public struct Frame {\n    public byte tag;\n}\n",
    "cs_struct_mods": "internal readonly struct P {\n    public int x;\n}\n",
    "cs_struct_empty": "public struct S { }\n",
    "cs_static_class": "public static class A {\n    public const int K = 1;\n}\n",
    "cs_namespace_allman": ("using System;\n\nnamespace LomentDemo\n{\n"
                            "    public class A\n    {\n        public int x;\n    }\n}\n"),
    "cs_interface_ignored": "public interface I {\n    int F();\n}\n",
    "cs_nested_struct": ("public struct Outer\n{\n"
                         "    public struct Inner { public int x; }\n"
                         "    public int y;\n}\n"),
    # ---- 类型表：这一族里**唯一**没落在同一格上的是 `byte`
    "cs_byte_unsigned": "class A {\n    public byte b;\n    public sbyte s;\n}\n",
    "cs_numeric": ("class A {\n    public ushort a;\n    public uint b;\n"
                   "    public ulong c;\n    public short d;\n    public long e;\n}\n"),
    "cs_bool_string_char": ("class A {\n    public bool a;\n    public string b;\n"
                            "    public char c;\n}\n"),
    "cs_javawords": "class A {\n    public String s;\n    public boolean b;\n}\n",
    "cs_generic_field": "class A {\n    public List<int> xs;\n}\n",
    "cs_readonly_field": "class A {\n    private readonly int x;\n}\n",
    # ---- 常量：C# 是 `const`（隐含 static），没有 `static final` 那一截
    "cs_const": "class A {\n    public const int K = 5;\n}\n",
    "cs_const_internal": "class A {\n    internal const long K = -3;\n}\n",
    "cs_const_no_mod": "class A {\n    const int K = 7;\n}\n",
    "cs_static_final_is_not_const": "class A {\n    static final int K = 5;\n}\n",
    "cs_const_string": "class A {\n    public const string S = \"x\";\n}\n",
    "cs_const_expr": "class A {\n    public const int K = 1 + 2;\n}\n",
    # ---- 方法/字段的修饰词表（认不出来 = **静默丢一个函数**）
    "cs_internal_method": "class A {\n    internal static int F() { return 1; }\n}\n",
    "cs_sealed_override": "class A {\n    public sealed override int F() { return 1; }\n}\n",
    "cs_async": "class A {\n    public async int F() { return 1; }\n}\n",
    "cs_virtual_void": "class A {\n    public virtual void F() { }\n}\n",
    "cs_ctor": "class A {\n    public A() { }\n    public int f() { return 1; }\n}\n",
    "cs_expr_body": "class A {\n    public int F() => 1;\n}\n",
    "cs_multi_declarator": "class A {\n    public int a, b;\n}\n",
    "cs_attribute": "class A {\n    [Obsolete]\n    public int x;\n}\n",
    "cs_generic_method": "class A {\n    public T Get<T>(int a) { return a; }\n}\n",
    # ---- 枚举（与 Java 同形）
    "cs_enum": "class A {\n    public enum E { X, Y }\n    private E e;\n}\n",
    "cs_struct_enum": "struct S {\n    enum E { A }\n    private E v;\n}\n",
}


@test
def test_lompotc_twin_matches_from_csharp():
    """**C# 那一门也在同一份孪生里**（`lompotc --csharp`）。

    上游 `from_csharp` 与 `from_java` **共用** `_from_class_lang`（`docs/188` §7.1 的
    "一份解析器 + 方言表"），差异只有四张正则与一张类型表：

      * 声明形状：C# 多收 `struct`（`public struct Frame { public byte tag; }` ——
        只收 `class` 的话整个类型**静默消失**），而且修饰词是**一整张反复的表**
        （连 `public` 都在表里），与 Java 那个"可选 `public` + 反复的 final/abstract"不同形；
      * 常量：C# 是 `const`（**隐含 static**），Java 是 `static final`；
      * 成员修饰词表：C# 的 `internal` / `sealed` / `partial` / `async` 不在 Java 那张里
        —— 共用一张的话一条 `internal static int F()` 会掉进"以 `;` 结尾 = 字段"那支
        被丢掉（**静默丢一个函数**）；
      * 类型表：**`byte` 是两门唯一没落在同一格上的**（C# 无符号 ⇒ `u8`，Java 有符号
        ⇒ `i8`）；另外 C# 有 `sbyte`/`ushort`/`uint`/`ulong`/`string`，而 Java 的
        `String`/`boolean` 在 C# 里**不该**映射。

    覆盖面：`loment/cstrans/` 三份语料 + `_CS_BATTERY`。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str]] = []
        for f in sorted((ROOT / "loment" / "cstrans").glob("*.cs")):
            cases.append((f.name, f.read_text(encoding="utf-8")))
        cases += [(k + ".cs", v) for k, v in sorted(_CS_BATTERY.items())]
        bad = []
        for name, src in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            want = json.dumps(potato_from.from_csharp(src, name, "strict")[0],
                              ensure_ascii=False)
            r = subprocess.run([str(exe), "--csharp", str(fp)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               shell=False, timeout=120)
            if r.returncode != 0 or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (f"{len(bad)}/{len(cases)} 份与 from_csharp 不同（前 2）:\n"
                         + "\n".join(f"  {n}: rc={rc}\n    py={w!r}\n    tw={g!r}"
                                      for n, rc, w, g in bad[:2]))
        print(f"      {len(cases)} 份 C#：Loment 前端与 `potato_from.from_csharp` "
              f"产出的对象逐字节相同")


#: Go 那一门自己带的输入。上游是**正则派**（`_GO_STRUCT` / `_GO_FN` / `_GO_EXPORT`），
#: 每一份钉住一条与别门不同的规则 —— 尤其是 `struct` 的体**不是**花括号配平出来的、
#: 形参的**分组写法**、以及 `//export` 才记 `abi` 且**不带正文**。
_GO_BATTERY = {
    # ---- 函数：形状、返回类型、正文与行号
    "go_simple": "package main\n\nfunc f(n int) int {\n\treturn n\n}\n",
    "go_noret": "package main\n\nfunc f() {\n}\n",
    "go_noparam": "package main\n\nfunc f() int {\n\treturn 1\n}\n",
    "go_ptr_ret": "package main\n\nfunc f(p *int) *int {\n\treturn p\n}\n",
    "go_slice_ret": "package main\n\nfunc f() []int {\n\treturn nil\n}\n",
    "go_multi_ret": "package main\n\nfunc f() (int, error) {\n\treturn 0, nil\n}\n",
    "go_named_ret": "package main\n\nfunc f() (n int) {\n\treturn 1\n}\n",
    "go_variadic": "package main\n\nfunc f(xs ...int) {\n}\n",
    "go_receiver": "package main\n\ntype T struct {\n\tx int\n}\n\nfunc (t T) M() int {\n\treturn t.x\n}\n",
    "go_allman_brace": "package main\n\nfunc f() int\n{\n\treturn 1\n}\n",
    "go_bad_param": "package main\n\nfunc f(x func(int) int) {\n}\n",
    # ---- 形参：**分组写法**（Go 里极常见）
    "go_group": "package main\n\nfunc f(a, b int) int {\n\treturn a\n}\n",
    "go_group_mixed": ("package main\n\nfunc f(a, b int, c string, d bool) int {\n"
                        "\treturn a\n}\n"),
    "go_group_only_names": "package main\n\nfunc f(a, b) {\n}\n",
    "go_group_unknown": "package main\n\nfunc f(a, b float64) int {\n\treturn 0\n}\n",
    "go_known_type_param": ("package main\n\ntype P struct {\n\tx int\n}\n\n"
                            "func f(p P) int {\n\treturn p.x\n}\n"),
    # ---- `//export`：**源码显式宣称的 C ABI**，记 abi 且**不带正文**
    "go_export": "package main\n\n//export add\nfunc add(a, b int) int {\n\treturn a\n}\n",
    "go_export_other": "package main\n\n//export other\nfunc add(a int) int {\n\treturn a\n}\n",
    "go_export_prefix": "package main\n\n//export adder\nfunc add(a int) int {\n\treturn a\n}\n",
    # ---- struct：体的终点是**第一个以 `}` 起头的行**（不是配平）
    "go_struct": "package main\n\ntype P struct {\n\tx int\n\ty bool\n}\n",
    "go_struct_one_line": "package main\n\ntype P struct {\n\tx int\n}\n",
    "go_struct_same_line": "package main\n\ntype P struct { x int }\n",
    "go_struct_empty": "package main\n\ntype P struct {\n}\n",
    "go_struct_unmapped": "package main\n\ntype P struct {\n\tf float64\n}\n",
    "go_struct_partial": "package main\n\ntype P struct {\n\tx int\n\tf float64\n\ts string\n}\n",
    "go_struct_ptr_slice": "package main\n\ntype P struct {\n\tp *int\n\txs []int\n}\n",
    "go_struct_comment": "package main\n\ntype P struct {\n\t// 注释行不算字段\n\tx int\n}\n",
    "go_struct_nested": ("package main\n\ntype P struct {\n\tInner struct {\n\t\ty int\n\t}\n"
                          "\tx int\n}\n"),
    "go_struct_unknown_ref": "package main\n\ntype P struct {\n\tinner Q\n}\n",
    "go_alias_ignored": "package main\n\ntype X = int\n\nfunc f(x X) X {\n\treturn x\n}\n",
    "go_cjk_comment": ("package main\n\n/* 中文注释，函数在下面 */\n"
                        "func f(n int) int {\n\treturn n\n}\n"),
}


@test
def test_lompotc_twin_matches_from_go():
    """**Go 那一门也在同一份孪生里**（`lompotc --go`）。

    上游 `from_go` 也是**轻量解析那一派**（三条正则），但与 C/C++/Java/C# 都不同：
    类型写在**名字后面**、条件不带括号、没有 `while`。三处必须照抄的：

      * `struct` 的体**不是**花括号配平出来的 —— `(.∗?)^[ \t]*\\}` 到**第一个以 `}`
        起头的行**为止（所以 `type P struct { x int }` 一行写完的那种**根本不匹配**，
        而嵌套 struct 会被截在里层那个 `}` 上）；
      * 形参的**分组写法** `func gcd(a, b int)`（Go 里极常见）：只有名字的那一段先攒着，
        等后面 `名字 类型` 那一段把类型带过来；攒到最后还没等到 ⇒ 拒；
      * `//export` 是**源码里显式宣称的 C ABI**（`docs/188` §3）—— 只有那种才记
        `abi: "c"`，而且只有**没记 abi** 的才带正文（`--impl` 那条路用）。

    覆盖面：`loment/gotrans/` 的语料 + `_GO_BATTERY`。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str]] = []
        for f in sorted((ROOT / "loment" / "gotrans").glob("*.go")):
            cases.append((f.name, f.read_text(encoding="utf-8")))
        cases += [(k + ".go", v) for k, v in sorted(_GO_BATTERY.items())]
        bad = []
        for name, src in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            want = json.dumps(potato_from.from_go(src, name, "strict")[0],
                              ensure_ascii=False)
            r = subprocess.run([str(exe), "--go", str(fp)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               shell=False, timeout=120)
            if r.returncode != 0 or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (f"{len(bad)}/{len(cases)} 份与 from_go 不同（前 2）:\n"
                         + "\n".join(f"  {n}: rc={rc}\n    py={w!r}\n    tw={g!r}"
                                      for n, rc, w, g in bad[:2]))
        print(f"      {len(cases)} 份 Go：Loment 前端与 `potato_from.from_go` "
              f"产出的对象逐字节相同")


@test
def test_lompotc_twin_selfhost_compiles():
    """`lompotc.lomt` 必须能走**种子自举链**编译，且产出的 IR 与参考实现**逐字节相同**。"""
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang")
        return
    import loment_dist  # noqa: E402
    stage1 = loment_dist.build_stage1()
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = subprocess.run([str(stage1), TWIN.relative_to(ROOT).as_posix()], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译 lompotc.lomt 失败: {r.stderr[-400:]}"
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)"
    print(f"      种子自举链编译 lompotc.lomt 成功，且 IR 与参考逐字节相同 ({len(want)}B)")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_ctrans_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
