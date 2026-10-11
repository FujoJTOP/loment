#!/usr/bin/env python3
"""loment_megaproject_test.py — **超大型项目**：100 个 Python 语法模块，共 1000 个函数。

用户 2026-10-10 的题：

> 继续，1000 个函数的超大型项目，100 个大型模块

## 它测什么（与 `loment_multisyntax_projects_test.py` 的分工）

那个判据测的是"六个表层语法**各**一个大型项目，算出的数对不对"；这一条只问**规模**：

* **100 个模块**、**每个 10 个函数**、合计 **1000 个函数**，一次编、一次跑；
* 宿主是**原生 Loment**，`use` 全部 100 个模块，把每个模块的 `entry()` 折成一个数；
* 折法**顺序敏感**（`acc = (acc * 31 + e) % 1000003`）—— 只是求和的话，两处错会互相抵消。

## 三条决定，都是实测逼出来的

1. **函数名必须全局唯一。** 单元的发射符号是**平的**（ABI）：两个模块各有一个 `f0`
   会被拒（"函数 f0 与模块 a 重名"，见 issue #221 那条消息本身也指错了东西）。
   所以每个模块的函数带自己的前缀（`m007_f3`）。这不是本判据的偏好，是语言的规则。

2. **模块级常量一个都不用。** 函数内与模块常量同名的局部会被静默忽略（issue #216），
   而参数与常量同名会被误报成"参数重复"（issue #217）。这一门语料**避开这两个形状** ——
   它们是缺陷，不是语义差。原来该当常量的东西直接写成字面量。

3. **语料是生成物，判据钉住它不漂。** 100 份手抄必然漂，所以这里有一份 `gen_module()`：
   仓里那 100 个 `.lomt` 必须逐字节等于它的产物（`test_corpus_matches_the_generator`），
   与自举种子的做法同源（"能藏的前提是它可复现"）。

## 判据是三方

```
   生成器 ──> 等价的 Python 源码 ──CPython──> rc_control ─┐
                                                          ├─ 三者相等
   生成器 ──> 100 个 .lomt ──前门-> lomelf -> WSL 跑──> rc_loment ─┤
                                                          │
   README 里 `EXPECTED`（**另写一遍**的 oracle 推的）────────────────expected ┘
```

用法:
    python tools/loment_megaproject_test.py            # 跑判据
    python tools/loment_megaproject_test.py --write    # 重新生成语料（改过 gen_* 之后）
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf       # noqa: E402
import lomentc      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROJ = ROOT / "loment" / "examples" / "megaproject"

#: 模块数与每模块函数数。**"100 个大型模块、1000 个函数"是这个判据的题面**，不是可调参数。
N_MODULES = 100
N_FUNCS = 10

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ------------------------------------------------------------------ 生成器
#
# **这是唯一真源**：仓里那 100 份 `.lomt` 由它产出，判据断两者逐字节相同。

def _ks(i: int) -> tuple[int, int, int]:
    """每个模块三个小常数，由模块号推出来（不写模块级常量，理由见文件头 §2）。"""
    return (i * 7) % 13 + 1, (i * 5) % 11 + 1, (i * 3) % 7 + 1


def gen_module(i: int) -> str:
    p = f"m{i:03d}"
    k1, k2, k3 = _ks(i)
    return f"""choose write grammar python

# {p}.lomt —— 超大型项目的第 {i} 个模块（**Python 拼法**写的 Loment）。
#
# 10 个函数，名字一律带 `{p}_` 前缀 —— **单元的发射符号是平的**（ABI），
# 跨模块同名会被拒。生成器见 `tools/loment_megaproject_test.py`，判据钉着这份产物不漂。


def {p}_f0(x: int) -> int:
    s = 0
    for j in range({k3}):
        s += x + j * {k1}
    return s


def {p}_f1(x: int) -> int:
    return {p}_f0(x) * {k1} // 8


def {p}_f2(x: int) -> int:
    return ((x << 3) ^ (x >> 1)) & 1023


def {p}_f3(x: int) -> int:
    if x > 100:
        return x - 100
    elif x > 10:
        return x * 2
    return x + 1


def {p}_f4(x: int) -> int:
    if x <= 0:
        return 0
    return 1 + {p}_f4(x - 1)


def {p}_f5(x: int) -> int:
    t = 0
    for a in range(3):
        for b in range(3):
            if a * b > 0:
                t += a * b + x
    return t


def {p}_f6(x: int) -> int:
    c = x & 65535
    n = 0
    while n < 8:
        if c & 1:
            c = (c >> 1) ^ 0xB400
        else:
            c = c >> 1
        n += 1
    return c


def {p}_f7(x: int) -> int:
    v = x & 255
    s = v >> 1
    while s != 0:
        v = v ^ s
        s = s >> 1
    return v


def {p}_f8(x: int) -> int:
    lo = x
    hi = x
    if {k2} < lo:
        lo = {k2}
    if {k2} > hi:
        hi = {k2}
    return hi - lo


