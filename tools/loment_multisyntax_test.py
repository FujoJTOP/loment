#!/usr/bin/env python3
# loment_multisyntax_test.py — 多语法前端判据 (docs/179, docs/175 §5/§6 第 4 条)
#
# **五种源语法**: C / Rust / Go / Java / Python。每种**十条**判据, 表驱动 ——
# 加一个语言只加一行数据, 而不是再抄十段测试。
#
# 十种判据 (每种语法各一份):
#   1 类型映射      标量/指针/字符串 -> 我们声明的 Potato 类型
#   2 结构体/类     聚合声明 -> `types` (字段逐个对)
#   3 函数签名      形参名与类型、返回值
#   4 ABI 归类      哪些是 `c`(能发 extern fn), 哪些是那一族自己的约定
#   5 发不出来的**被报出来**   名字 + 原因 (不是静默丢)
#   6 顺序保留      声明的先后不被重排
#   7 产物可检查    发出的 L1 过 `lomentc.check`
#   8 按内容识别    `.lomt` 装着这门语法 -> 认出来
#   9 确定性        同一份输入 -> 同一串字节
#  10 那条"腿"      见 §腿
#
# ## "腿": 链接型 vs 运行型 —— 两组语言真正分岔的地方
#
# `docs/173` 把"支持一个语言"分成两条腿, 而这一次动手让它在**前端**这一侧也显出来:
#
#   * **链接型 (C / Rust / Go)**: 函数的调用约定是平台 C ABI, 于是能发 `extern fn`,
#     由 `lomelf` 把外面编好的目标文件链进来 —— 第 10 条是**真跑**:
#     编目标文件 -> 生成接口单元 -> L1 调用 -> 链 -> 跑出预期退出码。
#   * **运行型 (Java / Python)**: 方法是 JVM/解释器的调用约定, **不是 C ABI**
#     (Java 的 `native` 走 JNI, 符号名前两个参数还是 `JNIEnv*`/`jobject`)。第 10 条
#     因此不是"链起来跑", 而是**钉住它们一条都不会被发成 `extern fn`** ——
#     那正是"要调它们得走进程桥"这条结论的机器可验形式。
#     它们**跑得通**, 判据在 `loment_ffi_test` 的进程桥那几条 (Go/Python/Java/JS/Perl/Lua)。
#
# 所以第 10 条在两组里形状不同, **不是偷懒, 是那两组本来就不一样**。
#
# 用法: python tools/loment_multisyntax_test.py   (无 clang/WSL 时相关条目 SKIP)

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf                                                        # noqa: E402
import lomentc                                                       # noqa: E402
import lomt_from                                                     # noqa: E402
import potato                                                        # noqa: E402
import potato_from                                                   # noqa: E402
import loment_diag                                                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

# ---------------------------------------------------------------- 夹具 (每种语法一份)

C_SRC = """\
struct Pair { int a; int b; };

enum Mode { IDLE, RUN, DONE };
enum Err { EOK = 0, EBAD = -1, EFULL = 5 };

int c_add(int a, int b) { return a + b; }
int c_sum(const unsigned char *p, unsigned n) { return 0; }
int c_utoa(unsigned value, char *buf) { return 0; }
double c_avg(double x, double y) { return x; }
"""
RUST_SRC = """\
#[no_mangle]
pub extern "C" fn r_add(a: i32, b: i32) -> i32 { a + b }

pub struct Pt { pub x: i32, pub y: i32 }

pub enum Mode { Idle, Run }

pub fn r_plain(x: i32) -> i32 { x * 2 }

pub fn r_big(x: i64) -> usize { 0 }
"""
GO_SRC = """\
package main

type Pt struct {
    X int
    Y int
}

//export g_add
func g_add(a int, b int) int { return a + b }

func g_plain(x int) int { return x * 2 }

func g_many(a, b int) int { return a }

func g_slice(n int) []int { return nil }
"""
JAVA_SRC = """\
package demo;

enum Level { LOW, MID, HIGH }

public class Shape {
    public static final int SIDES = 4;
    private int w;
    private int h;
    private double area;

    public Shape(int a, int b) { this.w = a; this.h = b; }

    public static int j_add(int a, int b) { return a + b; }

    public native int j_native(int a);
}
"""
PY_SRC = """\
def py_add(a: int, b: int) -> int:
    return a + b

def py_plain(x: int) -> int:
    return x * 2

def py_big(x: bytes) -> int:
    return 0
"""

LINK_C = {"compiler": "clang", "ext": ".c", "name": "e2e_add",
          "src": "int e2e_add(int a, int b) { return a + b; }\n"}
# 用 clang 编 Rust 源不成立 (clang 不认 `pub extern "C" fn`), 所以那条腿由 rustc 走
LINK_RUST = {
    "compiler": "rustc", "ext": ".rs",
    "src": ('#![no_std]\n#[no_mangle]\npub extern "C" fn e2e_add(a: i32, b: i32) -> i32 '
            '{ a + b }\n#[panic_handler]\nfn ph(_: &core::panic::PanicInfo) -> ! { loop {} }\n'),
    "name": "e2e_add",
}

