#!/usr/bin/env python3
# loment_multisyntax_test.py — 多语法前端判据 (docs/179, docs/175 §5/§6 第 4 条)
#
# 判据面: **"前端 -> L1 -> 冻结核心"这条路走通了没有**, 逐条钉住:
#
#   1. `potato_from` 发出的形式对象**必须合法** (原先每一份都非法, 见下)
#   2. Rust 与 C 两条前端各自**端到端**: 外源源码 -> 接口单元 -> Loment 程序调用它
#      -> 链上真正编出来的目标文件 -> 跑出预期退出码
#   3. ABI 闸门**出声**: 非 C ABI 的函数不许被发成 `extern fn`, 而且要报出来
#   4. 表示不了的对象**报错退出**, 不静默丢
#   5. 生成是**确定性**的 (同一份输入永远同一串字节) —— 否则产物进不了判据
#
# 为什么要第 1 条: `_blank()` 一直都少一个 `guards` 键, 于是 `potato_from` 发出的
# **每一份**对象都是非法 v1, 而这个事实只出现在它自己的报告里 (`validate_errors`),
# **没有任何测试读那份报告**。"有字段在报告里但没判据"= 静默, 只是换了个地方静默。
#
# 用法: python tools/loment_multisyntax_test.py
# 退出码: 0 = 全过 / 1 = 有失败

from __future__ import annotations

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

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

C_SOURCE = """\
int c_triple(int a) { return a * 3; }
struct Pair { int a; int b; };
int c_pair(struct Pair p) { return p.a + p.b; }
"""
#: `c_triple` 是标量 -> 发得出来。7 * 3 = **21**。
#: `c_pair` 收的是**聚合类型** -> 超出 FFI 第 1 阶段。
#: 注意它是在**转写**那步就被跳过的 (`potato_from` 的 `_c_type` 映不出 `struct Pair`),
#: 还没走到 `lomt_from` —— 所以判据要看**整条流水线**的跳过项, 不是只看最后一步。

RUST_SOURCE = """\
#![no_std]

#[no_mangle]
pub extern "C" fn r_bump(x: i32) -> i32 { x + 1 }

pub fn plain(x: i32) -> i32 { x * 2 }

#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
"""
#: `r_bump` 是 `extern "C"` -> 发得出来。20 + 1 = **21**。
#: `plain` 是普通 `pub fn` (Rust ABI) -> **不许**发成 `extern fn`。
#: 这条与 C 那条不同: `plain` 能转写 (签名是标量), 是 **`lomt_from` 那一步**按 ABI 挡下的
#: —— 两条判据合起来才覆盖"两个闸门各自都在起作用"。
#: `#![no_std]` + panic handler 是 rustc 编 `x86_64-unknown-none` 静态库的最低要求。

PY_SOURCE = """\
def f(a: int) -> int:
    return a + 1
"""


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _wsl() -> bool:
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                               # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _rustc() -> str | None:
    return shutil.which("rustc")


def _rust_has_target() -> bool:
    r = subprocess.run(["rustc", "--print", "target-list"], capture_output=True,
                       text=True, shell=False)
    return "x86_64-unknown-none" in r.stdout


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- 夹具

def _iface_from_source(td: Path, name: str, src: str, lang: str) -> tuple[Path, list]:
    """外源源码 -> 接口单元 .lomt。返回 (路径, **整条流水线**跳过的项)。

    走的是**工具本身**的路径: `potato_from` 转写 + `lomt_from` 发射, 不绕过任何一步。
    跳过项把**两步**的合起来报 —— 只报最后一步会让"转写那步丢的"看起来像没丢。
    """
    ext = {"c": ".c", "rust": ".rs", "python": ".py"}[lang]
    p = td / f"{name}{ext}"
    p.write_text(src, encoding="utf-8", newline="\n")
    doc, rep = potato_from.transcribe(p, lang, "strict")
    assert not rep.validate_errors, f"{lang} 转写出的对象非法: {rep.validate_errors[:2]}"
    text, skipped = lomt_from.emit_lomt(doc)
    out = td / f"{name}_iface.lomt"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out, [(s["name"], s["why"]) for s in rep.skipped] + skipped


def _why(skipped: list, name: str) -> str:
    """跳过项里那个名字的**原因** (没有就断言失败 —— 这条判据要的就是"它被报出来了")。"""
    hits = [w for n, w in skipped if n == name]
    assert hits, f"{name} 没有被报为跳过, 只看到 {[n for n, _ in skipped]}"
    return hits[0]