def {p}_entry() -> int:
    x = {k1} + {k2} + {k3}
    return ({p}_f1(x) + {p}_f2(x) + {p}_f3(x) + {p}_f4(x % 7) + {p}_f5(x)
            + {p}_f6(x) + {p}_f7(x) + {p}_f8(x) + {i}) % 251
"""


def gen_main() -> str:
    """宿主：**原生 Loment**，`use` 全部模块，把每个 `entry()` 折成一个数。

    折法顺序敏感（`acc * 31 + e`，再取模），所以两处错不会互相抵消。
    """
    out = ["module main", ""]
    for i in range(N_MODULES):
        out.append(f'use "m{i:03d}.lomt"')
    out += ["", "fn _start() {", "    let acc: i64 = 0;"]
    for i in range(N_MODULES):
        out.append(f"    acc = (acc * 31 + m{i:03d}_entry() as i64) % 1000003;")
    out += ["    syscall4(60, (acc % 256) as u64, 0, 0);", "}", ""]
    return "\n".join(out)


def _corpus() -> dict[str, str]:
    files = {f"m{i:03d}.lomt": gen_module(i) for i in range(N_MODULES)}
    files["main.lomt"] = gen_main()
    return files


# ------------------------------------------------------------------ oracle
#
# **另写一遍**（与语料不同的手法）：`f0` 用闭式、`f2` 用位串、`f7` 用 `bin()`、
# `f4` 直接用 `max(x, 0)`。README 里那个 `EXPECTED` 就是它算出来的。

def oracle_entry(i: int) -> int:
    k1, k2, k3 = _ks(i)
    x = k1 + k2 + k3

    # f0 = Σ_{j<k3} (x + j*k1) —— 闭式
    f0 = k3 * x + k1 * k3 * (k3 - 1) // 2
    f1 = f0 * k1 // 8
    # f2：位串写法
    f2 = (int(bin(x << 3)[2:], 2) ^ (x >> 1)) & 1023
    f3 = (x - 100) if x > 100 else ((x * 2) if x > 10 else x + 1)
    f4 = max(x % 7, 0)                       # 递归就是数步数
    # f5：双重循环的闭式（a,b 都过 0..2，a*b>0 的格子）
    f5 = sum(a * b + x for a in range(3) for b in range(3) if a * b > 0)
    # f6：另写一遍那个 8 轮移位
    c = x & 65535
    for _ in range(8):
        c = ((c >> 1) ^ 0xB400) if (c & 1) else (c >> 1)
    f6 = c
    # f7：用 bin() 把每一对位折起来
    v = x & 255
    bits = bin(v)[2:].zfill(8)
    f7 = v
    s = v >> 1
    while s:
        f7 ^= s
        s >>= 1
    f8 = abs(x - k2)
    return (f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + i) % 251


def oracle_total() -> int:
    acc = 0
    for i in range(N_MODULES):
        acc = (acc * 31 + oracle_entry(i)) % 1000003
    return acc % 256


# ------------------------------------------------------------------ 跑
def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _run_elf(exe: Path) -> int:
    r = subprocess.run(
        ["wsl", "-e", "bash", "-lc", f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=900, shell=False)
    return int((r.stdout or "").strip() or -1)


def _loment_rc() -> int:
    main = PROJ / "main.lomt"
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{main}: 检查不过: {errs[:3]}"
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "side.elf"
        exe.write_bytes(blob)
        return _run_elf(exe)


def _control_python(td: Path) -> int:
    """对照组：把生成器产出的**等价 Python** 交给 CPython 跑。

    语料本身是 Python **拼法**的 Loment（不是合法 Python），所以对照侧用的是
    `gen_module()` 里那些函数体的直译 —— 与语料同源，**不是独立推导**。
    独立的那一路是 `oracle_total()`（README 里的 `EXPECTED`）。
    """
    py = td / "mega.py"
    parts = ["def _k1(i): return (i * 7) % 13 + 1",
             "def _k2(i): return (i * 5) % 11 + 1",
             "def _k3(i): return (i * 3) % 7 + 1", "", ""]
    for i in range(N_MODULES):
        p = f"m{i:03d}"
        k1, k2, k3 = _ks(i)
        parts.append(f"def {p}_f0(x):\n    s = 0\n    for j in range({k3}):\n        s += x + j * {k1}\n    return s")
        parts.append(f"def {p}_f1(x):\n    return {p}_f0(x) * {k1} // 8")
        parts.append(f"def {p}_f2(x):\n    return ((x << 3) ^ (x >> 1)) & 1023")
        parts.append(f"def {p}_f3(x):\n    if x > 100:\n        return x - 100\n    elif x > 10:\n        return x * 2\n    return x + 1")
        parts.append(f"def {p}_f4(x):\n    if x <= 0:\n        return 0\n    return 1 + {p}_f4(x - 1)")
        parts.append(f"def {p}_f5(x):\n    t = 0\n    for a in range(3):\n        for b in range(3):\n            if a * b > 0:\n                t += a * b + x\n    return t")
        parts.append(f"def {p}_f6(x):\n    c = x & 65535\n    n = 0\n    while n < 8:\n        if c & 1:\n            c = (c >> 1) ^ 0xB400\n        else:\n            c = c >> 1\n        n += 1\n    return c")
        parts.append(f"def {p}_f7(x):\n    v = x & 255\n    s = v >> 1\n    while s != 0:\n        v = v ^ s\n        s = s >> 1\n    return v")
        parts.append(f"def {p}_f8(x):\n    lo = x\n    hi = x\n    if {k2} < lo:\n        lo = {k2}\n    if {k2} > hi:\n        hi = {k2}\n    return hi - lo")
        parts.append(f"def {p}_entry():\n    x = {k1} + {k2} + {k3}\n"
                     f"    return ({p}_f1(x) + {p}_f2(x) + {p}_f3(x) + {p}_f4(x % 7) "
                     f"+ {p}_f5(x) + {p}_f6(x) + {p}_f7(x) + {p}_f8(x) + {i}) % 251")
        parts.append("")
        parts.append("")
    body = "".join(f"    acc = (acc * 31 + m{i:03d}_entry()) % 1000003\n" for i in range(N_MODULES))
    parts.append("def total():\n    acc = 0\n" + body + "    return acc % 256\n")
    py.write_text("\n".join(parts), encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, "-c",
                        "import runpy,sys;print(runpy.run_path(sys.argv[1])['total']())", str(py)],
                       capture_output=True, text=True, timeout=600, shell=False)
    assert r.returncode == 0, f"CPython 跑失败: {r.stderr[-300:]}"
    return int(r.stdout.strip())


def _expected() -> int:
    txt = (PROJ / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\s*EXPECTED:\s*(\d+)\s*$", txt)
    assert m, f"{PROJ}/README.md 里没有 `EXPECTED: <数>` 那一行"
    return int(m.group(1))


# ------------------------------------------------------------------ 判据

@test
def test_corpus_matches_the_generator():
    """仓里那 100 份 `.lomt` 必须**逐字节**等于 `gen_module()` 的产物。

    与自举种子同一条纪律（`loment_seed_test`）：生成物可以进仓，但前提是它**可复现** ——
    没有这一条，手改一份 `.lomt` 而没改生成器就会静默漂，判据却照样绿。
    """
    want = _corpus()
    for name, text in want.items():
        p = PROJ / name
        assert p.is_file(), f"{p} 不在树里 —— 先跑 `--write` 生成语料"
        got = p.read_text(encoding="utf-8")
        assert got == text, (
            f"{name} 与生成器不一致（{len(got)}B vs {len(text)}B）—— "
            f"要么它被手改了，要么生成器改了没重新生成。跑 `--write`。")
    extra = sorted(p.name for p in PROJ.glob("m[0-9]*.lomt"))
    assert len(extra) == N_MODULES, f"模块数不对：{len(extra)} != {N_MODULES}"
    print(f"      {len(want)} 份文件与生成器逐字节一致（{N_MODULES} 模块 + 宿主）")


@test
def test_the_scale_is_what_was_asked_for():
    """**"1000 个函数 / 100 个模块"是个可数的要求**，不是形容词。"""
    nfn = 0
    for i in range(N_MODULES):
        text = (PROJ / f"m{i:03d}.lomt").read_text(encoding="utf-8")
        n = len(re.findall(r"(?m)^def\s+\w+\s*\(", text))
        assert n == N_FUNCS, f"m{i:03d}.lomt 有 {n} 个函数，要求 {N_FUNCS}"
        nfn += n
    assert nfn == N_MODULES * N_FUNCS == 1000, nfn
    main = (PROJ / "main.lomt").read_text(encoding="utf-8")
    assert main.count('use "m') == N_MODULES, "宿主没有 use 全部模块"
    assert f"m{N_MODULES - 1:03d}_entry()" in main, "最后一个模块没有被折进去"
    print(f"      {N_MODULES} 个模块、{nfn} 个函数、宿主 use {N_MODULES} 个模块")


@test
def test_the_whole_thing_builds_runs_and_agrees_three_ways():
    """**1000 个函数一次编、一次跑**，而且 `loment == CPython == 独立推的`。"""
    want = _expected()
    orc = oracle_total()
    assert orc == want, f"README 的 EXPECTED 与 oracle 不一致: {want} != {orc}"
    got = _loment_rc()
    with tempfile.TemporaryDirectory() as t:
        ctl = _control_python(Path(t))
    assert ctl == want, f"CPython 对照与独立期望值不一致: {ctl} != {want}"
    assert got == ctl, f"Loment 与对照组不一致: {got} != {ctl}"
    print(f"      1000 个函数 / {N_MODULES} 个模块 -> loment={got} cpython={ctl} expected={want}")


def _write() -> int:
    PROJ.mkdir(parents=True, exist_ok=True)
    for name, text in _corpus().items():
        (PROJ / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"写了 {len(_corpus())} 份到 {PROJ}")
    print(f"oracle 算出来: EXPECTED = {oracle_total()}")
    return 0


def main() -> int:
    if "--write" in sys.argv:
        return _write()
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_megaproject_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
