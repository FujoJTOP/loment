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

#: **`.lomt` 后缀里装着 C** —— docs/175 §5 那条原话的形状。这份是照 2026-09-17 一个子
#: agent 真写出来的 C 蒸馏的, 刻意保留了三处**当时把工具链绊倒**的东西:
#:   * Allman 风格 (`{` 在下一行) —— 第一版 `_C_FNDEF` 只认同行 `{`;
#:   * `unsigned` **单独写** —— 类型表里原先只有 `unsigned int`, 于是 5 个函数被丢 4 个;
#:   * 单引号字符字面量 `'0'` —— Loment 词法没有它, 所以**词法**先报错 (不是解析),
#:     而诊断工具第一版只认"期望 module"那条消息, 于是提示不出现。
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


# ---------------------------------------------------------------- 0. 按内容认出语法

@test
def test_sniffing_picks_the_right_language():
    """**按内容**定语法: `.lomt` 后缀 + 外源内容 = 要能认出来 (docs/175 §5)。

    这是整个多语法特性的入口 —— 认不出, 后面那半段根本不会被走到。
    每条判据都带一个"依据"字符串, 出问题时能看出是**哪条特征**判的。
    """
    cases = [
        ("loment", "module m\n\nfn f() -> u32 {\n    return 1;\n}\n"),
        ("loment", "module t\n\ncapability c : disk[0..4]\n\nfn f() {}\n"),
        ("c", C_IN_LOMT),                       # ← `.lomt` 装着 C
        ("c", "#include <stdio.h>\nint main(void) { return 0; }\n"),
        ("rust", '#[no_mangle]\npub extern "C" fn g(x: i32) -> i32 { x + 1 }\n'),
        ("rust", "pub struct P {\n    x: i32,\n}\n\npub fn h(x: i32) -> i32 { x }\n"),
        ("python", PY_SOURCE),
        ("", "some prose, no code at all\n"),
    ]
    for want, src in cases:
        got, why = potato_from.detect_lang(src)
        assert got == want, f"认成 {got!r}（{why}）, 期望 {want!r}：{src[:40]!r}"
    # 注释里的关键字**不许**把人骗过去 (这里的 C 还是"整行写完"那种)
    got, _ = potato_from.detect_lang("// fn def module 都在注释里\nint a(void) { return 1; }\n")
    assert got == "c", got
    # **反过来也要不撞车**: Loment 的一行函数长得很像, 不许被认成 C。
    # 判据是"开头是 C 的类型关键字吗" —— Loment 从 `fn`/`struct`/`const` 开头, 都不是。
    for loment_snippet in ("fn f() { return 1; }\n", "struct S { a: u32 }\n",
                           "pub fn g(x: u32) { }\n"):
        got, why = potato_from.detect_lang(loment_snippet)
        assert got != "c", f"把 Loment 认成了 C（{why}）：{loment_snippet!r}"
    print("      8 个样例都认对; 注释不误导; Loment 的一行函数不被认成 C")


