#!/usr/bin/env python3
# loment_hosted_test.py — `--hosted` 的门面判据 (docs/222)
#
# 判的是一整条链, 不是一层: **一份 Loment 源, 经真的启动器编出来, 链上系统真实的
# `libz.so`, 跑出逐字节正确的结果**。这条一旦绿, "能调外部库"就是真的, 而不是
# "IR 里有个 declare"。
#
# 为什么必须比**运行结果**: 这条路上最容易错的三处都**不改 IR** ——
#   ① 启动器把 `.ll` 当成目标文件递给 gcc (实测: `collect2: ld returned 1`);
#   ② `-lz` 这种连写形式没被参数循环收下 (实测: `unknown option -lz`);
#   ③ `--hosted` 却按 sealed 链 (lomelf), 于是报"未定义的符号 compress" —— 方向全错。
# 三者都只有**跑起来看结果**才抓得到。
#
# 平台: 判据要一条链跑到底, 所以它自己选路 ——
#   * **本机是 Linux 且 PATH 上有懂 IR 的 clang** (CI 就是这种): 原生跑;
#   * **本机是 Windows** (开发机): IR 由主机的 `clang.exe` 交叉成 ELF 目标文件,
#     链接与运行在 WSL 里由 gcc 做 —— 与 `loment_ffi_test` 同一套舞步;
#   * 两样都没有: **SKIP 并指名缺什么**, 不静默绿。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_dist                                                 # noqa: E402
import lomentc                                                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLANG_CANDIDATES = (r"C:\Program Files\LLVM\bin\clang.exe", "clang")

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- 被验的那份 Loment

#: 一份**未改动的** Loment 源: `fn main` 是 hosted 的入口 (docs/222 §4.3)。
#: 它调系统 libz 的 `compress` / `uncompress`, 把 64 字节往返一遍。
#: 成功返回 0; 每一个非零都是一个**具体的**失败点, 不是笼统的"没过"。
HOSTED_SRC = """\
module hostz

extern fn compress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;
extern fn uncompress(dst: ptr, dstlen: ptr, src: ptr, srclen: u64) -> i32;

const NSRC: u32 = 64;

fn store32(p: ptr, off: u32, v: u32) {
    store8(p, off, v as u8);
    store8(p, off + 1, ((v / 256) % 256) as u8);
    store8(p, off + 2, ((v / 65536) % 256) as u8);
    store8(p, off + 3, ((v / 16777216) % 256) as u8);
}

fn load32(p: ptr, off: u32) -> u32 {
    return load8(p, off)
        + load8(p, off + 1) * 256
        + load8(p, off + 2) * 65536
        + load8(p, off + 3) * 16777216;
}

fn main() -> u32 {
    let src: ptr = alloc(256);
    let clen: ptr = alloc(16);
    let comp: ptr = alloc(1024);
    let olen: ptr = alloc(16);
    let out: ptr = alloc(256);
    let i: u32 = 0;
    while i < NSRC {
        store8(src, i, ((i % 8) + 65) as u8);
        i = i + 1;
    }
    store32(clen, 0, 1024);
    if compress(comp, clen, src, NSRC as u64) != 0 { return 10; }
    let ncomp: u32 = load32(clen, 0);
    store32(olen, 0, 256);
    if uncompress(out, olen, comp, ncomp as u64) != 0 { return 20; }
    if load32(olen, 0) != NSRC { return 30; }
    let j: u32 = 0;
    while j < NSRC {
        if load8(out, j) != load8(src, j) { return 40; }
        j = j + 1;
    }
    if ncomp >= NSRC { return 50; }
    return 0;
}
"""

#: **证伪用的那一份**: 只把最后一处比较写错 (比成 0)。若这条判据真的在测往返,
#: 它必须返回 40; 若返回 0, 说明这条判据测的是"能跑"而不是"算对"。
FALSIFY_SRC = HOSTED_SRC.replace("if load8(out, j) != load8(src, j) {",
                                 "if load8(out, j) != 0 {")


def _clang() -> str | None:
    for c in CLANG_CANDIDATES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _resolves_with_flags(cc: str) -> bool:
    """这台机器上的 `cc` 会不会**独立解决掉**一个外部符号 (真 libc) ——

    不是"它能不能读懂 IR"(那要用一份 IR 试), 而是这一条: 原生 clang 在 Linux 上
    默认链真 libc, 而 Windows 那份 `clang.exe` 交叉到 ELF 时需要 sysroot, 给不了。
    所以这一条决定"原生跑"还是"走 WSL 两段"。
    """
    if os.name == "nt":
        return False
    return True


def _wsl() -> bool:
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:                                               # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    """Windows 路径 -> WSL 里能用的 `/mnt/<盘>/…`。"""
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _write_sh(path: Path, text: str, executable: bool = False) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    if executable:
        os.chmod(path, 0o755)


# ---------------------------------------------------------------- 假包 (真启动器)

