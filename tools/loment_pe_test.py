#!/usr/bin/env python3
# loment_pe_test.py — 原生 PE 后端: Loment 程序在 Windows 上原生跑, 不经 WSL (docs/167)
#
# 判据:
#   1. 平台中性的语料的 PE 产物, 与 clang/Linux 参考路 **stdout 字节 + 退出码**一致;
#   2. 产物在本机**原生**跑 (判据 1 的参考路要 WSL, 而产物本身不需要) —— 这一条把
#      PATH 收成只剩 python, 证明整条"编译 + 运行"都不碰 clang 与 wsl;
#   3. 不支持的输入必须报错退出, 不许静默错编;
#   4. CLI: `--target pe --check` 不落盘 rc=0; 非法 `--target` rc=2。
#
# **语料只取平台中性的两个。** `bootprobe`(M76) 与 `selfcheck`(M77) 是 OS 探针 —— 一个打印
# uname 的 sysname, 一个断言 seccomp 沙箱里 getrandom 会失败。它们在 Windows 上**本就该不同**
# (shim 还没有 uname/getpid/seccomp), 拿它们当判据只会把平台差异误判成回归。
#
# 已知边界 (与 lomelf.py 头注同源): shim 只实现了 `write`(1) 与 `exit`(60), 其余 syscall 号
# 返回 -1。所以**还不能跑需要 argv 或文件 I/O 的程序** (openat/read/getdents64/newfstatat/brk
# 未做, Windows 也没有 procfs 可读)。
#
# 运行: python tools/loment_pe_test.py   (非 Windows 或无 clang/WSL 时 SKIP 相应用例)

from __future__ import annotations

import contextlib
import importlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOMELF = ROOT / "tools" / "lomelf.py"
PORTABLE = [
    "loment/examples/user_hello.lomt",
    "loment/examples/all_loment.lomt",
]
# 构建路径上的 Loment 工具 + 它们对应的 Python 参照物。这些是**真正要去 WSL 的**那一批 ——
# 它们要 argv、要开文件、要遍历目录，所以这条用例同时钉住 shim 的整个 syscall 面。
TOOLS = [
    ("lomstatus", "loment/tools/lomstatus.lomt", ["--check"], "loment_status"),
    ("lomrel", "loment/tools/lomrel.lomt", ["--check"], "loment_release"),
]
# 程序自己的规格输出（与 clang 路对过；这里是给"不碰 clang/WSL"那条用例用的定值）
GOLDEN = {
    "user_hello": (0, b"M67 RESULT: PASS loment-user\n"),
    "all_loment": (0, b"M78 sum=42 double=84 max=84 fib=55\nM78 RESULT: PASS all-loment\n"),
}
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _on_windows() -> bool:
    return sys.platform == "win32"


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


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


