#!/usr/bin/env python3
# loment_ffi_test.py — FFI 第 1 阶段的门面判据 (docs/173)
#
# 判的是一整条链, 不是一层: **一个别的语言写的目标文件(.o), 由 Loment 声明成 `extern fn`,
# 用 `lomelf` 单独链接成可执行文件, 跑出正确答案**。这条一旦绿, "能调外部库"就是真的,
# 而不是"IR 里有个 declare"。
#
# 为什么必须比**运行结果**而不是比 IR: 这条路上最容易错的两处都**不改 IR** ——
#   ① C ABI 传参 (实参进 rdi/rsi/... 而不是我们自己的栈) —— 改错了 IR 一模一样;
#   ② 调用结果的落槽 —— 少写一句, 调用结果丢掉, 后面读到旧值 (实测退过 0 而不是 37)。
# 两者都只有**跑起来看结果**才抓得到。
#
# 证伪 (把实现弄坏, 确认它会红) —— **三条都真做过**:
#   * 把 `C_ARG_REGS` 前两个寄存器对调 -> 两条 ABI 判据红 (rc=22≠52, rc=213≠123);
#   * 把 `self.put(d, RAX)` 那句删掉 -> 红, 退出码变成 0 (调用结果丢掉, 后面读到旧值);
#   * 让 `parse_ll` 重新跳过 `declare` -> 红 (`finalize` 报未定义标签)。
#
# **第一次证伪时它没红**: 那时的 C 夹具只用了 `a+b` 与 `a*b`, 而这两条**可交换** ——
# 把 rdi/rsi 对调这种彻底的 ABI 错误, 结果**一模一样**。教训写进 `C_SOURCE` 的注释:
# 测传参次序的判据, 夹具里必须有**非交换运算**, 否则测的是"能跑"而不是"传参对"。
#
# 平台: 需要 clang (交叉编出 x86_64 ELF 目标文件) 与 WSL (跑 ELF)。缺任一则 SKIP。

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf                                                       # noqa: E402
import lomentc                                                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