def _run_with_iface(td: Path, iface: Path, main_src: str, objs: list[Path],
                    exit_want: int) -> tuple[int, str]:
    """Loment 主程序 `use` 生成的接口单元 -> 链上外部对象 -> 跑。"""
    main = td / "main.lomt"
    main.write_text(main_src, encoding="utf-8", newline="\n")
    mod = lomentc.load(main)
    deps = lomentc.resolve_deps(mod, ROOT, main.parent, entry=main)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, errs
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    blob, _ = lomelf.compile_ll(ir, [lomelf.load_foreign(p) for p in objs])
    exe = td / "ms.elf"
    exe.write_bytes(blob)
    r = subprocess.run(["wsl", "-e", "bash", "-lc",
                        f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                       capture_output=True, text=True, timeout=120, shell=False)
    assert r.returncode == exit_want, \
        f"退出码 {r.returncode} != {exit_want} (stderr {r.stderr[-200:]!r})"
    return r.returncode, r.stderr


# ---------------------------------------------------------------- 1. 对象必须合法

@test
def test_transcribed_objects_are_valid():
    """`potato_from` 的三条转写路径都**必须**发出合法的形式对象。

    这条原先不存在, 而 `_blank()` 少一个 `guards` 键 —— 于是**每一份**对象都不合法,
    事实只躺在报告的 `validate_errors` 里没人读。补上这条才叫把它钉住。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for lang, src, ext in (("c", C_SOURCE, ".c"), ("rust", RUST_SOURCE, ".rs"),
                               ("python", PY_SOURCE, ".py")):
            p = td / f"x{ext}"
            p.write_text(src, encoding="utf-8", newline="\n")
            doc, rep = potato_from.transcribe(p, lang, "strict")
            assert not rep.validate_errors, f"{lang}: {rep.validate_errors[:2]}"
            assert potato.validate(doc) == [], f"{lang}: {potato.validate(doc)[:2]}"
    print("      三条转写路径 (c/rust/python) 的对象都合法")


# ---------------------------------------------------------------- 2. 两条前端端到端

@test
def test_c_frontend_end_to_end():
    """C -> 接口单元 -> Loment 调它 -> 链上 clang 编出的目标文件 -> 退出码 21。"""
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        iface, skipped = _iface_from_source(td, "ffi_c", C_SOURCE, "c")
        txt = iface.read_text(encoding="utf-8")
        assert "pub extern fn c_triple(a: i32) -> i32;" in txt, txt
        # 聚合参数那条必须**跳过** (不是发出来再让编译器报错)
        assert not any("c_pair" in ln for ln in txt.splitlines()
                       if ln.startswith("pub extern fn")), txt
        # 它在**转写**那步就被挡下了 (聚合类型映不过来), 所以理由里点的是参数类型
        assert "无映射" in _why(skipped, "c_pair"), skipped
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-c", "-O1",
                            "-ffreestanding", "-fno-stack-protector",
                            "-o", str(td / "c.o"), str(td / "c.c")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"clang 失败: {r.stderr[-300:]}"
        _run_with_iface(td, iface,
                        f'module msc\n\nuse "{iface.as_posix()}"\n\n'
                        "fn _start() {\n"
                        "    syscall4(60, c_triple(7 as i32) as u64, 0, 0);\n"
                        "}\n", [td / "c.o"], 21)
    print("      C: 源码 -> 接口单元 -> L1 调用 -> 退出码 21 (聚合参数那条被跳过并报出)")


@test
def test_rust_frontend_end_to_end():
    """Rust -> 接口单元 -> Loment 调它 -> 链上 rustc 编出的目标文件 -> 退出码 21。

    `r_bump` 是 `extern "C"`; `plain` 是普通 `pub fn` —— 后者**不许**变成 `extern fn`
    (那是 Rust ABI, 照 C ABI 调就是错编)。所以这条同时钉住"发得出来的发出来了"
    与"发不出来的没被硬发"。
    """
    rustc, clang = _rustc(), _clang()
    if not rustc or not _rust_has_target() or not clang or not _wsl():
        print("      SKIP: 需要 rustc + x86_64-unknown-none + clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        iface, skipped = _iface_from_source(td, "ffi_rs", RUST_SOURCE, "rust")
        txt = iface.read_text(encoding="utf-8")
        assert "pub extern fn r_bump(x: i32) -> i32;" in txt, txt
        assert "plain" not in txt, txt
        # 这一条能转写 (签名是标量), 是 **lomt_from 那一步**按 ABI 挡下的
        assert "不是 C ABI" in _why(skipped, "plain"), skipped
        (td / "r.rs").write_text(RUST_SOURCE, encoding="utf-8", newline="\n")
        r = subprocess.run([rustc, "--target", "x86_64-unknown-none", "--crate-type",
                            "staticlib", "--emit=obj", "-O",
                            "-o", str(td / "r.o"), str(td / "r.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"rustc 失败: {r.stderr[-300:]}"
        _run_with_iface(td, iface,
                        f'module msr\n\nuse "{iface.as_posix()}"\n\n'
                        "fn _start() {\n"
                        "    syscall4(60, r_bump(20 as i32) as u64, 0, 0);\n"
                        "}\n", [td / "r.o"], 21)
    print("      Rust: extern \"C\" 发出来并跑通 (21); 普通 pub fn 被跳过并报出")


# ---------------------------------------------------------------- 3. 闸门出声

@test
def test_python_abi_is_not_emitted_as_extern():
    """Python 的函数不是平台 C ABI —— **一条都不许**发成 `extern fn`。

    它要走进程桥 (`loment/lib/proc.lomt`, docs/173 §4), 不是 FFI 声明。
    这条钉住"ABI 记在对象里"真的起了作用: 去掉 `abi` 字段, 这里就会硬发出一个
    调用约定错误的声明。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "s.py"
        p.write_text(PY_SOURCE, encoding="utf-8", newline="\n")
        doc, rep = potato_from.transcribe(p, "python", "strict")
        assert not rep.validate_errors, rep.validate_errors
        assert doc["functions"] and all(f.get("abi") == "python" for f in doc["functions"]), \
            doc["functions"]
        text, skipped = lomt_from.emit_lomt(doc)
        # 判**代码行**, 不是整份文本: 文件头的注释里就写着 "函数是 `extern fn`" ——
        # 拿整份文本判会一直红, 而那是注释不是声明。
        assert not [ln for ln in text.splitlines() if ln.startswith("pub extern fn")], text
        assert "不是 C ABI" in _why(skipped, "f"), skipped
    print("      Python: 函数不落成 extern fn (走进程桥), 且跳过项被报出")


@test
def test_abi_must_be_a_known_value():
    """`abi` 是可选的, 但**给了就必须是已知值** —— 拼错一个 ABI 名不许一路静默。"""
    doc = potato_from._blank("m", "c")
    doc["functions"].append({"name": "f", "params": [], "ret": "u32", "abi": "cxx"})
    errs = potato.validate(doc)
    assert any("abi 非法" in e for e in errs), errs
    doc["functions"][0]["abi"] = "c"
    assert potato.validate(doc) == [], potato.validate(doc)
    # 不写 abi 也合法 (可选) —— 这是"可加字段不必升版本"的前提
    del doc["functions"][0]["abi"]
    assert potato.validate(doc) == [], potato.validate(doc)
    print("      abi: 已知值通过 / 拼错报错 / 省略合法")


# ---------------------------------------------------------------- 4. 发不出来就报错

@test
def test_unrepresentable_object_is_rejected():
    """对象里有 L1 那侧接不上的东西 (traits / layouts / imports) -> **报错退出**, 不静默丢。"""
    base = potato_from._blank("m", "rust")
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


# ---------------------------------------------------------------- 5. 确定性

@test
def test_emission_is_deterministic():
    """同一份对象 -> 同一串字节。产物要进判据, 就不能带任何非确定性。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        a, _ = _iface_from_source(td, "d1", C_SOURCE, "c")
        b, _ = _iface_from_source(td, "d1", C_SOURCE, "c")
        assert a.read_bytes() == b.read_bytes()
        # 顺序无关: 把 functions 倒过来, 产物应当不同但**仍然确定**
        doc = json.loads(json.dumps(potato_from._blank("m", "c")))
        doc["functions"] = [{"name": "b_fn", "params": [], "ret": "u32", "abi": "c"},
                            {"name": "a_fn", "params": [], "ret": "u32", "abi": "c"}]
        t1, _ = lomt_from.emit_lomt(doc)
        doc["functions"].reverse()
        t2, _ = lomt_from.emit_lomt(doc)
        assert t1 != t2, "顺序被吃掉了 —— 那是在无声地重新排序用户的接口"
    print("      同一份对象 -> 同一串字节; 顺序保留, 不重排")


def main(argv: list[str] | None = None) -> int:
    only = argv[0] if argv else None
    failed = []
    for fn in TESTS:
        if only and only not in fn.__name__:
            continue
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:                                  # noqa: PERF203
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    n = len([f for f in TESTS if not only or only in f.__name__])
    print(f"\nloment_multisyntax_test: {n - len(failed)}/{n} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
