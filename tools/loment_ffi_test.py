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

#: 调 `C_SOURCE` 的那份 Loment: 三个 `extern fn`, 结果 7 + 30 + 15 = **52**。
#: 顺序敏感的是 `c_sub` (20-5=15; 寄存器对调会得到 -15) —— 与 `C_SOURCE` 的注释同一条理由。
#: 常量放在模块级是为了让 `loment_elf_test` 的自举镜像判据共用**同一份**源码, 不抄第二遍。
LOMENT_SOURCE = """\
module ffidemo

extern fn c_add(a: i32, b: i32) -> i32;
extern fn c_mul(a: i32, b: i32) -> i32;
extern fn c_sub(a: i32, b: i32) -> i32;

fn _start() {
    let r: i32 = c_add(3 as i32, 4 as i32)
        + c_mul(5 as i32, 6 as i32)
        + c_sub(20 as i32, 5 as i32);
    syscall4(60, r as u64, 0, 0);
}
"""


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _llvm_bin(name: str) -> str | None:
    """LLVM 自带工具 (`llvm-ar` 等) —— 与 clang 同目录, Windows 上就有。"""
    clang = _clang()
    if not clang:
        return None
    p = Path(clang).with_name(name)
    return str(p) if p.exists() else None


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
    blob, _info = lomelf.compile_ll(ir, [lomelf.load_foreign(p) for p in objs])
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
        (td / "m.lomt").write_text(LOMENT_SOURCE, encoding="utf-8", newline="\n")
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


#: 第二个 `.o` 的内容 —— 只为"**两个**外部对象一起链"那条判据存在。
C2_SOURCE = """\
int d_scale(int a, int b) { return a * 10 - b; }
"""

#: 调**两个** `.o` 的那份 Loment。它存在的理由是**名字池的基**:
#: 自举链接器把外部符号名拷进**输入缓冲的尾部** —— 标签表存的是偏移, 而 `lbl_find` 拿
#: 输入缓冲当基。若把名字留在**对象缓冲**里, 第二个 `.o` 一读进来就会把第一个的符号名
#: 盖掉, 然后在回填时报"未定义的标签: c_add"。**单对象测不出这个**: 只有一个对象时那块
#: 缓冲始终是完整的。所以这条判据必须有两个对象才有意义。
#: 退出码 75 = c_add(3,4)=7 + d_scale(7,2)=68。
LOMENT_TWO_OBJECTS_SOURCE = """\
module ffitwo

extern fn c_add(a: i32, b: i32) -> i32;
extern fn d_scale(a: i32, b: i32) -> i32;

fn _start() {
    let r: i32 = c_add(3 as i32, 4 as i32) + d_scale(7 as i32, 2 as i32);
    syscall4(60, r as u64, 0, 0);
}
"""


