#!/usr/bin/env python3
"""loment_trans_test.py — **花括号族写法 -> Loment** 的 Loment 版（`docs/186` / `189`）。

`tools/trans_core.py` —— C / C++ / Java / C# 四门共用的那个翻译器：一份扫描器、
一个递归下降、一个发射器，四门的差异是**一张方言表加几个开关**。判据与第 16、17 格
**同一条**：同一份源喂两边，产出的 Loment 源码**逐字节相同**，退出码也相同。

    源  --trans_core.translate-->  Loment 源码
    源  --lomtrans.lomt-------->  Loment 源码     ← 逐字节比这一份

## 比什么

1. **stdout 逐字节相同**（翻译出来的 Loment 源码）；
2. **退出码相同**（0 = 翻出来了 / 1 = 子集外 / 2 = 解析不过）。

**不比起文案** —— Python 的报错消息里插了源文本与 `repr()`，Loment 侧复现不了。
与第 15 格 `lompotato`、第 17 格 `lomtfrom` 同一条纪律：**失败时比退出码**，
而 `trans_core` 那两类失败（`Unsupported` / `CError`）本来就是两种退出码，
所以这一条仍然分得出"没实现"和"你写错了"。

## 覆盖面

* 四门各自语料库里的"真程序"（`loment/{ctrans,cpptrans,jtrans,cstrans}/`）；
* 第 16 格那四张前端 `_BATTERY`（直接从 `loment_ctrans_test` 引 —— 同一批输入
  喂前端和喂翻译器，覆盖面对齐；两处各抄一份必然漂）；
* `_EXTRA` —— **翻译器独有**的那些路：两个方向的强转、`for` 改名外提、保留字后缀、
  `--keep` / `--extern` / `--const`、重名、常量名照抄。

## 这一格**不含**什么

`--tokens` / `--pre` / `--ast` 是孪生自己的**调试**开关（不是上游接口），判据不碰，
所以这里不设判据钉它们 —— 它们的作用是出错时把"读错了"和"发错了"分开。

用法: python tools/loment_trans_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cpptrans  # noqa: E402
import cstrans  # noqa: E402
import ctrans  # noqa: E402
import jtrans  # noqa: E402
import lomelf  # noqa: E402
import lomentc  # noqa: E402
import loment_ctrans_test as T  # noqa: E402  (第 16 格那四张前端电池)
import trans_core  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TWIN = ROOT / "loment" / "tools" / "lomtrans.lomt"

#: 一门一张前端电池（第 16 格那四张，直接从 `loment_ctrans_test` 引）——
#: 同一批输入喂前端和喂翻译器，覆盖面对齐; 两处各抄一份必然漂。
_BATTERIES = {
    "c": T._BATTERY, "cpp": T._CPP_BATTERY,
    "java": T._JAVA_BATTERY, "csharp": T._CS_BATTERY,
}

#: **翻译器独有**的那几条路。语料与前端电池覆盖的是"前端怎么读源"，
#: 这里覆盖"翻译器怎么决定" —— 后者才是这一格新引入的东西。
#: 每项 `(标签, 源, 方言, 后缀, 选项)`；选项里的 `keep` / `externs` / `consts`
#: 就是 `trans_core.translate` 的那三个关键字，命令行侧对应 `--keep` / `--extern` / `--const`。
_EXTRA: list[tuple[str, str, object, str, dict]] = [
    # ---- 两个方向的强转: **方言表说了算**（C/C++ 都补，Java/C# 都不补）
    ("c_int_to_bool", "int f(int x) { if (x) { return 1; } return 0; }\n",
     ctrans.C, ".c", {}),
    ("c_bool_to_int", "int g(int a, int b) { int x = (a < b); return x; }\n",
     ctrans.C, ".c", {}),
    ("c_bool_ret", "bool pos(int x) { return x > 0; }\n", ctrans.C, ".c", {}),
    ("c_not", "int f(int x) { return !x; }\n", ctrans.C, ".c", {}),
    ("c_andand", "int f(int a, int b) { return a && b; }\n", ctrans.C, ".c", {}),
    ("cpp_bool_ret", "bool pos(int x) { return x > 0; }\n", cpptrans.CPP, ".cpp", {}),
    ("cpp_bool_param", "int f(bool b, int x) { if (b) { return x; } return 0; }\n",
     cpptrans.CPP, ".cpp", {}),
    ("java_no_coerce", "int f(boolean b) { if (b) { return 1; } return 0; }\n",
     jtrans.JAVA, ".java", {}),
    # Java/C# 这两条是**子集外**（退出码 1）—— 判据比退出码，所以照样钉得住
    ("java_int_as_cond", "int f(int x) { if (x) { return 1; } return 0; }\n",
     jtrans.JAVA, ".java", {}),
    ("java_bool_as_int", "int f(boolean b) { int x = b; return x; }\n",
     jtrans.JAVA, ".java", {}),
    ("cs_bool_ret", "class K { static bool pos(int x) { return x > 0; } }\n",
     cstrans.CSHARP, ".cs", {}),

    # ---- `for`: 改名外提 / 遮蔽 / 空条件 / 嵌套
    ("for_rename",
     "int f(int n) { int s = 0; for (int i = 0; i < n; i = i + 1) { s = s + i; }"
     " return s; }\n", ctrans.C, ".c", {}),
    ("for_shadow",
     "int f(int i) { int s = 0; for (int i = 0; i < 3; i = i + 1) { s = s + i; }"
     " return s + i; }\n", ctrans.C, ".c", {}),
    ("for_assign_init",
     "int f(int n) { int i = 0; int s = 0; for (i = 0; i < n; i = i + 1) { s = s + 1; }"
     " return s; }\n", ctrans.C, ".c", {}),
    ("for_empty_cond",
     "int f() { int s = 0; for (;;) { s = s + 1; } return s; }\n", ctrans.C, ".c", {}),
    ("for_nested",
     "int f(int n) { int s = 0; for (int i = 0; i < n; i = i + 1) {"
     " for (int j = 0; j < i; j = j + 1) { s = s + 1; } } return s; }\n",
     ctrans.C, ".c", {}),

    # ---- 三条"不是翻译、是决定"
    ("uninit_zero", "int f() { int x; return x; }\n", ctrans.C, ".c", {}),
    ("void_var", "int f() { void x; return 0; }\n", ctrans.C, ".c", {}),

    # ---- 保留字后缀（每门不同: `_c` / `_cpp` / `_j` / `_cs`）
    ("kw_suffix_c", "int f(int match) { int module = match; return module; }\n",
     ctrans.C, ".c", {}),
    ("kw_suffix_java",
     "class K { static int f(int match) { int module = match; return module; } }\n",
     jtrans.JAVA, ".java", {}),

    # ---- `--keep` / `--extern` / `--const`
    ("keep_only_one",
     "int a(int x) { return x; }\nint b(int y) { return y + 1; }\n",
     ctrans.C, ".c", {"keep": {"b"}}),
    ("keep_all_by_default",
     "int a(int x) { return x; }\nint b(int y) { return y + 1; }\n",
     ctrans.C, ".c", {}),
    ("extern_call_types",
     "int f(int n) { int s = 0; s = g(n); s = h(s); return s; }\n",
     ctrans.C, ".c", {"externs": {"g": "i32", "h": "()"}}),
    ("extern_unknown_is_loud",
     "int f(int n) { return unknown_fn(n); }\n", ctrans.C, ".c", {}),
    ("extern_wins_over_local",
     "int g(int x) { return x; }\nint f(int n) { return g(n); }\n",
     ctrans.C, ".c", {"externs": {"g": "()"}}),
    ("const_name_verbatim",
     "class K { static final int K0 = 5;\n static int f(int x) { return x + K0; } }\n",
     jtrans.JAVA, ".java", {"consts": {"K0": "i32"}}),
    ("const_needs_table",
     "class K { static final int K0 = 5;\n static int f(int x) { return x + K0; } }\n",
     jtrans.JAVA, ".java", {}),

    # ---- 重名（Loment 没有重载）
    ("dup_fn", "int f(int x) { return x; }\nint f(int y) { return y; }\n",
     ctrans.C, ".c", {}),
]


def _twin_exe(td: Path) -> Path:
    """把 `lomtrans.lomt` 链成可执行文件（走仓库自己的原生后端，不经 clang）。

    它只吃源码、只用 open/read/write/brk/exit 五个系统调用，所以能在本机**原生**跑。
    """
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomtrans.lomt 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / ("lomtrans.exe" if os.name == "nt" else "lomtrans")
    exe.write_bytes((lomelf.compile_pe(ir) if os.name == "nt" else lomelf.compile_ll(ir))[0])
    exe.chmod(0o755)
    return exe


def _argv(exe: Path, lang: str, fp: Path, opts: dict) -> list[str]:
    a = [str(exe), "--lang", lang, str(fp)]
    if opts.get("keep"):
        a += ["--keep", ",".join(sorted(opts["keep"]))]
    if opts.get("externs"):
        a += ["--extern", ",".join(f"{k}:{v}" for k, v in sorted(opts["externs"].items()))]
    if opts.get("consts"):
        a += ["--const", ",".join(f"{k}:{v}" for k, v in sorted(opts["consts"].items()))]
    return a


def _diff(lang: str, d, ext: str, extra: list) -> None:
    """一门一条判据的本体: 语料 + 前端电池 + `_EXTRA`，全部逐字节比。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases: list[tuple[str, str, dict]] = []
        for f in sorted((ROOT / "loment" / lang).glob("*" + ext)):
            cases.append((f.name, f.read_text(encoding="utf-8"), {}))
        cases += [(k + ext, v, {}) for k, v in sorted(_BATTERIES[lang].items())]
        cases += [(lab + ext, src, opts) for (lab, src, _d, _e, opts) in extra]
        bad = []
        for name, src, opts in cases:
            fp = td / name
            fp.write_text(src, encoding="utf-8", newline="\n")
            try:
                want = trans_core.translate(src, d, keep=opts.get("keep"),
                                            externs=opts.get("externs"),
                                            consts=opts.get("consts"))
                wrc = 0
            except trans_core.Unsupported:
                want, wrc = "", 1
            except trans_core.CError:
                want, wrc = "", 2
            r = subprocess.run(_argv(exe, lang, fp, opts), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=False, timeout=120)
            if r.returncode != wrc or r.stdout != want:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, wrc, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80]))
        assert not bad, (
            f"{len(bad)}/{len(cases)} 份与 translate 不同（前 2）:\n"
            + "\n".join(f"  {n}: rc={rc}/{wrc}\n    py={w!r}\n    tw={g!r}"
                        for n, rc, wrc, w, g in bad[:2]))
        print(f"      {lang}: {len(cases)} 份 —— Loment 版翻译器与 "
              f"`trans_core.translate` 产出的源码逐字节相同，退出码也一致")


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_lomtrans_twin_matches_translate_c():
    """**C 那一门的翻译器有了 Loment 版**（S1 第十八格，§4.1 的第二根轴）。

    第十七格把 `potato_from` + `lomt_from` 两半都搬进了 Loment，但那两半之间那段
    ——**语法树 -> Loment 源码**—— 一直只有 Python 版。这一格补上它。

    覆盖面：`loment/ctrans/` 五份语料 + 第 16 格的 `_BATTERY` + `_EXTRA` 里 C 那几条。
    """
    _diff("c", ctrans.C, ".c", [x for x in _EXTRA if x[2] is ctrans.C])