#: WSL 侧那个 `clang` 垫片。**为什么需要它**: 这台开发机的 WSL 里没有 clang,
#: 而真懂 IR 的是 Windows 那一份 `clang.exe` (WSL 经 interop 能调它)。
#: `clang.exe` 一步链完需要一个 Linux 链接器与 sysroot —— 那是 Windows 侧给不了的,
#: 所以拆成 `clang.exe -c`(交叉出 ELF 目标文件) + `gcc`(链接)。
#: **它测的是启动器怎么拼那条命令行**, 不是"clang 能不能一步链完" ——
#: CI 上的原生 clang 才是后者, 那时这份垫片不参与。
CLANG_SHIM = """\
#!/bin/sh
CLANG="/mnt/c/Program Files/LLVM/bin/clang.exe"
ir=; out=; objs=; libs=
while [ $# -gt 0 ]; do
    case "$1" in
        *.ll) ir=$1 ;;
        -o) out=$2; shift ;;
        -L*|-l*) libs="$libs $1" ;;
        *) objs="$objs $1" ;;
    esac
    shift
done
tmp=$(mktemp -d)
# `clang.exe` 是 Windows 进程, 看不见 WSL 的 `/tmp/...` —— 递过去之前要换写法。
irw=$(wslpath -w "$ir")
ow=$(wslpath -w "$tmp/a.o")
"$CLANG" --target=x86_64-unknown-linux-gnu -c "$irw" -o "$ow" || exit 1
gcc "$tmp/a.o" $objs $libs -o "$out" || exit 1
exit 0
"""


class Pkg:
    """一个**最小**的发行包布局, 里面跑的是 `loment_dist.LAUNCHER_SH` 那一份真启动器。

    驱动是个桩 (把预生成的 IR 抄到 stdout): 这一条判据要测的是**链接那一段**,
    编译器另有它的判据。桩把这件事说出来, 而不是假装它是个真驱动。
    """

    def __init__(self, td: Path, native: bool) -> None:
        self.td = td
        self.native = native
        self.bin = td / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        (td / "share" / "loment").mkdir(parents=True, exist_ok=True)
        _write_sh(self.bin / "loment", loment_dist._subst(loment_dist.LAUNCHER_SH),
                  executable=True)
        _write_sh(self.bin / "loment-lomelf",
                  '#!/bin/sh\necho "lomelf: a hosted build must not reach here" >&2\nexit 9\n',
                  executable=True)
        self.shim = td / "shim"
        if not native:
            self.shim.mkdir(exist_ok=True)
            _write_sh(self.shim / "clang", CLANG_SHIM, executable=True)
        #: 每一步的 IR 落在这里, 由 `emit()` 写、桩驱动读。
        self.ll = td / "prog.ll"
        # 桩驱动在**哪一侧**跑, 就用哪一侧的路径写法 —— WSL 里 `C:/...` 是打不开的
        # (实测: `cat: C:/Users/.../prog.ll: No such file or directory`)。
        stub_path = self.ll.as_posix() if native else _wsl_path(self.ll)
        _write_sh(self.bin / "loment-driver",
                  '#!/bin/sh\ncat "%s"\n' % stub_path,
                  executable=True)

    def emit(self, src: str) -> None:
        """用**参考编译器**把源编成 IR —— 与 `loment ir` 同一条路。"""
        d = self.td / "src"
        d.mkdir(exist_ok=True)
        f = d / "prog.lomt"
        _write_sh(f, src)
        mod = lomentc.load(f)
        errs = lomentc.check(mod)
        assert not errs, errs
        _write_sh(self.ll, lomentc.emit_llvm(mod, ROOT))

    def run(self, args: list[str], src_name: str = "src/prog.lomt") -> tuple[int, str]:
        """跑**真启动器**。返回 (退出码, 合并输出)。"""
        if self.native:
            paths = [str(self.bin)] + ([str(self.shim)] if self.shim.exists() else [])
            r = subprocess.run([str(self.bin / "loment"), *args], capture_output=True,
                               text=True, shell=False, encoding="utf-8",
                               errors="replace", timeout=600,
                               env={**os.environ, "PATH": os.pathsep.join(paths + [os.environ.get("PATH", "")])})
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        return self._run_wsl(args, src_name)

    def _run_wsl(self, args: list[str], src_name: str) -> tuple[int, str]:
        w = _wsl_path(self.td)
        q = " ".join("'%s'" % a.replace("'", "'\\''") for a in args)
        script = (
            f"cd '{w}' || exit 99\n"
            f"chmod +x bin/* shim/* 2>/dev/null\n"
            f"PATH='{w}/shim':$PATH ./bin/loment {q}\n"
            f"b=$?\n"
            f"if [ $b -ne 0 ]; then echo \"BUILD_RC=$b\"; exit 0; fi\n"
            f"./out.bin\n"
            f"echo \"RUN_RC=$?\"\n"
        )
        r = subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True,
                           text=True, shell=False, encoding="utf-8",
                           errors="replace", timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
        if "RUN_RC=" in out:
            return int(out.rsplit("RUN_RC=", 1)[1].split()[0]), out
        if "BUILD_RC=" in out:
            return int(out.rsplit("BUILD_RC=", 1)[1].split()[0]), out
        return r.returncode, out