@test
def test_two_foreign_objects():
    """两个 `.o` 一起 `--link`: 第一个对象导出的符号不能被第二个对象的读入盖掉。

    退出码 75 = 7 + (70 - 2)。`d_scale` 也是**非交换**的 (`a*10 - b`), 所以传参次序在
    这条里同样被测到。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "c.c").write_text(C_SOURCE, encoding="utf-8", newline="\n")
        (td / "d.c").write_text(C2_SOURCE, encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0, "C 编译失败"
        assert compile_c(clang, td / "d.c", td / "d.o") == 0, "C 编译失败"
        (td / "m.lomt").write_text(LOMENT_TWO_OBJECTS_SOURCE, encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "c.o", td / "d.o"], 75)
        assert rc == 75, f"两个外部对象结果不对: rc={rc} (期望 75) err={err[-300:]!r}"
        print("      两个外部对象一起链 -> 退出码 75")


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
def test_python_via_process_bridge():
    """Python: 起一个 `python3`, 让它 import 它自己的库, 把结果读回来。

    **这是第二条腿, 不是 C ABI 那条** (docs/173 §2 的说明): Python 不是"导出 C ABI 的库",
    是解释器。所以这里**不链接** —— 起一个进程, 把代码交给它, 把 stdout 读回来。判据里用的
    是 Python 自带的 `json` 库 (不是我们写死的一个数), 所以它证明的是"**真用上了 Python 的库**",
    而不是"能起个进程"。

    退出码 = `len(json.dumps([1,2,3]))` = 9 (即 `[1, 2, 3]` 的长度)。数字是 Python 算的,
    Loment 只把读回来的第一个字节转成退出码 —— 中间没有任何一处是我们自己算的。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    code = r"""import json
print(len(json.dumps([1,2,3])))"""
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "p.lomt").write_text(
            "module pybridge\n\n"
            "use proc\n\n"
            "fn _start() {\n"
            "    let buf: ptr = alloc(1024);\n"
            "    let n: i64 = proc_sh(\"python3 -c 'import json; print(len(json.dumps([1,2,3])))'\",\n"
            "                         buf, 1024);\n"
            "    if n <= 0 {\n"
            "        syscall4(60, 200 as u64, 0, 0);\n"
            "    }\n"
            "    let d: u32 = load8(buf, 0) - 48;\n"       # '9' -> 9
            "    syscall4(60, d as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        # 先把桥自己的模块装进单元 (use proc)
        mod = lomentc.load(td / "p.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "p.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        blob, _info = lomelf.compile_ll(ir)
        exe = td / "p.elf"
        exe.write_bytes(blob)
        r = subprocess.run(["wsl", "-e", "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=180, shell=False)
        assert r.returncode == 9, (
            f"Python 桥结果不对: rc={r.returncode} (期望 9 = len(json.dumps([1,2,3]))) "
            f"err={r.stderr[-300:]!r}")
        print("      Python: python3 + json 库, 输出读回 -> 退出码 9")


@test
def test_java_via_process_bridge():
    """Java: 同一条腿。这台机器上 WSL 里没有 java, 所以**这里 SKIP** —— 配方在判据里。

    `proc_sh` 收的是一条 shell 命令, 所以 Java 与 Python 走的是**同一个函数**, 区别只在命令
    与"怎么把结果打出来"。真正要写的是那段 Java: 编译成 class (或直接用 `java -` 的单文件源),
    跑它, 结果打到 stdout, 我们读回来。没有 java 的机器上这条一律 SKIP, 不是静默通过。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    has = subprocess.run(["wsl", "-e", "bash", "-lc", "command -v java"],
                         capture_output=True, text=True, timeout=60, shell=False)
    if has.returncode != 0:
        print("      SKIP: WSL 里没有 java (配方见本判据的 docstring)")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "Hello.java").write_text(
            "public class Hello { public static void main(String[] a) { "
            "System.out.println(7); } }\n", encoding="utf-8", newline="\n")
        (td / "j.lomt").write_text(
            "module javabridge\n\n"
            "use proc\n\n"
            "fn _start() {\n"
            "    let buf: ptr = alloc(1024);\n"
            "    let n: i64 = proc_sh(\"cd /tmp && javac Hello.java && java Hello\", buf, 1024);\n"
            "    if n <= 0 {\n"
            "        syscall4(60, 200 as u64, 0, 0);\n"
            "    }\n"
            "    syscall4(60, (load8(buf, 0) - 48) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "j.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "j.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps))
        exe = td / "j.elf"
        exe.write_bytes(blob)
        r = subprocess.run(["wsl", "-e", "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=180, shell=False)
        assert r.returncode == 7, f"Java 桥结果不对: rc={r.returncode} (期望 7)"
        print("      Java: javac + java, 输出读回 -> 退出码 7")


@test
def test_javascript_via_process_bridge():
    """JavaScript: 同一条腿上的第三个语言 —— 换个命令就行, **代码一行不用改**。

    这条顺带钉住一件事: `proc_sh` 是**通用**的 (起进程 + 读 stdout), 不是"Python 专用"。
    语言数量在第二条腿上**不是**工作量 —— 有解释器就能用; 真正的工作量在第一条腿
    (C ABI 那一族) 的链接能力上。
    """
    if not _wsl():
        print("      SKIP: 需要 WSL")
        return
    has = subprocess.run(["wsl", "-e", "bash", "-lc", "command -v node"],
                         capture_output=True, text=True, timeout=60, shell=False)
    if has.returncode != 0:
        print("      SKIP: WSL 里没有 node")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "j.lomt").write_text(
            "module jsbridge\n\n"
            "use proc\n\n"
            "fn _start() {\n"
            "    let buf: ptr = alloc(1024);\n"
            "    let n: i64 = proc_sh(\"node -e 'console.log(JSON.stringify([1,2,3]).length)'\",\n"
            "                         buf, 1024);\n"
            "    if n <= 0 {\n"
            "        syscall4(60, 200 as u64, 0, 0);\n"
            "    }\n"
            "    syscall4(60, (load8(buf, 0) - 48) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "j.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "j.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps))
        exe = td / "j.elf"
        exe.write_bytes(blob)
        r = subprocess.run(["wsl", "-e", "bash", "-lc",
                            f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}"],
                           capture_output=True, text=True, timeout=180, shell=False)
        # JSON.stringify([1,2,3]) = "[1,2,3]" -> 长度 7
        assert r.returncode == 7, (
            f"JS 桥结果不对: rc={r.returncode} (期望 7 = len(JSON.stringify([1,2,3]))) "
            f"err={r.stderr[-300:]!r}")
        print("      JavaScript: node + JSON 库, 输出读回 -> 退出码 7")


@test
def test_multi_object_cross_reference():
    """**多目标文件**: `a.o` 调 `b.o` —— 跨对象重定位必须解开。

    这是 `docs/173` 阶段 2 的正题, 也是"真实 C 库"与"手工挑出来的单函数玩具"的分界线:
    真实库就是多目标文件、成员互相引用、每个成员都带重定位。

    退出码 22 = `c_add(3,4)` = 7 + `c_sub(20,5)` = 15。`c_sub` 只存在于 `b.o`,
    所以 `a.o` 里那条 `R_X86_64_PLT32` 只能靠"**先摆好所有对象再建符号表**"才解得出 ——
    先解后摆的话 `c_sub` 还是 UND。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "b.c").write_text("int c_sub(int a, int b) { return a - b; }\n",
                                encoding="utf-8", newline="\n")
        (td / "a.c").write_text(
            "extern int c_sub(int, int);\n"
            "__attribute__((noinline)) int c_add(int a, int b) { return a + b + c_sub(20, 5); }\n",
            encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "a.c", td / "a.o") == 0, "C 编译失败"
        assert compile_c(clang, td / "b.c", td / "b.o") == 0, "C 编译失败"
        # 先确认这条判据真的踩在重定位上 —— 否则它测的是别的东西
        fo = lomelf.ForeignObject(td / "a.o")
        assert fo.relocs, "a.o 里没有重定位, 这条判据没测到该测的东西"
        assert "c_sub" in fo.undefined, f"a.o 没把 c_sub 记成未定义: {fo.undefined}"
        (td / "m.lomt").write_text(
            "module multi\n\n"
            "extern fn c_add(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [td / "a.o", td / "b.o"], 22)
        assert rc == 22, f"跨对象引用结果不对: rc={rc} (期望 22) err={err[-300:]!r}"
        print("      多目标文件 (a.o 调 b.o, 跨对象重定位) -> 退出码 22")


@test
def test_archive_selects_only_needed_members():
    """**静态库 `.a`**: 只挑定义"当前需要的符号"的成员, **不整包收**。

    退出码 22, 与上一条同一个程序, 只是把 `a.o`/`b.o` 打成了归档。

    判据的关键是那个 **`junk.o`**: 它定义 `unused_junk`（谁也不需要）、却**引用了 `printf`**。
    - 整包收的实现会把它拖进来, 于是"引用了未定义符号" → **整个库被拒**;
    - 固定点选择只收 `a.o`(定义 c_add) → 它需要 c_sub → 再收 `b.o` → 停。`junk.o` 从不进。
    真实库里几乎总有那么一两个成员带无关的 libc 依赖, 所以这条差别就是"能用/不能用"。
    """
    clang = _clang()
    if not clang or not _wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    # 打包器用 **llvm-ar** (与 clang 同一套, Windows 上就有) —— 不用 `ar`, 免得判据
    # 在 Windows 上静默 SKIP (第一版就是那么写的, 结果它在最该跑的那台机器上没跑)。
    ar = shutil.which("llvm-ar") or _llvm_bin("llvm-ar.exe") or shutil.which("ar")
    if not ar:
        print("      SKIP: 没有 llvm-ar/ar")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        (td / "a.c").write_text(
            "extern int c_sub(int, int);\n"
            "__attribute__((noinline)) int c_add(int a, int b) { return a + b + c_sub(20, 5); }\n",
            encoding="utf-8", newline="\n")
        (td / "b.c").write_text("int c_sub(int a, int b) { return a - b; }\n",
                                encoding="utf-8", newline="\n")
        (td / "junk.c").write_text(
            "extern int printf(const char *, ...);\n"
            "int unused_junk(void) { return printf(\"never called\"); }\n",
            encoding="utf-8", newline="\n")
        for stem in ("a", "b", "junk"):
            assert compile_c(clang, td / f"{stem}.c", td / f"{stem}.o") == 0, "C 编译失败"
        lib = td / "libdemo.a"
        r = subprocess.run([ar, "rcs", str(lib), str(td / "a.o"), str(td / "b.o"),
                            str(td / "junk.o")], capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"ar 失败: {r.stderr[-200:]}"
        # 先钉住"固定点只挑该挑的" —— 不比这条, 整包收也能跑出 22, 判据就没意义了
        picked = {o.name for o in lomelf.Archive(lib).select({"c_add"})}
        assert picked == {"a.o/", "b.o/"}, f"归档挑错了成员: {picked}"
        (td / "m.lomt").write_text(
            "module arch\n\n"
            "extern fn c_add(a: i32, b: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        rc, err = build_and_run(td, td / "m.lomt", [lib], 22)
        assert rc == 22, f"归档链接结果不对: rc={rc} (期望 22) err={err[-300:]!r}"
        print("      归档 .a: 固定点只挑 a.o+b.o, 含 printf 的 junk.o 没被拖进来 -> 22")


@test
def test_object_with_relocations_is_rejected():
    """**仍然硬的边界**: 不认识的重定位类型 -> 硬拒。

    上面两条把重定位从"一律拒"放开到了"认识的那几种", 但**不认识的一律拒**这条没变 ——
    猜一个偏移的后果是"跳到错地址", 那是运行期崩溃而不是编译期报错。

    另外仍然拒的还有: 重定位指向**非代码节** (`.data`/`.rodata`) —— 本档不摆那些节。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 需要 clang")
        return
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        # 造一个假的重定位类型: 改掉 .rela.text 里 r_info 的低 32 位
        (td / "c.c").write_text(
            "extern int c_sub(int, int);\n"
            "__attribute__((noinline)) int c_add(int a, int b) { return a + b + c_sub(20, 5); }\n",
            encoding="utf-8", newline="\n")
        assert compile_c(clang, td / "c.c", td / "c.o") == 0
        raw = bytearray((td / "c.o").read_bytes())
        fo = lomelf.ForeignObject(td / "c.o")
        assert fo.relocs, "夹具没生成重定位"
        # 找 .rela.text 并把它第一条的 type 改成 0x7f (不认识的)
        import struct as _s
        e_shoff, = _s.unpack_from("<Q", raw, 40)
        sent, shnum = _s.unpack_from("<HH", raw, 58)
        for k in range(shnum):
            o = e_shoff + k * sent
            typ, = _s.unpack_from("<I", raw, o + 4)
            if typ != 4:
                continue
            off, = _s.unpack_from("<Q", raw, o + 24)
            _s.pack_into("<I", raw, off + 8, 0x7F)      # r_info 低 32 位 = 类型
            break
        (td / "bad.o").write_bytes(bytes(raw))
        try:
            lomelf.ForeignObject(td / "bad.o")
        except lomelf.Unsupported as e:
            assert "重定位" in str(e), e
        else:
            raise AssertionError("不认识的重定位类型应当被拒")
        print("      不认识的重定位类型: 硬拒")


@test
def test_object_with_undefined_symbol_is_rejected():
    """一个符号**到链接结束都没人提供** (典型是 libc) -> **硬拒**, 不猜。

    **判定点从"读对象时"挪到了"链接结束时"**（阶段 2）：未定义符号本身**不再是错误** ——
    它可能由**另一个对象**提供, 那正是多目标文件 C 库的形状。所以这条给一个没人提供的符号,
    断言它在**链接期**被拒。留住这条是为了钉住边界仍然硬: 拒掉, 而不是猜一个地址。
    """
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
        (td / "m.lomt").write_text(
            "module undef\n\n"
            "extern fn c_wrap(x: i32) -> i32;\n\n"
            "fn _start() {\n"
            "    syscall4(60, c_wrap(1 as i32) as u64, 0, 0);\n"
            "}\n", encoding="utf-8", newline="\n")
        mod = lomentc.load(td / "m.lomt")
        deps = lomentc.resolve_deps(mod, ROOT, td, entry=td / "m.lomt")
        assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        try:
            lomelf.compile_ll(ir, [lomelf.ForeignObject(td / "c.o")])
        except lomelf.Unsupported as e:
            assert "未定义" in str(e), e
        else:
            raise AssertionError("引用没人提供的符号应当被拒")
        print("      没人提供的符号: 链接期硬拒 (第 2 阶段仍不链 libc)")


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