#: 每种语法的全部期望值。**加一个语言 = 加一项, 不改测试代码。**
SYNTAXES = [
    {
        "lang": "c", "ext": ".c", "src": C_SRC, "leg": "link",
        "types": [("Pair", [("a", "i32"), ("b", "i32")])],
        # 不带值的枚举走 `enums`; **带值的走 `consts`** (值进不了 enums 的 schema,
        # 而它常常是协议常量 —— 丢值比丢名严重)。整个枚举要么进一边、要么进另一边,
        # **不拆开** (拆开会造出一个看着少了一个变体的枚举)。
        "enums": [("Mode", ["IDLE", "RUN", "DONE"])],
        "consts": [("EOK", "i32", 0), ("EBAD", "i32", -1), ("EFULL", "i32", 5)],
        "fns": [("c_add", [("a", "i32"), ("b", "i32")], "i32", "c"),
                ("c_sum", [("p", "ptr"), ("n", "u32")], "i32", "c"),
                # `char *buf` 映 `str`(不是 C 字符串) -> 转写得出来, 但过不了
                # FFI 第 1 阶段那道闸门。所以它在 fns 里, 也在 skips 里。
                ("c_utoa", [("value", "u32"), ("buf", "str")], "i32", "c")],
        "emitted": ["c_add", "c_sum"],
        "skips": [("c_utoa", "签名超出"), ("c_avg", "无映射")],
        "link": LINK_C,
    },
    {
        "lang": "rust", "ext": ".rs", "src": RUST_SRC, "leg": "link",
        "types": [("Pt", [("x", "i32"), ("y", "i32")])],
        "enums": [("Mode", ["Idle", "Run"])],
        "fns": [("r_add", [("a", "i32"), ("b", "i32")], "i32", "c"),
                ("r_plain", [("x", "i32")], "i32", "rust"),
                ("r_big", [("x", "i64")], "u64", "rust")],
        "skips": [],
        "emitted": ["r_add"],
        "link": LINK_RUST,
    },
    {
        "lang": "go", "ext": ".go", "src": GO_SRC, "leg": "c-abi-subset",
        "types": [("Pt", [("X", "i64"), ("Y", "i64")])],
        "fns": [("g_add", [("a", "i64"), ("b", "i64")], "i64", "c"),
                ("g_plain", [("x", "i64")], "i64", "go")],
        "skips": [("g_many", "两段"), ("g_slice", "无映射")],
        "emitted": ["g_add"],
        "link": None,
    },
    {
        "lang": "java", "ext": ".java", "src": JAVA_SRC, "leg": "runtime",
        "types": [("Shape", [("w", "i32"), ("h", "i32")])],
        "consts": [("SIDES", "i32", 4)],
        "enums": [("Level", ["LOW", "MID", "HIGH"])],
        "fns": [("j_add", [("a", "i32"), ("b", "i32")], "i32", "java"),
                ("j_native", [("a", "i32")], "i32", "java")],
        "skips": [("Shape", "构造器")],
        "emitted": [],
        "link": None,
    },
    {
        "lang": "python", "ext": ".py", "src": PY_SRC, "leg": "runtime",
        "types": [],
        "fns": [("py_add", [("a", "i64"), ("b", "i64")], "i64", "python"),
                ("py_plain", [("x", "i64")], "i64", "python"),
                ("py_big", [("x", "ptr")], "i64", "python")],
        "skips": [],
        "emitted": [],
        "link": None,
    },
]

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _rustc() -> str | None:
    return shutil.which("rustc")


def _rust_has_target() -> bool:
    r = subprocess.run(["rustc", "--print", "target-list"], capture_output=True,
                       text=True, shell=False)
    return "x86_64-unknown-none" in r.stdout


def _wsl() -> bool:
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                               # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _transcribe(spec: dict) -> tuple[dict, object]:
    doc, rep = potato_from.LANGS[spec["lang"]](spec["src"], f"m{spec['ext']}", "strict")
    assert not rep.validate_errors, f"{spec['lang']}: {rep.validate_errors[:2]}"
    assert potato.validate(doc) == [], f"{spec['lang']}: {potato.validate(doc)[:2]}"
    return doc, rep


def _emit(spec: dict) -> tuple[str, list]:
    doc, _rep = _transcribe(spec)
    return lomt_from.emit_lomt(doc)


# ---------------------------------------------------------------- 十种判据, 每种语法一份