@test
def test_detection_is_actually_used_by_the_pipeline():
    """检测不是摆设: 一份 `.lomt` 装着 C, `--lang auto` 必须走通全链。"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        p = td / "mod.lomt"                       # ← 后缀是 .lomt
        p.write_text(C_IN_LOMT, encoding="utf-8", newline="\n")
        lang, why = potato_from.resolve_lang(p, "auto")
        assert lang == "c", (lang, why)
        doc, rep = potato_from.transcribe(p, "auto")
        assert not rep.validate_errors, rep.validate_errors[:2]
        text, skipped = lomt_from.emit_lomt(doc)
        assert "pub extern fn c_fact(n: i32) -> i32;" in text, text
        assert "pub extern fn c_gcd(a: u32, b: u32) -> u32;" in text, text
        assert "pub extern fn c_popcount(v: u32) -> i32;" in text, text
        # `char *buf` -> `str` -> 出不了 FFI 第 1 阶段那道闸门。**必须报出来**, 不能少一条
        # 声明还不出声 (Loment 没有重载, 少一条声明是安全的 —— 但沉默不是)。
        assert "不是 C ABI" not in str(skipped), skipped
        assert "c_utoa" in [n for n, _ in skipped], skipped
        # 一份本来就是 Loment 的 `.lomt` 不许被当成外源语法
        q = td / "real.lomt"
        q.write_text("module real\n\nfn f() -> u32 {\n    return 1;\n}\n",
                     encoding="utf-8", newline="\n")
        try:
            potato_from.transcribe(q, "auto")
        except ValueError as e:
            assert "Loment 语法" in str(e), str(e)
        else:
            raise AssertionError("一份 Loment 源被当成外源语法转写了")
    print("      `.lomt` 装着 C -> 认出来 -> 转出接口单元; 确实是 Loment 的会被告知走编译器")


@test
def test_diag_does_not_tell_you_to_fix_valid_c():
    """诊断工具**不许**对一份语法正确的 C 说"改那一行的写法"。

    实测过的那条弯路: 报的是 `63:25: 非法字符 "'"`（Loment 词法没有单引号字面量,
    **词法**先于解析报错）, 而工具照 E019 的通用建议让人去改那一行 —— 照做会把一份好 C
    改坏。第一版提示只认"期望 module"那条消息, 所以它**不出现**; 判据因此不测那条消息,
    测的是"有错 + 内容是别的语言"这个**行为**。
    """
    import loment_diag
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        c = td / "m.lomt"
        c.write_text(C_IN_LOMT, encoding="utf-8", newline="\n")
        note = loment_diag.foreign_note(c, ["63:25: 非法字符 \"'\""])
        assert note and "C" in note, note
        assert "改" in note and "不是 Loment" in note, note
        # 一份**真 Loment** 带 typo: 不许出现这条提示 (否则把人从正确方向引开)
        lp = td / "t.lomt"
        lp.write_text("module t\n\nfn f() -> u32 {\n    return 1\n}\n",
                      encoding="utf-8", newline="\n")
        assert loment_diag.foreign_note(lp, ["5:1: 期望 ;，得到 '}'"]) is None
        # 没报错时也不该有
        assert loment_diag.foreign_note(c, []) is None
    print("      C 装 `.lomt` -> 给出对得上的提示; Loment 的 typo -> 不给")


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


@test
def test_c_in_lomt_end_to_end():
    """**端到端**: 一份 C 装 `.lomt` -> 认出来 -> 接口单元 -> L1 调用 -> 链真目标文件 -> 跑。

    这是 docs/175 §5 那条的完整形态, 也是"多语法系统"这个说法唯一值得信的证据:
    它跨了三个工具 (clang 编 C、lomt_from 转、lomentc 编 Loment)、两道链接 (lomelf 把
    外部 `.o` 链进来), 最后**真的跑出正确结果** —— 退出码由 Python 独立算出来对照。

    **`-x c` 不能省**: clang 按**后缀**认语言, 一份 `.lomt` 会被它当成 linker 输入
    (警告 `'linker' input unused`) 然后 **rc=0 且什么都不产出** —— 2026-09-17 实测
    撞到的第一个坑, 而且它**不报错**, 只在你去找产物时才发现是空的。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = td / "m.lomt"                       # ← 后缀是 .lomt, 内容是 C
        src.write_text(C_IN_LOMT, encoding="utf-8", newline="\n")

        iface, skipped = _iface_from_source(td, "m", C_IN_LOMT, "c")
        txt = iface.read_text(encoding="utf-8")
        assert "c_fact" in txt and "c_gcd" in txt and "c_popcount" in txt, txt
        assert "c_utoa" in [n for n, _ in skipped], skipped

        obj = td / "m.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-x", "c", "-c",
                            "-O1", "-ffreestanding", "-fno-stack-protector",
                            "-o", str(obj), str(src)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        assert obj.exists() and obj.stat().st_size > 0, "clang 没产出目标文件 (漏了 -x c?)"

        # Python 独立算: 6! = 720, gcd(1071,462) = 21, popcount(0xbeef) = 13
        want = (720 + 21 + 13) % 256
        _run_with_iface(td, iface,
                        f'module msc\n\nuse "{(td / "m_iface.lomt").as_posix()}"\n\n'
                        "fn _start() {\n"
                        f"    let a: u32 = c_fact(6 as i32) as u32;\n"
                        f"    let b: u32 = c_gcd(1071 as u32, 462 as u32);\n"
                        f"    let c: u32 = c_popcount(48879 as u32) as u32;\n"
                        "    syscall4(60, ((a + b + c) % 256) as u64, 0, 0);\n"
                        "}\n", [obj], want)
    print(f"      C 装 `.lomt`: 认出 -> 接口单元 -> 链真目标文件 -> 退出码 {want} (由 Python 算出)")


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