def _ir(src: Path, td: Path) -> Path:
    """参考实现发射 IR（PE 路与 clang 路喂同一份字节）。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / (src.stem + ".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    return ll


def _run_native(exe: Path, timeout: int = 20) -> tuple[int, bytes]:
    """在本机直接跑 PE（不经 WSL）。退出码与 stdout 分开拿，不混在一个流里。"""
    r = subprocess.run([str(exe)], capture_output=True, timeout=timeout, shell=False)
    return r.returncode, r.stdout


def _run_in_wsl(elf: Path, name: str, td: Path, timeout: int = 20) -> tuple[int, bytes]:
    binn = f"/tmp/lompe_{name}.bin"
    outp = td / f"{name}.out"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"timeout {timeout} {binn} > {_wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, timeout=timeout + 120, shell=False)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


def _sections(blob: bytes) -> tuple[dict, list[tuple]]:
    """解出 PE 的关键头部与节表。字段偏移按 PE32+ 的规格（这里是判据，故意不复用 lomelf）。"""
    assert blob[:2] == b"MZ", "缺 MZ"
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    assert blob[pe:pe + 4] == b"PE\x00\x00", "缺 PE 签名"
    nsec = struct.unpack_from("<H", blob, pe + 6)[0]
    opt = pe + 24
    machine = struct.unpack_from("<H", blob, pe + 4)[0]
    size_img = struct.unpack_from("<I", blob, opt + 56)[0]
    size_hdr = struct.unpack_from("<I", blob, opt + 60)[0]
    entry = struct.unpack_from("<I", blob, opt + 16)[0]
    sec_align, file_align = struct.unpack_from("<II", blob, opt + 32)
    head = {"machine": machine, "nsec": nsec, "size_image": size_img,
            "size_headers": size_hdr, "entry": entry,
            "sec_align": sec_align, "file_align": file_align}
    st = opt + struct.unpack_from("<H", blob, pe + 20)[0]
    secs = []
    for i in range(nsec):
        h = st + 40 * i
        name = blob[h:h + 8].rstrip(b"\0").decode()
        vs, va, rs, rp, ch = (struct.unpack_from("<I", blob, h + 8)[0],
                              struct.unpack_from("<I", blob, h + 12)[0],
                              struct.unpack_from("<I", blob, h + 16)[0],
                              struct.unpack_from("<I", blob, h + 20)[0],
                              struct.unpack_from("<I", blob, h + 36)[0])
        secs.append((name, vs, va, rs, rp, ch))
    return head, secs


# ---------------------------------------------------------------- 用例

@test
def test_pe_image_is_structurally_sane():
    """PE 头部与节表必须自洽 —— 这几条正是当初把加载器搞拒收的地方。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in PORTABLE:
            src = ROOT / rel
            blob, _info = lomelf.compile_pe(_ir(src, td).read_text(encoding="utf-8"))
            head, secs = _sections(blob)
            assert head["machine"] == 0x8664, f"{src.name}: 不是 x64"
            assert head["size_headers"] and head["size_headers"] % head["file_align"] == 0, \
                f"{src.name}: SizeOfHeaders 未按 FileAlignment 对齐 ({head['size_headers']})"
            assert head["size_image"] % head["sec_align"] == 0, f"{src.name}: SizeOfImage 未对齐"
            # 节区按 RVA 升序且互不重叠（早先 .text 固定 0x1000 / .data 固定 0x2000，
            # 文本一过一页两节就重叠）
            end = 0
            for name, vs, va, _rs, _rp, _ch in secs:
                assert va >= end, f"{src.name}: {name} 与前节重叠 (va={va:#x} < {end:#x})"
                end = va + max(vs, 1)
            assert end <= head["size_image"], f"{src.name}: SizeOfImage 盖不住最后一节"
            # 入口必须落在某个可执行节里
            hit = [s for s in secs if s[2] <= head["entry"] < s[2] + max(s[1], 1) and s[5] & 0x20000000]
            assert hit, f"{src.name}: 入口 {head['entry']:#x} 不在可执行节里"
            # 文件必须盖住所有节的原始数据，否则加载器读到 EOF 之外直接拒收
            need = max(rp + rs for _n, _v, _a, rs, rp, _c in secs)
            assert len(blob) >= need, f"{src.name}: 文件 {len(blob)} < 节数据需要 {need}"
    print("      PE 头部/节表自洽: 对齐、不重叠、入口可执行、文件盖得住节数据")


@test
def test_pe_runs_natively_matches_linux_behavior():
    """同一份 IR: PE 产物(本机原生) 与 clang/Linux 产物(WSL) 的 stdout 字节 + 退出码一致。"""
    clang = _clang()
    if not (_on_windows() and clang and _wsl()):
        print("      SKIP: 非 Windows 或无 clang/WSL")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in PORTABLE:
            src = ROOT / rel
            name = src.stem
            ll = _ir(src, td)
            ref = td / f"{name}.elf"
            r = subprocess.run([clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                                "-ffreestanding", "-static", "-fno-pie", "-fuse-ld=lld",
                                "-Wl,-e,_start", str(ll), "-o", str(ref)],
                               capture_output=True, text=True, shell=False)
            assert r.returncode == 0, f"clang 链接失败: {r.stderr[-300:]}"
            exe = td / f"{name}.exe"
            blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
            exe.write_bytes(blob)
            want = _run_in_wsl(ref, f"{name}_c", td)
            got = _run_native(exe)
            if want != got:
                raise AssertionError(
                    f"[{name}] 行为不一致: rc clang/wsl={want[0]} pe/native={got[0]}\n"
                    f"  linux ({len(want[1])}B): {want[1][:200]!r}\n"
                    f"  pe    ({len(got[1])}B): {got[1][:200]!r}")
            ok += 1
    print(f"      {ok} 个程序的 PE 产物与 clang/Linux 产物 stdout 字节 + 退出码一致")