def _mk(spec: dict):
    lang = spec["lang"]

    def t_types_map():
        """1 类型映射: 这门语言的标量/指针/字符串映到我们声明的 Potato 类型。"""
        doc, _ = _transcribe(spec)
        got = {f["name"]: [p["type"] for p in f["params"]] for f in doc["functions"]}
        for name, params, _ret, _abi in spec["fns"]:
            assert name in got, f"{lang}: 没抽出函数 {name}（抽到 {sorted(got)}）"
            assert len(got[name]) == len(params), f"{lang}/{name}: 形参数 {got[name]}"

    def t_aggregate_becomes_type():
        """2 聚合与常量：结构体/类 -> `types`，枚举 -> `enums` 或 `consts`，常量 -> `consts`。

        **带值的枚举走 `consts`**（值进不了 `enums` 的 schema，而它常常是协议常量 ——
        丢值比丢名严重）；不带值的走 `enums`。整个枚举要么进一边、要么进另一边，
        **不拆开** —— 拆开会造出一个"看着少了一个变体"的枚举。
        """
        doc, _ = _transcribe(spec)
        got_t = {t["name"]: [(f["name"], f["type"]) for f in t["fields"]]
                 for t in doc["types"]}
        for name, fields in spec.get("types", []):
            assert name in got_t, f"{lang}: 没有类型 {name}（有 {sorted(got_t)}）"
            assert got_t[name] == fields, f"{lang}/{name}: {got_t[name]} != {fields}"
        got_e = {e["name"]: list(e["variants"]) for e in doc["enums"]}
        for name, vs in spec.get("enums", []):
            assert name in got_e, f"{lang}: 没有枚举 {name}（有 {sorted(got_e)}）"
            assert got_e[name] == vs, f"{lang}/{name}: {got_e[name]} != {vs}"
        got_c = {c["name"]: (c["type"], c["value"]) for c in doc["consts"]}
        for name, ty, val in spec.get("consts", []):
            assert got_c.get(name) == (ty, val), \
                f"{lang}: 常量 {name} = {got_c.get(name)} 期望 {(ty, val)}"

    def t_signatures():
        """3 函数签名: 形参名、形参类型、返回类型都对得上。"""
        doc, _ = _transcribe(spec)
        got = {f["name"]: ([(p["name"], p["type"]) for p in f["params"]], f["ret"])
               for f in doc["functions"]}
        for name, params, ret, _abi in spec["fns"]:
            assert got.get(name) == (params, ret), \
                f"{lang}/{name}: {got.get(name)} != {(params, ret)}"

    def t_abi_classification():
        """4 ABI 归类: 只有平台 C ABI 的那个能发 `extern fn`。

        这一条是**这整套东西的安全阀**: 把普通 Rust `pub fn` 当 C ABI 调、
        把 Go 的 `//export` 与普通 `func` 当成一回事 —— 都是**错编**。
        """
        doc, _ = _transcribe(spec)
        got = {f["name"]: f.get("abi") for f in doc["functions"]}
        for name, _params, _ret, abi in spec["fns"]:
            assert got.get(name) == abi, f"{lang}/{name}: abi={got.get(name)!r} 期望 {abi!r}"
            assert abi in potato.ABIS, f"{lang}: abi {abi!r} 不在 {potato.ABIS}"

    def t_unsupported_are_reported():
        """5 发不出来的**必须被报出来** (名字 + 原因) —— 静默丢比拒绝坏。"""
        _doc, rep = _transcribe(spec)
        # **整条流水线**的跳过项: 转写那一步 + 发射那一步。只看后者会让"前一步丢的"
        # 看起来像没丢 (2026-09-17 实测撞过一次, docs/179 §6.3)。
        _text, emit_skips = _emit(spec)
        reasons = {s["name"]: s["why"] for s in rep.skipped}
        for n, why in emit_skips:
            reasons.setdefault(n, why)
        for name, sub in spec["skips"]:
            assert name in reasons, f"{lang}: {name} 没被报为跳过（报了 {sorted(reasons)}）"
            assert sub in reasons[name], f"{lang}/{name}: 原因 {reasons[name]!r} 不含 {sub!r}"

    def t_order_preserved():
        """6 顺序保留: 抽出来的函数顺序 = 源里的顺序。"""
        doc, _ = _transcribe(spec)
        want = [n for n, _p, _r, _a in spec["fns"]]
        assert [f["name"] for f in doc["functions"]] == want, \
            f"{lang}: {[f['name'] for f in doc['functions']]} != {want}"

    def t_unit_is_checkable():
        """7 产物可检查: 发出的 L1 单元过 `lomentc.check` (它要进冻结核心)。"""
        text, _sk = _emit(spec)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / f"m{spec['ext']}.lomt"
            p.write_text(text, encoding="utf-8", newline="\n")
            mod = lomentc.load(p)
            errs = lomentc.check(mod, deps=lomentc.resolve_deps(mod, ROOT, p.parent, entry=p))
            assert not errs, f"{lang}: 发出的 L1 过不了检查: {errs[:2]}\n{text[:400]}"

    def t_detected_from_content():
        """8 按内容识别: 一份 `.lomt` 装这门语法, `resolve_lang` 要认出来。"""
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "m.lomt"
            p.write_text(spec["src"], encoding="utf-8", newline="\n")
            got, why = potato_from.resolve_lang(p, "auto")
            assert got == lang, f"认成 {got!r}（{why}）, 期望 {lang!r}"

    def t_emission_deterministic():
        """9 确定性: 同一份输入 -> 同一串字节 (产物要能进判据)。"""
        a, _ = _emit(spec)
        b, _ = _emit(spec)
        assert a == b, f"{lang}: 两次发射不一致"

    def t_the_leg():
        """10 那条腿。三档, 见文件头 §腿 —— **分档本身就是这一条的结论**。

        * `link`           C / Rust: C ABI 是语言级默认 -> 真编目标文件、真链、真跑。
        * `c-abi-subset`   Go: **只有 `//export` 过的**是 C ABI。这一条钉住
                           "哪些发得出来、哪些发不出来" —— 它同时也是"不能整门语言
                           一概而论"的机器可验形式。
                           (不做真链: Go 的 C ABI 目标文件要 `-buildmode=c-archive`,
                           还得有目标平台的 C 交叉工具链; 它跑得通那条在
                           `loment_ffi_test` 的进程桥里。)
        * `runtime`        Java / Python: 一条都不是 C ABI -> 钉住"一条都不许发
                           `extern fn`"。要调它们走进程桥 (docs/173 §4)。
        """
        text, _sk = _emit(spec)
        decls = [ln.split("pub extern fn ", 1)[1].split("(", 1)[0]
                 for ln in text.splitlines() if ln.startswith("pub extern fn")]
        want = spec["emitted"]                # 应该出现在产物里的 extern fn
        if spec["leg"] == "runtime":
            assert not decls, f"{lang}: 运行型的语言不该有 extern fn 声明: {decls}"
            return
        if spec["leg"] == "c-abi-subset":
            assert decls == want, f"{lang}: 发出来的 {decls} != 该发的 {want}"
            return
        assert decls == want, f"{lang}: 发出来的 {decls} != 该发的 {want}"
        if not link_ready(spec):
            print(f"      SKIP {lang} 端到端: 缺工具链")
            return
        _link_roundtrip(spec)

    return [(f"{lang}_1_types", t_types_map),
            (f"{lang}_2_aggregate", t_aggregate_becomes_type),
            (f"{lang}_3_signatures", t_signatures),
            (f"{lang}_4_abi", t_abi_classification),
            (f"{lang}_5_skips_reported", t_unsupported_are_reported),
            (f"{lang}_6_order", t_order_preserved),
            (f"{lang}_7_checkable", t_unit_is_checkable),
            (f"{lang}_8_detected", t_detected_from_content),
            (f"{lang}_9_deterministic", t_emission_deterministic),
            (f"{lang}_10_leg", t_the_leg)]