def _pkg(td: Path, src: str) -> Pkg:
    native = _resolves_with_flags(_clang() or "cc")
    p = Pkg(td, native)
    p.emit(src)
    return p


# ---------------------------------------------------------------- 判据


@test
def test_hosted_links_a_real_library_and_returns_the_right_answer():
    """**那一条判据**: 一份 Loment 源调系统 libz, compress→uncompress 逐字节回原文。

    退出码 0 才算过; 非 0 的数字指出具体是哪一步 (10 压缩 / 20 解压 / 30 长度 /
    40 逐字节 / 50 没压小)。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)
        out = pkg.td / "out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted", "-lz", "-o",
                           str(out) if pkg.native else "./out.bin"])
        assert rc == 0, f"hosted 程序返回 {rc} (期望 0)\n{log[-1200:]}"
        print("      Loment -> libz compress/uncompress -> 逐字节回原文 (rc=0)")


@test
def test_the_criterion_can_go_red():
    """**证伪**: 只把最后一处比较写错, 必须返回 40。

    没有这一条, 上一条测的是"能跑"而不是"算对" —— 一个把结果丢掉的实现也会返回 0。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), FALSIFY_SRC)
        out = pkg.td / "out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted", "-lz", "-o",
                           str(out) if pkg.native else "./out.bin"])
        assert rc == 40, f"改造过的程序应该返回 40, 实际 {rc}\n{log[-1200:]}"
        print("      证伪: 比较写错 -> rc=40 (判据不是空跑)")


@test
def test_naming_a_library_without_hosted_is_refused():
    """`-l` / `-L` 是在声明"这份产物有对外端口"。sealed 档兑现不了这个声明, 要说出来。"""
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)
        rc, log = pkg.run(["build", "src/prog.lomt", "-lz", "-o",
                           str(pkg.td / "o.bin") if pkg.native else "./out.bin"])
        assert rc == 2, f"应该退 2 (用法错), 实际 {rc}\n{log[-600:]}"
        assert "--hosted" in log, log[-600:]
        print("      没有 --hosted 时 -lz 被点名拒 (退 2)")


@test
def test_hosted_needs_a_main_entry():
    """hosted 产物由 C 运行期调用 `main`; 拿一份只有 `_start` 的单元来, 必须点名拒。

    静默按 sealed 链的后果是"未定义的符号 compress" —— 把病因指到符号上, 方向全错。
    """
    with tempfile.TemporaryDirectory() as t:
        pkg = _pkg(Path(t), HOSTED_SRC)
        # 把 IR 里的入口改成 _start: 模拟一份 sealed 形状的单元
        _write_sh(pkg.ll, pkg.ll.read_text(encoding="utf-8").replace("@main(", "@_start("))
        out = str(pkg.td / "o.bin") if pkg.native else "./out.bin"
        rc, log = pkg.run(["build", "src/prog.lomt", "--hosted", "-lz", "-o", out])
        assert rc == 1, f"应该退 1, 实际 {rc}\n{log[-600:]}"
        assert "main" in log, log[-600:]
        print("      hosted 却只有 _start: 点名拒 (退 1)")


@test
def test_windows_launcher_refuses_hosted_by_name():
    """PE 侧今天没有 hosted 这条路 (docs/222 §7)。**点名拒**, 不许静默按 lomelf 链。

    这台机器上 Windows 启动器跑不了 hosted, 所以这一条只能静态判 —— 它判的是
    "那份批处理里有这一支, 而且它会退 2", 不是"PE 上能跑"。
    """
    body = loment_dist.LAUNCHER_CMD
    assert 'if /I "%~1"=="--hosted" goto barg_hosted' in body, "Windows 启动器没收下 --hosted"
    assert ":barg_hosted" in body, "Windows 启动器没有那一支"
    tail = body.split(":barg_hosted", 1)[1].split(":barg_done", 1)[0]
    assert "exit /b 2" in tail, "那一支没有拒绝 (退 2)"
    assert "ELF" in tail, "拒绝理由没说清是平台的事"
    print("      Windows 启动器对 --hosted 点名拒 (退 2, 理由说清是平台)")


@test
def test_the_shipped_usage_mentions_hosted():
    """两份启动器的用法文本都要提到 `--hosted` —— 否则它是个"藏在代码里"的开关。"""
    sh = loment_dist._subst(loment_dist.LAUNCHER_SH)
    assert "--hosted" in sh, "POSIX 启动器的用法没提 --hosted"
    assert "--hosted" in loment_dist.LAUNCHER_CMD, "Windows 启动器的用法没提 --hosted"
    print("      两份启动器的用法文本都提到 --hosted")


def main() -> int:
    if not _clang():
        print("loment_hosted_test: SKIP —— 这台机器上没有 clang, "
              "IR 编不成目标文件 (docs/222)")
        return 0
    if not _resolves_with_flags(_clang()) and not _wsl():
        print("loment_hosted_test: SKIP —— 本机是 Windows 且没有 WSL: "
              "ELF 产物跑不起来 (docs/222)")
        return 0
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
        print(f"loment_hosted_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
        return 1
    print(f"loment_hosted_test: {len(TESTS)}/{len(TESTS)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