@test
def test_pe_builds_and_runs_without_clang_or_wsl():
    """**去 WSL 的判据**: PATH 里只剩 python 也编得出、跑得起来。"""
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent)      # 没有 clang、没有 wsl
        for rel in PORTABLE:
            src = ROOT / rel
            name = src.stem
            ll = _ir(src, td)
            exe = td / f"{name}.exe"
            r = subprocess.run([sys.executable, str(LOMELF), str(ll),
                                "--target", "pe", "-o", str(exe)],
                               capture_output=True, text=True, shell=False, env=env)
            assert r.returncode == 0, f"[{name}] 编译失败: {r.stderr[-300:]}"
            assert exe.exists(), f"[{name}] 没落盘"
            assert _run_native(exe) == GOLDEN[name], f"[{name}] 输出与定值不符"
    print(f"      PATH 里没有 clang/wsl 也能编出并跑起来 ({len(PORTABLE)} 个程序)")


@test
def test_pe_runs_the_loment_toolchain_natively():
    """**去 WSL 的判据（构建路径）**：Loment 工具本身编成 PE 后在本机原生跑，
    stdout 字节 + 退出码与 Python 参照版一致 —— 也就是说这台机器上已经不需要
    "把 ELF 丢进 WSL" 才能跑构建工具。"""
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for name, rel, argv, pymod in TOOLS:
            ll = _ir(ROOT / rel, td)
            exe = td / f"{name}.exe"
            blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
            exe.write_bytes(blob)
            # 工具按相对路径解析输入，必须在仓库根跑
            got = subprocess.run([str(exe)] + argv, capture_output=True,
                                 cwd=str(ROOT), shell=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    rc = importlib.import_module(pymod).main(list(argv))
                except SystemExit as e:
                    rc = e.code
            want = buf.getvalue().encode("utf-8")
            assert (rc, want) == (got.returncode, got.stdout), (
                f"[{name}] 不一致: rc {rc} vs {got.returncode}\n"
                f"  python ({len(want)}B): {want[:160]!r}\n"
                f"  pe     ({len(got.stdout)}B): {got.stdout[:160]!r}")
            ok += 1
    print(f"      {ok} 个构建工具（argv + 目录遍历 + 文件 I/O）"
          f"在 Windows 上原生跑，与 Python 版逐字节相同")


@test
def test_pe_reports_unsupported_instead_of_miscompiling():
    """不支持的东西必须**报错退出**, 不许静默编出一个错的 PE。"""
    bad = [
        ("结构体动态下标 GEP",
         "define void @_start() {\nentry:\n  %p = alloca { i32, i32 }\n  %i = load i32, ptr %p\n"
         "  %q = getelementptr { i32, i32 }, ptr %p, i32 %i\n  ret void\n}\n"),
        ("间接调用",
         "define void @_start() {\nentry:\n  %f = alloca ptr\n  %g = load ptr, ptr %f\n"
         "  call void %g()\n  ret void\n}\n"),
        ("没有 _start",
         "define void @main() {\nentry:\n  ret void\n}\n"),
    ]
    for label, text in bad:
        try:
            lomelf.compile_pe(text)
        except lomelf.Unsupported as e:
            assert str(e), f"{label}: 报了 Unsupported 但没有消息"
            continue
        raise AssertionError(f"[{label}] 本该报 Unsupported, 却编过去了")
    print(f"      {len(bad)} 类不支持的输入都报了错")


@test
def test_pe_cli_check_and_usage():
    """CLI: `--target pe --check` 不落盘且 rc=0; 非法 `--target` rc=2。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ir(ROOT / PORTABLE[0], td)
        r = subprocess.run([sys.executable, str(LOMELF), str(ll), "--target", "pe", "--check"],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"--check rc={r.returncode}: {r.stderr[-200:]}"
        assert "[OK]" in r.stdout, r.stdout[-200:]
        assert not (td / "user_hello.exe").exists(), "--check 不该落盘"
        r2 = subprocess.run([sys.executable, str(LOMELF), str(ll), "--target", "macho"],
                            capture_output=True, text=True, shell=False)
        assert r2.returncode == 2, f"非法 --target 应当 rc=2, 得到 {r2.returncode}"
    print("      --target pe --check 不落盘 rc=0 · 非法 --target rc=2")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_pe_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