def link_ready(spec: dict) -> bool:
    if spec["leg"] != "link":
        return False
    if not _wsl():
        return False
    return bool(_clang()) and (spec["link"]["compiler"] != "rustc"
                               or (_rustc() and _rust_has_target()))


def _link_roundtrip(spec: dict) -> None:
    """真跑: 编目标文件 -> 接口单元 -> L1 调用 -> 链 -> 跑出 42。"""
    lg = spec["link"]
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = td / f"{lg['name']}{lg['ext']}"
        src.write_text(lg["src"], encoding="utf-8", newline="\n")
        iface, skipped = _iface_from(td, lg["name"], lg["src"], spec["lang"])
        assert f"pub extern fn {lg['name']}" in iface.read_text(encoding="utf-8"), \
            (spec["lang"], skipped)
        obj = td / "a.o"
        if lg["compiler"] == "clang":
            cmd = [_clang(), "--target=x86_64-unknown-linux-gnu", "-x", "c", "-c", "-O1",
                   "-ffreestanding", "-fno-stack-protector", "-o", str(obj), str(src)]
        else:
            cmd = [_rustc(), "--target", "x86_64-unknown-none", "--crate-type",
                   "staticlib", "--emit=obj", "-O", "-o", str(obj), str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"{spec['lang']} 编目标文件失败: {r.stderr[-300:]}"
        # 产物非空 —— clang 把不认识的输入当 linker input 时会 **rc=0 且不出东西**
        assert obj.exists() and obj.stat().st_size > 0, "没产出目标文件 (漏了 -x c?)"
        _run_with(td, td / f"{lg['name']}_iface.lomt", [obj],
                  40 + 2, lg["name"])       # 40 + 2 = 42


def _iface_from(td: Path, name: str, src: str, lang: str) -> tuple[Path, list]:
    doc, rep = potato_from.LANGS[lang](src, f"{name}.x", "strict")
    text, skipped = lomt_from.emit_lomt(doc)
    out = td / f"{name}_iface.lomt"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out, [(s["name"], s["why"]) for s in rep.skipped] + skipped


def _run_with(td: Path, iface: Path, objs: list[Path], exit_want: int, fname: str) -> None:
    main = td / "main.lomt"
    main.write_text(f'module ms\n\nuse "{iface.as_posix()}"\n\n'
                    "fn _start() {\n"
                    f"    syscall4(60, {fname}(40 as i32, 2 as i32) as u64, 0, 0);\n"
                    "}\n", encoding="utf-8", newline="\n")
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, errs
    blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps),
                               [lomelf.load_foreign(p) for p in objs])
    exe = td / "ms.elf"
    exe.write_bytes(blob)
    r = subprocess.run(["wsl", "-e", "bash", "-lc",
                        f"cp {_wsl_path(exe)} /tmp/ms_{exe.stat().st_size}.elf && "
                        f"chmod +x /tmp/ms_{exe.stat().st_size}.elf && "
                        f"/tmp/ms_{exe.stat().st_size}.elf"],
                       capture_output=True, text=True, timeout=120, shell=False)
    assert r.returncode == exit_want, \
        f"退出码 {r.returncode} != {exit_want} (stderr {r.stderr[-200:]!r})"