#: 被调的那份 C。**freestanding**: 不碰 libc —— 第 1 阶段不链 libc (docs/173 §3),
#: 目标文件里一旦出现未定义符号就会被硬拒 (下面有一条判据钉这个)。
#:
#: **全部用非交换运算** —— 这条是证伪逼出来的: 第一版只用了 `a+b` 和 `a*b`, 于是"把
#: rdi/rsi 对调"这种**彻底的 ABI 错误**照样绿 (加法交换律把它盖住了)。判据挑运算时,
#: 必须让**实参次序**影响结果, 否则它测的是"能跑"而不是"传参对"。
C_SOURCE = """\
int c_add(int a, int b) { return a + b; }
int c_mul(int a, int b) { return a * b; }
int c_sub(int a, int b) { return a - b; }
int c_mix(int a, int b, int c) { return a * 100 + b * 10 + c; }
void c_nop(void) { }
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


def compile_c(clang: str, src: Path, obj: Path, extra: tuple[str, ...] = ()) -> int:
    r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-c", "-O1",
                        "-ffreestanding", "-fno-stack-protector", *extra,
                        "-o", str(obj), str(src)],
                       capture_output=True, text=True, shell=False)
    return r.returncode


def build_and_run(td: Path, lomt: Path, objs: list[Path], exit_want: int) -> tuple[int, str]:
    """Loment 源码 -> IR -> (lomelf + 外部对象) -> 跑。返回 (退出码, stderr)。"""
    mod = lomentc.load(lomt)
    errs = lomentc.check(mod)
    assert not errs, errs
    ir = lomentc.emit_llvm(mod, ROOT)
    blob, _info = lomelf.compile_ll(ir, [lomelf.ForeignObject(p) for p in objs])
    exe = td / "ffi.elf"
    exe.write_bytes(blob)
    r = subprocess.run(["wsl", "-e", "bash", "-lc",
                        f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                       capture_output=True, text=True, timeout=120, shell=False)
    return r.returncode, r.stderr


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def test_c_end_to_end():
    """C: `c_add(3,4) + c_mul(5,6) + c_sub(20,5)` 走完整条链, 退出码必须是 52。

    退出码 52 = 7 + 30 + 15。**任何一环错都到不了这个数**: 传参顺序错 -> `c_sub` 给出
    -15 而不是 15 (这是**故意**挑非交换运算的理由, 见 `C_SOURCE` 的注释 —— 第一版只用
    加减乘里可交换的那两个, 结果连"rdi/rsi 对调"都测不出来); 结果不落槽 -> 后面读到旧值
    (实测退 0 而不是 37); `declare` 被跳过 -> 链接期报未定义标签。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(
            "module ffidemo\n\n"
            "extern fn c_add(a: i32, b: i32) -> i32;\n"
            "extern fn c_mul(a: i32, b: i32) -> i32;\n"
            "extern fn c_sub(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    let r: i32 = c_add(3 as i32, 4 as i32)\n"
            "        + c_mul(5 as i32, 6 as i32)\n"
            "        + c_sub(20 as i32, 5 as i32);\n"
            "    syscall4(60, r as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "c.o"], 52)
        assert rc == 52, f"C 端到端结果不对: rc={rc} (期望 52) err={err[-300:]!r}"
        print("      C: c_add/c_mul/c_sub 端到端 -> 退出码 52 (含次序敏感的一项)")


@test
def test_three_args_and_void_call():
    """三个实参 + 一个 void 调用: 走完寄存器路径的更多位置, 且 void 调用不留返回值。

    `c_mix(a,b,c) = a*100 + b*10 + c` —— 三个位置**各有各的量级**, 于是"第 3 个实参走到
    第 1 个寄存器"这类错位会被看出来 (求和同样是交换的, 测不出位置)。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(
            "module ffi3\n\n"
            "extern fn c_mix(a: i32, b: i32, c: i32) -> i32;\n"
            "extern fn c_nop();\n\n"
            "fn _start() {\n"
            "    c_nop();\n"
            "    let r: i32 = c_mix(1 as i32, 2 as i32, 3 as i32);\n"
            "    syscall4(60, r as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "c.o"], 123)
        assert rc == 123, f"三实参/void 结果不对: rc={rc} (期望 123) err={err[-300:]!r}"
        print("      三实参 + void 调用 -> 退出码 123 (位置敏感)")


#: Rust 那份。**`#![no_std]` + `#[no_mangle] extern "C"`** —— 这就是 Rust 库对外暴露 C ABI 的
#: 标准做法 (`#[no_mangle]` 保住符号名, `extern "C"` 保住调用约定)。目标用
#: `x86_64-unknown-none`(裸机, 对象格式是 ELF), 因为这台机器上只装了 msvc 与 none 两个 target。
#: 同样挑**非交换**的减法, 理由见 `C_SOURCE` 的注释。
RUST_SOURCE = """\
#![no_std]
#[no_mangle]
pub extern "C" fn r_sub(a: i32, b: i32) -> i32 { a - b }
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
"""


def _rustc() -> str | None:
    return shutil.which("rustc")


def _rust_has_target() -> bool:
    r = subprocess.run(["rustc", "--print", "target-list"], capture_output=True,
                       text=True, shell=False)
    return "x86_64-unknown-none" in r.stdout


@test
def test_rust_end_to_end():
    """Rust: 一个 `#[no_mangle] extern "C"` 的静态库目标文件, 由 Loment 调。

    **同一个机制, 不同的语言** —— C ABI 是那条共同接口: Rust 侧 `#[no_mangle]` 保符号名、
    `extern "C"` 保调用约定, 我们这边 `extern fn` 声明 + 寄存器传参。所以"支持一个语言"
    在阶段 1/2 里就是"给它写一条构建配方 + 一条判据", 不是给每个语言写一套 FFI。
    """
    rustc, clang = _rustc(), _clang()
    if not rustc or not _rust_has_target() or not clang or not _wsl():
        print("      SKIP: 需要 rustc + x86_64-unknown-none + clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "r.rs").write_text(RUST_SOURCE, encoding="utf-8", newline="\n")
        r = subprocess.run([rustc, "--target", "x86_64-unknown-none", "--crate-type",
                            "staticlib", "--emit=obj", "-O",
                            "-o", str(td / "r.o"), str(td / "r.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"rustc 失败: {r.stderr[-300:]}"
        (td / "m.lomt").write_text(
            "module ffirust\n\n"
            "extern fn r_sub(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, r_sub(50 as i32, 8 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "r.o"], 42)
        assert rc == 42, f"Rust 端到端结果不对: rc={rc} (期望 42) err={err[-300:]!r}"
        print("      Rust: #[no_mangle] extern \"C\" 目标文件 -> 退出码 42")


@test
def test_cpp_end_to_end():
    """C++: `extern "C"` 包一层 —— 与 C 同一个机制 (C 系列的语言都走这条)。

    直接调 C++ 的重载/名字修饰符号需要 Itanium 还原, 那是另一件事; 库对外暴露的那一面
    照例是 `extern "C"`。
    """
    clang = _clang()
    cxx = shutil.which("clang++") or str(Path(clang).with_name("clang++.exe")) \
        if clang else None
    if not clang or not cxx or not Path(cxx).exists() or not _wsl():
        print("      SKIP: 需要 clang++ + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "cxx.cc").write_text(
            "namespace demo { int div(int a, int b) { return a / b; } }\n"
            "extern \"C\" int cxx_div(int a, int b) { return demo::div(a, b); }\n",
            encoding="utf-8", newline="\n")
        r = subprocess.run([cxx, "--target=x86_64-unknown-linux-gnu", "-c", "-O1",
                            "-ffreestanding", "-fno-stack-protector", "-fno-exceptions",
                            "-fno-rtti", "-o", str(td / "cxx.o"), str(td / "cxx.cc")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"clang++ 失败: {r.stderr[-300:]}"
        (td / "m.lomt").write_text(
            "module fficxx\n\n"
            "extern fn cxx_div(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, cxx_div(84 as i32, 2 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "cxx.o"], 42)
        assert rc == 42, f"C++ 端到端结果不对: rc={rc} (期望 42) err={err[-300:]!r}"
        print("      C++: extern \"C\" 包装 -> 退出码 42")


@test
def test_object_with_relocations_is_rejected():
    """对象里有重定位 -> **硬拒**。

    第 1 阶段不实现跨对象符号重定位。猜一个偏移的后果是"调到一个错地址" —— 运行期崩溃或
    算出错值, 而不是编译期报错。所以宁可不支持, 也不静默错链。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # -fpic 会为外部/常量引用生成 R_X86_64_* 重定位
        (td / "c.c").write_text(
            "static const char *msg = \"reloc\";\n"
            "const char *c_msg(void) { return msg; }\n",
            encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o", ("-fpic",)) == 0
        try:
            lomelf.ForeignObject(td / "c.o")
        except lomelf.Unsupported as e:
            assert "重定位" in str(e), e
        else:
            raise AssertionError("带重定位的目标文件应当被拒")
        print("      带重定位的对象: 硬拒")


@test
def test_object_with_undefined_symbol_is_rejected():
    """对象引用了未定义符号 (典型是 libc) -> **硬拒**, 不猜。"""
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text("extern int not_defined_anywhere(int);\n"
                                "int c_wrap(int x) { return not_defined_anywhere(x); }\n",
                                encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0
        try:
            lomelf.ForeignObject(td / "c.o")
        except lomelf.Unsupported as e:
            assert "未定义" in str(e), e
        else:
            raise AssertionError("引用未定义符号的对象应当被拒")
        print("      引用未定义符号的对象: 硬拒 (第 1 阶段不链 libc)")


@test
def test_link_is_elf_only():
    """`--link` 只支持 ELF 目标 —— PE 侧的 FFI 还没做, 要**明说**而不是忽略参数。"""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py"),
                        "in.ll", "--target", "pe", "--link", "x.o"],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 2 and "只支持 ELF" in r.stderr, (r.returncode, r.stderr[-200:])
    print("      --link 配 PE: 明确拒绝")


@test
def test_program_without_extern_is_unchanged():
    """**反向的一条**: 没有 `extern` 的程序一个字都不该变。

    新增 `parse_ll` 的第三个返回值 / 新增 `Emitter.externs` 都可能顺手改变老程序的行为,
    这条钉住"老路径不动": 同一份 IR, 带不带 `--link` 之外的一切照旧。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        ir = ("define void @_start() {\n"
              "entry:\n"
              "  %t1 = call i64 asm sideeffect \"syscall\", "
              "\"={ax},{ax},{di},{si},{dx}\"(i64 60, i64 7, i64 0, i64 0)\n"
              "  ret void\n"
              "}\n")
        (td / "a.ll").write_text(ir, encoding="utf-8", newline="\n")
        blob, info = lomelf.compile_ll(ir)
        assert info["linked"] == [] and info["objects"] == [], info
        assert len(blob) > 0
        print("      无 extern 的程序: 路径照旧")


def main() -> int:
    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:                                      # noqa: BLE001
            failed.append((fn.__name__, e))
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print()
    if failed:
        print(f"loment_ffi_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
        return 1
    print(f"loment_ffi_test: {len(TESTS)}/{len(TESTS)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