@test
def test_lomtrans_twin_matches_translate_cpp():
    """C++ 那一门 —— **强转那一格的最尖处**：`bool` 是真类型，两个方向都补。

    C++ 的语料里有 `bool` 返回的函数，于是 `return x > 0;` 那一步必须按**声明的返回类型**
    决定场合（写死"整数"会发出 `(x > 0) as i32`，而函数声明的是 `-> bool` —— 本语言的
    类型错）。C 那一门没有 `bool`，所以这个分支只有 C++ 逼得出来。
    """
    _diff("cpp", cpptrans.CPP, ".cpp", [x for x in _EXTRA if x[2] is cpptrans.CPP])


@test
def test_lomtrans_twin_matches_translate_java():
    """Java 那一门 —— **两个方向一个都不补**，补不动的地方报**子集外**（退出码 1）。

    同一个 `if (x)`：C 那边补成 `if (x != 0)`，Java 这边是错 —— `&&` 出 boolean、
    条件只收 boolean，所以那里本来就该是个布尔表达式。两族结论相反，靠的是方言表
    那两个开关，不是代码里的分支。
    """
    _diff("java", jtrans.JAVA, ".java", [x for x in _EXTRA if x[2] is jtrans.JAVA])


@test
def test_lomtrans_twin_matches_translate_csharp():
    """C# 那一门 —— 与 Java 同一族（不补），但类型表与外壳那一层不一样。

    另外这一门与 Java 共享 `static` / `public` 那一套修饰词扫描，`const` 才是它的
    常量标志词（Java 是 `final`）—— 混了的话常量声明会被当成函数体里的一条语句。
    """
    _diff("csharp", cstrans.CSHARP, ".cs", [x for x in _EXTRA if x[2] is cstrans.CSHARP])


@test
def test_lomtrans_twin_selfhost_compiles():
    """`lomtrans.lomt` 必须能走**种子自举链**编译，且产出的 IR 与参考实现**逐字节相同**。

    与第 16、17 格同一条：孪生不能只在自己这一套后端下面成立 —— 它得能被**已经
    自举过的那个编译器**吃下去，否则 S2 拆参考实现时这一步就断了。
    """
    import loment_dist  # noqa: E402

    stage1 = loment_dist.build_stage1()
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = subprocess.run([str(stage1), TWIN.relative_to(ROOT).as_posix()], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译 lomtrans.lomt 失败: {r.stderr[-400:]}"
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)"
    print(f"      种子自举链编译 lomtrans.lomt 成功，且 IR 与参考逐字节相同 ({len(want)}B)")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print()
    if failed:
        print(f"loment_trans_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
        return 1
    print(f"loment_trans_test: {len(TESTS)}/{len(TESTS)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