# ---------------------------------------------------------------- 跨语法的判据

@test
def test_five_syntaxes_and_ten_tests_each():
    """这一条钉住**规模本身**: 五种语法, 每种十条 —— 少一条就红。

    它看着像废话判据, 但它防的是一件真事: 加语言/加判据时漏掉一条 (比如给新语言
    只写了 8 条), 而**没有任何东西会告诉你**。规模是有人明确要过的, 就该有判据钉着。
    """
    langs = [s["lang"] for s in SYNTAXES]
    assert len(set(langs)) >= 4, f"至少四种语法, 现在是 {langs}"
    for lang in langs:
        n = len([1 for name, _f in TESTS if name.startswith(f"{lang}_")])
        assert n >= 10, f"{lang} 只有 {n} 条判据 (至少 10)"
    per = {g["lang"]: len([1 for n, _f in TESTS if n.startswith(g["lang"] + "_")])
           for g in SYNTAXES}
    print(f"      {len(langs)} 种语法 × 每种至少 10 条 = "
          f"{sum(per.values())} 条: {per}")


@test
def test_sniffing_separates_languages_that_share_keywords():
    """`class` 在 Python/Java 里都有, `package` 在 Go/Java 里都有 —— 必须分得开。

    这两处是**实测撞出来的**: 加 Java 之后, 一份 Java 源码被判成 `go`(都有 `package`),
    而没切开的 `class` 会让 Java 被判成 `python`。判据钉住切开了。
    """
    cases = [
        ("python", "class A:\n    pass\n"),
        ("java", "class A {\n    int x;\n}\n"),
        ("java", "package com.example;\npublic class A { }\n"),
        ("go", "package main\n\nfunc f() { }\n"),
        ("go", "package main\n\ntype T struct {\n    X int\n}\n"),
        ("rust", "struct T {\n    x: i32,\n}\n"),
        ("c", "struct T { int x; };\n"),
        # **`enum X { … }` 在 C / Java / Rust 里长得一样** -> 它不是任何一门的判据,
        # 只能靠文件里**别的**特征定位。这一组钉住"带枚举的 C 不许被判成 java"。
        ("c", "enum Color { RED, GREEN };\n\nint f(int a) { return a; }\n"),
        ("java", "enum Color { RED, GREEN }\n\nclass T { int x; }\n"),
        # **`import` 在 Python 与 Java 里都有** -> 按**行尾分号**分开。一份带 import 的
        # 真 Java 文件原先被内容兜底判成 `python`, 然后 `ast.parse` 在 `package a.b;`
        # 上抛一坨 traceback (2026-09-17, docs/179 §8.1 第 6 条)。
        ("java", "package a.b;\nimport java.util.List;\npublic class T { int x; }\n"),
        ("python", "import os\n\nclass A:\n    x: int\n"),
    ]
    for want, src in cases:
        got, why = potato_from.detect_lang(src)
        assert got == want, f"认成 {got!r}（{why}）, 期望 {want!r}: {src!r}"
    # 反过来: Loment 不许被认成任何一种外源语法
    for snippet in ("module m\n\nfn f() -> u32 {\n    return 1;\n}\n",
                    "fn f() { return 1; }\n",
                    "struct S { a: u32 }\n"):
        got, why = potato_from.detect_lang(snippet)
        assert got != "c", f"Loment 被认成 C（{why}）: {snippet!r}"
    print("      共 7 组易混的形状都分得开 (class/package/struct 各自归属)")


@test
def test_abi_values_are_declared_in_the_schema():
    """每种语法的 `abi` 都必须在 `potato.ABIS` 里 —— 校验器与前端不许各说各话。"""
    for s in SYNTAXES:
        doc, _ = _transcribe(s)
        for f in doc["functions"]:
            assert f.get("abi") in potato.ABIS, (s["lang"], f["name"], f.get("abi"))
    for extra in ("go", "java"):
        assert extra in potato.ABIS, f"前端支持 {extra} 而 schema 的 ABIS 里没有"
    print(f"      ABIS = {potato.ABIS}；五种语法都在里面")


