#!/usr/bin/env python3
"""loment_pytrans_test.py — **Python 翻成 Loment，跑出来的数一样**（`docs/187`）。

判据与 C 那门（`loment_ctrans_test.py`）**同一条** —— 用户定的：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接用 clang 编那份 C 跑出的结果」

这一门把"直接编那份 C"换成"直接用 CPython 跑那份 Python"：

    Python ──CPython──> main() 的返回值 ──┐
                                          ├─> 比一比
    Python ──potato_from──> Potato ──lomt_from --impl──> Loment ──> 退出码 ──┘

**比的是数，不是文本。**

## 这一门与 C 那门的三处语义差，各有一条语料钉住

| 差 | 语料 |
|---|---|
| Python 是**函数级**作用域，Loment 的 `let` 是**块级** | `scope.py` |
| `//` `%` Python 向下取整、Loment 向零截断 | `floordiv.py`（还比了"直译会得到什么"） |
| `and`/`or` 返回**操作数** | `unsupported.py`（**应当被拒**，不是应当翻对） |

用法: python tools/loment_pytrans_test.py    （无 CPython/WSL 时 SKIP，退出码 0）
"""
from __future__ import annotations

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
import pytrans     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "pytrans"

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _python() -> str | None:
    for n in ("python3", "python"):
        p = shutil.which(n)
        if p:
            return p
    return None


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
        ["wsl", "-e", "bash", "-lc",
         f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    return int(r.stdout.strip() or -1)


def _cpython(src: Path) -> int:
    """CPython 跑那份源码的 `main()` —— **对照组**。"""
    return int(subprocess.run(
        [_python(), "-c",
         "import runpy,sys;m=runpy.run_path(sys.argv[1]);print(m['main']())", str(src)],
        capture_output=True, text=True, timeout=120, shell=False).stdout.strip() or -1)


#: Loment 侧的入口：60 号系统调用 = exit。
_L_ENTRY = """
fn _start() {
    syscall4(60, main() as u64, 0, 0);
}
"""


def _build_loment(td: Path, doc: dict) -> Path:
    text, _skipped = lomt_from.emit_lomt(doc, impl=True)
    p = td / "l_side.lomt"
    p.write_text(text + _L_ENTRY, encoding="utf-8", newline="\n")
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / "l_side.elf"
    exe.write_bytes(blob)
    return exe


# ---------------------------------------------------------------- 期望值：独立推出来的
#
# **推，不是抄** —— 抄一个数会把"两边一起错成同一副样子"放过去。每一条都在这里
# 用 Python 重写一遍那份程序的意思（**含 Python 的** `//` `%` 语义，因为那是源语言的语义）。


def _policy_expected() -> int:
    """`policy.py`。`level(1000)=2`、`bucket(-7,2) = -7 // 2 = -4`、累加 `0+2+4+6=12`。"""
    a = 2
    b = -7 // 2                       # Python 的向下取整
    acc = 0
    for i in range(4):
        acc += i * a
    return (a * 10 + (b + 20) + acc) % 200


def _floordiv_expected() -> int:
    """`floordiv.py`。四个符号组合的商与余数 —— 照 Python 的语义。"""
    q1, q2 = -7 // 2, 7 // -2
    m1, m2 = -7 % 2, 7 % -2
    return (q1 + q2 + 20) * 3 + (m1 + m2 + 20)


def _floordiv_naive() -> int:
    """同一份语料**若照 Loment 的原生语义直译**会得到什么 —— 向零截断。

    这一条是用来说清"那份语料真的踩在缝上"的：两个数**必须不同**，否则 `floordiv.py`
    只是在摆样子，而 `__py_mod` / `__py_floordiv` 那对辅助函数也就没被测到。
    """
    def cdiv(a, b):                    # C 的向零截断
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q

    def cmod(a, b):
        return a - cdiv(a, b) * b

    q1, q2 = cdiv(-7, 2), cdiv(7, -2)
    m1, m2 = cmod(-7, 2), cmod(7, -2)
    return (q1 + q2 + 20) * 3 + (m1 + m2 + 20)


def _scope_expected() -> int:
    """`scope.py`。`walk(1) = 10 + (0+1+2) + 2 = 15`、`walk(0) = 20 + 3 + 2 = 25`。"""
    def walk(c):
        x = 10 if c else 20
        total = 0
        for i in range(3):
            total += i
        return x + total + i           # `i` 在循环之后**还活着**（Python 的 for 目标不外逃出函数）
    return walk(1) + walk(0)


#: 语料表：`(文件名, 期望值)`。期望值全部推出来（见上面那几条）。
CORPUS = [
    ("policy.py", _policy_expected()),
    ("floordiv.py", _floordiv_expected()),
    ("scope.py", _scope_expected()),
]


@test
def test_translation_runs_same_as_cpython():
    """**翻译出来的 Loment 跑出的结果 == 直接跑那份 Python 的结果**（`docs/187` 的判据）。

    三份语料各自钉住一处语义差（见文件头那张表），任一处理错都会把数改掉 ——
    而**每一处错了都照样编得过**，这正是必须有这条判据的理由。
    """
    if not _wsl() or not _python():
        print("      SKIP: 需要 WSL + CPython")
        return
    with tempfile.TemporaryDirectory() as t:
        for name, want in CORPUS:
            src = EX / name
            td = Path(t) / name.replace(".", "_")
            td.mkdir()
            doc, rep = potato_from.from_python(src.read_text(encoding="utf-8"),
                                               name, "strict")
            assert not potato.validate(doc), (name, potato.validate(doc)[:3])
            assert not rep.skipped, f"{name}: 转写那一步就丢了东西: {rep.skipped[:3]}"
            py_rc = _cpython(src)
            l_rc = _run(_build_loment(td, doc))
            assert py_rc == want, (
                f"{name}: CPython 那边就不对: {py_rc} != {want} —— 语料或期望值错了")
            assert l_rc == py_rc, (
                f"{name}: **翻译出来的 Loment 与 CPython 结果不同**: {l_rc} != {py_rc}")
            print(f"      {name}: CPython -> {py_rc}，翻译成 Loment -> {l_rc}（相等）")

    # 单独一条：那份 `//` `%` 语料**必须真的踩在缝上**
    naive = _floordiv_naive()
    want = _floordiv_expected()
    assert naive != want, (
        f"floordiv.py 的直译结果是 {naive}、正确结果是 {want} —— **一样**，"
        f"说明这份语料没踩到 `//` `%` 的符号差上，`__py_mod`/`__py_floordiv` 没被测到")
    print(f"      floordiv.py 直译（向零截断）会得到 {naive}，"
          f"Python 是 {want} —— 差 {abs(naive - want)}，缝真的踩到了")


@test
def test_and_or_as_value_is_rejected():
    """`and` / `or` **当值用**必须报错 —— 照翻成 `&&` 两边都编得过，只有数不一样。

    Python 的 `1 and 2` 是 `2`；Loment 的 `&&` 只能给 `bool`。所以在**条件位置**两者
    一致（只问真假，可以翻），在**值位置**不一致（Python 可能给出任何 int，必须拒）。
    """
    src = (EX / "unsupported.py").read_text(encoding="utf-8")
    doc, _rep = potato_from.from_python(src, "unsupported.py", "strict")
    assert potato.validate(doc) == [], potato.validate(doc)[:3]
    try:
        lomt_from.emit_lomt(doc, impl=True)
    except lomt_from.NotRepresentable as e:
        msg = str(e)
        assert "条件位置" in msg, f"报的话要说清是「只能用在条件位置」，实得: {msg}"
        assert "操作数" in msg, f"要说出理由（Python 的 `and` 返回操作数是**值**）: {msg}"
        print(f"      `and` 当值用报得出: {msg[-100:]}")
    else:
        raise AssertionError(
            "`a and b` 当值用却一个字都没报 —— 照翻成 `&&` 会得到 1 而 Python 给 2，"
            "两边都编得过。这正是要消灭的静默")

    # **条件位置要照收** —— 不然上面那条"拒"就成了"这一门根本不支持 and"
    ok = "def f(a: int, b: int) -> int:\n    if a and b:\n        return 1\n    return 0\n"
    d2, _r2 = potato_from.from_python(ok, "ok.py", "strict")
    text, _s2 = lomt_from.emit_lomt(d2, impl=True)
    assert "&&" in text, f"条件位置的 `and` 应当翻成 `&&`:\n{text}"
    print("      条件位置的 `and` 照常翻成 `&&`")


@test
def test_helpers_only_when_used():
    """`__py_mod` / `__py_floordiv` **用到了才发** —— 确定性，不留死代码。

    同时钉住"发出来的恒等式是对的"：把生成的那两个函数原样抽出来单独编，
    与 Python 的 `//` `%` 逐例比一遍（四组符号）。语料那四组之外再补上边界。
    """
    used = pytrans.translate((EX / "floordiv.py").read_text(encoding="utf-8"))
    assert "__py_mod" in used and "__py_floordiv" in used, used[:300]
    unused = pytrans.translate((EX / "scope.py").read_text(encoding="utf-8"))
    assert "__py_mod" not in unused, f"没用到 `%` 却发了辅助函数:\n{unused[:300]}"
    assert "__py_floordiv" not in unused, f"没用到 `//` 却发了辅助函数:\n{unused[:300]}"
    print("      辅助函数按需发：用到 `//` `%` 的才有，没用的不发")

    if not _wsl():
        print("      SKIP: 恒等式逐例比对需要 WSL")
        return
    # 把两个辅助函数原样编成一个小单元（**用生成器里那份源**，不是另抄一遍 ——
    # 另抄一遍的话测的是抄件，而生成的可能是另一份），返回值编码成 `(商, 余)` 两个数
    prog = ("module h\n\n" + pytrans._HELPERS[pytrans._PY_MOD]
            + pytrans._HELPERS[pytrans._PY_FLOORDIV] + """
fn pack(a: i64, b: i64) -> i64 {
    return (__py_floordiv(a, b) + 50) * 100 + (__py_mod(a, b) + 50);
}

fn _start() {
    syscall4(60, (pack(P_A, P_B) % 250) as u64, 0, 0);
}
""")
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for a, b in ((-7, 2), (7, -2), (-7, -2), (7, 2), (-8, 3), (8, -3)):
            p = td / "h.lomt"
            p.write_text(prog.replace("P_A", str(a)).replace("P_B", str(b)),
                         encoding="utf-8", newline="\n")
            mod = lomentc.load(p)
            deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
            assert not lomentc.check(mod, deps=deps), (a, b, lomentc.check(mod, deps=deps)[:3])
            blob, _i = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
            exe = td / "h.elf"
            exe.write_bytes(blob)
            got = _run(exe)
            q, m = a // b, a % b
            want = ((q + 50) * 100 + (m + 50)) % 250
            assert got == want, (
                f"({a}, {b}): Loment 的辅助函数给 {got}，Python 是 {want}"
                f"（商 {q}、余 {m}）")
    print("      恒等式逐例对过：6 组符号组合与 Python 全等")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_pytrans_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
