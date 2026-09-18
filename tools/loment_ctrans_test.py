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

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato      # noqa: E402
import potato_from  # noqa: E402

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


#: 语料表：`(文件名, 期望退出码)`。两边都真编真跑，各自的数还要与这里对一次 ——
#: 否则"翻译器与 clang 一起错成同一副样子"会被当成通过。
CORPUS = [
    ("sample.c", WANT_RC),
    ("coerce.c", _coerce_expected()),
    ("scoping.c", _scoping_expected()),
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
def test_interface_unit_is_unchanged():
    """**不开 `--impl` 时，带正文的函数照旧只发接口。**

    这一条防的是"正文进了 Potato 之后，默认那条路被悄悄带歪" —— 而
    `loment_multilang_test` 正是靠默认那条路（`extern fn` + 外面编好的 `.o`）跑起来的。
    发了体就是同一个符号定义两遍，那是**链接期**的错，离这里很远，所以要在源头钉住。
    """
    doc, _rep = potato_from.from_c((EX / "sample.c").read_text(encoding="utf-8"),
                                   "sample.c", "strict")
    iface, skipped = lomt_from.emit_lomt(doc, impl=False)
    assert "pub fn " not in iface, "默认那条路不该发出任何实现:\n" + iface[:400]
    for n in ("gcd", "classify", "score", "main"):
        assert f"pub extern fn {n}(" in iface, f"{n} 的接口没发出来:\n{iface[:400]}"
    # **正文不该被当成"跳过项"**：它没有丢，只是这条路用不上（`docs/179` §2 的接口单元）
    assert not skipped, f"默认这条路上不该有跳过项（正文不是被丢的东西）: {skipped}"
    impl, _s2 = lomt_from.emit_lomt(doc, impl=True)
    assert "pub extern fn " not in impl, "带体的那几个不该同时发接口:\n" + impl[:400]
    for n in ("gcd", "classify", "score", "main"):
        assert f"pub fn {n}(" in impl, f"{n} 的实现没发出来"
    print("      默认只发接口（无跳过项）；--impl 只发实现")


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