@test
def test_diag_does_not_tell_you_to_fix_valid_foreign_source():
    """诊断工具**不许**对一份语法正确的 C/Java 说"改那一行的写法"。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for s in SYNTAXES:
            p = td / f"m{s['ext']}.lomt"           # 后缀是 .lomt, 内容是外源语法
            p.write_text(s["src"], encoding="utf-8", newline="\n")
            note = loment_diag.foreign_note(p, ['63:25: 非法字符 "\'"'])
            assert note and "不是 Loment" in note, (s["lang"], note)
            assert s["lang"].upper() in note, (s["lang"], note)
        lp = td / "t.lomt"
        lp.write_text("module t\n\nfn f() -> u32 {\n    return 1\n}\n",
                      encoding="utf-8", newline="\n")
        assert loment_diag.foreign_note(lp, ["5:1: 期望 ;，得到 '}'"]) is None
        assert loment_diag.foreign_note(td / "m.c", []) is None
    print("      五种语法都给出对得上的提示; Loment 的 typo 不给")


#: 照 2026-09-17 一个子 agent **真写出来的那份 C** 蒸馏的 (无 `#include`、无 libc),
#: 刻意保留了三处当时把工具链绊倒的形状: Allman `{`、`unsigned` 单独写、
#: 单引号字符字面量 `'0'`。它由下面那条端到端每次真跑 —— 见 `docs/179` §6。
C_IN_LOMT = """\
// 一份 C 源码 后缀是 lomt

int c_fact(int n)
{
    int acc = 1;
    int i;
    if (n < 0) {
        return 0;
    }
    for (i = 2; i <= n; i = i + 1) {
        acc = acc * i;
    }
    return acc;
}

unsigned c_gcd(unsigned a, unsigned b)
{
    unsigned t;
    while (b != 0) {
        t = a % b;
        a = b;
        b = t;
    }
    return a;
}

int c_popcount(unsigned v)
{
    int n = 0;
    while (v != 0) {
        n = n + (int)(v & 1u);
        v = v >> 1;
    }
    return n;
}

// 把数字写进调用方缓冲 返回写了几位
int c_utoa(unsigned value, char *buf)
{
    unsigned scale = 1;
    unsigned v = value;
    int n = 0;
    while (v >= 10) {
        v = v / 10;
        scale = scale * 10;
    }
    while (scale > 0) {
        buf[n] = (char)('0' + (value / scale) % 10);
        n = n + 1;
        scale = scale / 10;
    }
    return n;
}
"""


@test
def test_c_source_named_lomt_end_to_end():
    """**一份 C 源码后缀写成 `.lomt`** —— `docs/175` §5 那条原话的完整形态。

    认出 C → 生成接口单元 → L1 调用 → 链上 clang 真编出来的目标文件 → 跑。
    期望的退出码由 Python **独立**算出来对照, 不是硬编码。

    **`-x c` 不能省**: clang 按**后缀**认语言, 一份 `.lomt` 会被当成 linker 输入
    (`'linker' input unused`) 然后 **rc=0 且不产出目标文件** —— 没有报错、没有非零
    退出码, 只有你去找产物时才发现是空的。判据因此在 clang 那一步**先查产物非空**。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = td / "mymod.lomt"                 # ← 后缀是 .lomt, 内容是 C
        src.write_text(C_IN_LOMT, encoding="utf-8", newline="\n")
        got_lang, why = potato_from.resolve_lang(src, "auto")
        assert got_lang == "c", f"认成 {got_lang!r}（{why}）"
        iface, skipped = _iface_from(td, "mymod", C_IN_LOMT, "c")
        assert "c_fact" in iface.read_text(encoding="utf-8"), skipped
        # 收 `char *` 的那个过不了 FFI 闸门 —— **必须被报出来**
        assert "c_utoa" in [n for n, _ in skipped], skipped
        obj = td / "mymod.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-x", "c", "-c",
                            "-O1", "-ffreestanding", "-fno-stack-protector",
                            "-o", str(obj), str(src)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        assert obj.exists() and obj.stat().st_size > 0, "没产出目标文件 (漏了 -x c?)"
        want = (720 + 21 + 13) % 256            # 6! + gcd(1071,462) + popcount(0xbeef)
        main = td / "main.lomt"
        main.write_text(f'module msc\n\nuse "{iface.as_posix()}"\n\n'
                        "fn _start() {\n"
                        "    let a: u32 = c_fact(6 as i32) as u32;\n"
                        "    let b: u32 = c_gcd(1071 as u32, 462 as u32);\n"
                        "    let c: u32 = c_popcount(48879 as u32) as u32;\n"
                        "    syscall4(60, ((a + b + c) % 256) as u64, 0, 0);\n"
                        "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(main)
        deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, errs
        blob, _ = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps),
                                    [lomelf.load_foreign(obj)])
        exe = td / "ms.elf"
        exe.write_bytes(blob)
        rr = subprocess.run(["wsl", "-e", "bash", "-lc",
                             f"cp {_wsl_path(exe)} /tmp/ms_cinlomt.elf && "
                             f"chmod +x /tmp/ms_cinlomt.elf && /tmp/ms_cinlomt.elf"],
                            capture_output=True, text=True, timeout=120, shell=False)
        assert rr.returncode == want, \
            f"退出码 {rr.returncode} != {want} (stderr {rr.stderr[-200:]!r})"
    print(f"      C 装 `.lomt`: 认出 -> 接口单元 -> 链真目标文件 -> 退出码 {want} (Python 独立算出)")


@test
def test_unrepresentable_object_is_rejected():
    """对象里有 L1 那侧接不上的东西 (traits / layouts / imports) -> **报错退出**。"""
    base = potato_from._blank("m", "c")
    for key, val in (("traits", [{"name": "T", "methods": ["m"]}]),
                     ("layouts", [{"name": "H", "size": 8, "endian": "little",
                                   "packed": True,
                                   "fields": [{"name": "a", "type": "u32", "offset": 0}]}]),
                     ("imports", ["other"])):
        doc = json.loads(json.dumps(base))
        doc[key] = val
        try:
            lomt_from.emit_lomt(doc)
        except lomt_from.NotRepresentable as e:
            assert key in str(e), (key, str(e))
            continue
        raise AssertionError(f"{key} 非空却没有报错")
    print("      traits / layouts / imports 非空 -> 一律报错退出")


@test
def test_field_level_drops_are_reported_too():
    """**字段级跳过也要出声** —— 语料库那条 (§8) 撞出来的第 1 个口子。

    五个前端都在调 `Report.skip_field`, 而 `lomt_from` 原先**只打印**实体级的
    `skipped` —— 一份 struct 少两个字段 (`scale: float` / `names: list`),
    `[OK]` 那行干干净净, 一个数都不变。字段不进"实体分母"是对的 (见 `skip_field`
    的注释), 但**不计数不等于不报告**。

    这条测的是**工具的输出**, 不是某份对象 —— 所以它必须走 `lomt_from.main`,
    而不是直接调 `transcribe`。
    """
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "cfg.py"
        src.write_text("class Cfg:\n    base: int\n    scale: float\n    names: list\n",
                       encoding="utf-8", newline="\n")
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = lomt_from.main([str(src), "--lang", "auto"])
        assert rc == 0, err.getvalue()
    text = err.getvalue()
    for f in ("Cfg.scale", "Cfg.names"):
        assert f"[skip] {f}:" in text, f"{f} 没出声:\n{text}"
    # 出声之后**仍然不许进产物** —— 报出来是为了让人知道, 不是为了硬塞一个 ptr
    assert "scale" not in out.getvalue(), out.getvalue()
    print("      `float`/`list` 字段: 报出来 (且仍不进产物)")


@test
def test_declaration_forms_that_used_to_vanish():
    """三种**最平常**的声明写法曾经静默消失, 一处一种语言 (docs/179 §8.1)。

    共同点: 丢了以后从产物上**看不出来** —— 少一个常量, 而汇总行不变、`[skip]` 没有。
    语料里也各放了一份 (§8.2), 这条夹具把**形态本身**钉死。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # Python: 带注解的常量 / 负号 / 移位表达式 —— 原先只认"裸整数字面量 + 赋值"
        py = td / "c.py"
        py.write_text("MAX: int = 8\nLOW = -5\nSHIFT = 1 << 4\n",
                      encoding="utf-8", newline="\n")
        doc, _ = potato_from.transcribe(py, "python")
        got = {c["name"]: c["value"] for c in doc["consts"]}
        assert got == {"MAX": 8, "LOW": -5, "SHIFT": 16}, got
        # Rust: `pub const` 原先**一条都不抽** (那 5 个文件丢了 14 个)
        rs = td / "c.rs"
        rs.write_text("pub const MASK: u32 = 0xFF;\npub const NEG: i32 = -5;\n",
                      encoding="utf-8", newline="\n")
        doc, _ = potato_from.transcribe(rs, "rust")
        assert {c["name"]: c["value"] for c in doc["consts"]} == {"MASK": 255, "NEG": -5}
        # Java: `void` 原先报"返回类型 'void' 无映射" —— **那句提示本身是错的**,
        # `void` 完全表示得了 (C 那边一直映 `"()"`)。注意它仍然是 `abi: "java"`,
        # 照样不会被发成 `extern fn` —— 修的是**表示**, 不是**调用约定**。
        jv = td / "D.java"
        jv.write_text("public class D {\n    private int b;\n"
                      "    public void set_b(int v) { }\n}\n",
                      encoding="utf-8", newline="\n")
        doc, rep = potato_from.transcribe(jv, "java")
        fn = [x for x in doc["functions"] if x["name"] == "set_b"]
        assert fn and fn[0]["ret"] == "()", doc["functions"]
        assert fn[0]["abi"] == "java", fn[0]
        assert not [x for x in rep.skipped if "void" in x["why"]], rep.skipped
    print("      Python 注解/负号/移位、Rust `pub const`、Java `void` 都进得来")


@test
def test_array_typed_struct_field_does_not_invalidate_the_object():
    """`[T; N]` 当**结构体字段**: 校验器曾经把它判成"未声明", 于是**整份对象非法**、
    `lomt_from` 直接 `[ERR]` 退出 —— 一个字段的问题毁掉整个模块, 比 skip 更坏。

    根因是**同一批字段被查了两遍**, 后一遍用裸字符串相等, 而且它的名单里
    **不含 enums** (docs/179 §8.1 第 4 条)。两个 agent **各自独立**撞到。
    """
    doc = potato_from._blank("m", "rust")
    doc["types"] = [{"name": "W", "fields": [{"name": "n", "type": "u32"},
                                             {"name": "w", "type": "[u64; 8]"}]}]
    assert not potato.validate(doc), potato.validate(doc)
    # 枚举类型的字段也一起钉: 后一遍的 `declared` 曾经漏掉 enums
    doc["enums"] = [{"name": "S", "variants": ["A", "B"]}]
    doc["types"].append({"name": "U", "fields": [{"name": "s", "type": "S"}]})
    assert not potato.validate(doc), potato.validate(doc)
    print("      `[u64; 8]` 与枚举类型的字段都过得了校验")


#: 多语法**语料**: 目录名就是它声称的语法。
#: 布局 = `loment/examples/multisyntax/<语法>/<NN-名字>/<名字>.lomt`。
#: 文件后缀是 `.lomt` —— 也就是走**内容兜底**那条路, 后缀一点忙都帮不上。
CORPUS = ROOT / "loment" / "examples" / "multisyntax"
#: 每个语法至少要有的项目数 (三个 agent × 5 个项目 = 15)。
CORPUS_MIN = {"python": 5, "rust": 5, "java": 5}


@test
def test_multisyntax_corpus_projects_all_check():
    """语料库: **每个项目的目录名就是它声称的语法**, 逐条验到底。

    这一条比上面那批**更接近真事**: 那批是**我**照语法写的小夹具, 这里是**别人照自己
    习惯写的整项目**。2026-09-17 实测, 它一次撞出六个"静默丢东西"的口子 ——
    `float` 字段、`AnnAssign` 常量、Rust `pub const`、Java `void`、枚举类型的字段,
    以及 `[T; N]` 字段让**整份对象**被判非法。夹具照着我写的规则写, 撞不出这些;
    **只有不受控的输入能** —— 这就是语料库存在的理由。

    钉住四件事, 缺一不可:
      1. **认对** —— `detect_lang` 的结果要等于目录名 (目录名是**声称**, 不是输入);
      2. **转成合法对象** —— v1 schema 校验干净 (`validate_errors` 为空);
      3. **产物非空** —— 前三件事在一份"什么都没抽出来"的对象上**也全都成立**,
         所以必须数一遍声明。这条是这组里最容易漏、也最要命的一条;
      4. **过 `lomentc.check`** —— 产出的 L1 单元得真能编。
    """
    if not CORPUS.is_dir():
        raise AssertionError(f"语料目录不在: {CORPUS}")
    seen: dict[str, int] = {}
    for langdir in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
        claim = langdir.name
        assert claim in potato_from.LANGS, f"目录名不是已知语法: {claim}"
        for proj in sorted(p for p in langdir.iterdir() if p.is_dir()):
            src = next(iter(sorted(proj.glob("*.lomt"))), None)
            assert src is not None, f"{proj} 里没有 .lomt"
            text = src.read_text(encoding="utf-8")
            got, why = potato_from.detect_lang(text)
            assert got == claim, f"{src}: 认成 {got!r}（{why}）, 目录声称 {claim}"
            doc, rep = potato_from.transcribe(src, claim)
            assert not rep.validate_errors, (src, rep.validate_errors)
            out, _skipped = lomt_from.emit_lomt(doc)
            n_decl = sum(out.count("pub " + k)
                         for k in ("const ", "struct ", "enum ", "extern fn "))
            assert n_decl > 0, f"{src}: 产物是空的 (一个声明都没有)"
            with tempfile.TemporaryDirectory() as t:
                unit = Path(t) / (src.stem + ".iface.lomt")
                unit.write_text(out, encoding="utf-8", newline="\n")
                mod = lomentc.load(unit)
                deps = lomentc.resolve_deps(mod, ROOT, unit.parent, entry=unit)
                errs = lomentc.check(mod, deps=deps)
                assert not errs, (src, errs[:3])
            seen[claim] = seen.get(claim, 0) + 1
    for lang, least in CORPUS_MIN.items():
        n = seen.get(lang, 0)
        assert n >= least, f"{lang} 只有 {n} 个项目 (至少 {least})"
    print(f"      {sum(seen.values())} 个语料项目全部认对/合法/非空/过检: {seen}")


for _spec in SYNTAXES:
    for _name, _fn in _mk(_spec):
        TESTS.append((_name, _fn))


def main(argv: list[str] | None = None) -> int:
    only = argv[0] if argv else None
    failed = []
    for name, fn in TESTS:
        if only and only not in name:
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:                                  # noqa: PERF203
            failed.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    n = len([1 for x, _ in TESTS if not only or only in x])
    print(f"\nloment_multisyntax_test: {n - len(failed)}/{n} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
