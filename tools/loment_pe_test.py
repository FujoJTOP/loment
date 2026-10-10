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
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf   # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

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
# 能力域探针（见 test_pe_links_capability_programs）: 域内两个槽位相加, 顺带过一次 syscall。
CAP_DEMO = """module capdemo

capability blk_write : disk[0..4] revocable

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);
    return slot;
}

fn _start() {
    let a: u32 = write_slot(3);
    let b: u32 = write_slot(1);
    if a + b == 4 {
        write_str(1, "CAP OK\\n");
        syscall4(60, 0, 0, 0);
    }
    write_str(1, "CAP BAD\\n");
    syscall4(60, 1, 0, 0);
}
"""
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
    binn = f"{_T}lompe_{name}.bin"
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
def test_pe_links_capability_programs():
    """能力域程序必须能走原生后端 —— 它的域描述表是**结构体全局初值**。

    回归护栏：`capability` 会发一张 `[N x { i64, i64, i64, i64 }]` 的表, 而 `_parse_init`
    原先只认标量/字节串/数组, 于是**任何带 capability 的程序**在链接期直接
    `v0 不支持的全局初值` 失败 —— 连仓库自己的 `loment/examples/native_cap.lomt` 都编不出来。
    这个用例把"能编 + 能跑 + 越界真的 trap"三件事一起钉住。
    """
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "capdemo.lomt"
        src.write_text(CAP_DEMO, encoding="utf-8", newline="\n")
        ll = _ir(src, td)
        assert "[1 x { i64, i64, i64, i64 }]" in ll.read_text(encoding="utf-8"), \
            "域描述表形态变了 —— 这个用例盯的就是它的初值解析"
        exe = td / "capdemo.exe"
        blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
        exe.write_bytes(blob)
        assert _run_native(exe) == (0, b"CAP OK\n"), "域内 guard 应当放行"
        # 非字面量越界 -> 运行期 trap (IR 后端是 ud2, 退出码 132/SIGILL)
        trap = td / "captrap.lomt"
        trap.write_text(CAP_DEMO.replace("write_slot(1)", "write_slot(9)"),
                        encoding="utf-8", newline="\n")
        tll = _ir(trap, td)
        texe = td / "captrap.exe"
        texe.write_bytes(lomelf.compile_pe(tll.read_text(encoding="utf-8"))[0])
        assert _run_native(texe)[0] != 0, "运行期越界应当 trap, 不该正常退出"
        # 字面量越界 -> 编译期就拒 (参考实现的 check)
        lit = td / "caplit.lomt"
        lit.write_text(CAP_DEMO.replace("guard blk_write(slot);", "guard blk_write(9);"),
                       encoding="utf-8", newline="\n")
        mod = lomentc.load(lit)
        deps = lomentc.resolve_deps(mod, ROOT, lit.parent, entry=lit)
        assert any("越界" in e for e in lomentc.check(mod, deps=deps)), \
            "字面量越界必须在检查期报错"
    print("      能力域程序: 域内放行 / 越界 trap / 字面量越界拒绝, 且走的是原生 PE")


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
def test_pe_runs_the_selfhost_compiler_natively():
    """**去 WSL 的判据（编译器侧）**：自举种子编成 PE 后，在 Windows 上**原生当编译器用** ——
    对语料产出的 IR 与参考实现逐字节相同；那份 IR 再喂回 `lomelf --target pe`，
    产物跑出来的行为也对得上。整条链在这台机器上不碰 clang、不碰 WSL。"""
    if not _on_windows():
        print("      SKIP: 非 Windows")
        return
    seed = ROOT / "loment/build/selfhost_driver.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        drv = td / "driver.exe"
        blob, _info = lomelf.compile_pe(seed.read_text(encoding="utf-8"))
        drv.write_bytes(blob)
        ok = 0
        for rel in PORTABLE:
            src = ROOT / rel
            ref = _ir(src, td)                          # 参考实现发的 IR
            r = subprocess.run([str(drv), rel], capture_output=True,
                               cwd=str(ROOT), shell=False)   # 入口按仓库相对路径给
            assert r.returncode == 0, f"[{src.stem}] 编译器 rc={r.returncode}: {r.stderr[-200:]!r}"
            assert r.stdout == ref.read_bytes(), f"[{src.stem}] IR 与参考不一致"
            # 全链：那份 IR -> PE -> 跑，行为与定值一致
            prog = td / f"{src.stem}.exe"
            prog.write_bytes(lomelf.compile_pe(r.stdout.decode("utf-8"))[0])
            assert _run_native(prog) == GOLDEN[src.stem], f"[{src.stem}] 全链产物行为不符"
            ok += 1
    print(f"      {ok} 个语料: 自举种子编成的 PE 原生当编译器用, IR 逐字节相同且全链可跑")


@test
def test_pe_selfhost_mirror_matches_reference():
    """**去 WSL 的最后一道坎**：自举侧的镜像 `loment/tools/lomelf.lomt` 也能出 PE，
    且与参考实现的 PE **逐字节相同**。也就是说 Windows 包里那个"把 .ll 变成 .exe"的
    链接器可以是自举产物，不必是 Python 也不必是 clang。

    镜像本体是 Linux ELF（走种子自举链编出来），所以它在 WSL 里跑 —— 但它**产出的**是 PE。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        script = (f"cp {_wsl_path(s1)} {_T}lompe_s1.bin && chmod +x {_T}lompe_s1.bin && "
                  f"cd {_wsl_path(ROOT)} && {_T}lompe_s1.bin loment/tools/lomelf.lomt")
        rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                            capture_output=True, timeout=900, shell=False)
        assert rr.returncode == 0, f"stage1 编镜像失败: {rr.stderr[-300:]}"
        mir_ll = td / "lomelf.ll"
        mir_ll.write_bytes(rr.stdout)
        mir = td / "lomelf.bin"
        r2 = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(mir_ll), "-o", str(mir)],
            capture_output=True, text=True, shell=False)
        assert r2.returncode == 0, f"镜像链接失败: {r2.stderr[-300:]}"
        ok = 0
        for rel in PORTABLE:
            src = ROOT / rel
            ll = _ir(src, td)
            got = td / f"{src.stem}.mir.exe"
            script = (f"cd {_wsl_path(ROOT)} && {_wsl_path(mir)} "
                      f"{_wsl_path(ll)} {_wsl_path(got)}")
            r3 = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                                capture_output=True, timeout=300, shell=False)
            assert r3.returncode == 0, f"镜像出 PE 失败: {r3.stderr[-300:]!r}"
            blob, _info = lomelf.compile_pe(ll.read_text(encoding="utf-8"))
            assert got.read_bytes() == blob, f"[{src.stem}] 镜像的 PE 与参考不一致"
            ok += 1
    print(f"      {ok} 个语料: 自举镜像产出的 PE 与参考逐字节相同（去 WSL 的最后一道坎）")


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


#: 联网语料：**一份源、两个平台**。它自己起监听（端口填 0 ⇒ 由内核挑临时端口）、
#: 自己连自己，一次把 shim 的联网面走完：socket / setsockopt(SO_REUSEADDR) / bind /
#: listen / getsockname / connect / accept / read / write（socket 型 fd 上分流到 recv/send）/
#: setsockopt(SO_RCVTIMEO)（timeval → 毫秒） / sendto(addr=NULL, MSG_NOSIGNAL)。
#: 三行输出各钉一件事：回显对得上 / 读超时返回 -EAGAIN / 对端挂断之后进程还活着。
#: 走 127.0.0.1 + 临时端口，所以并发跑门禁也不会撞端口。
SOCK_DEMO = """module sockdemo

fn w(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn die(code: u64, s: str) -> u32 {
    w(1, s);
    syscall4(60, code, 0, 0);
    return 0;
}

fn sa_init(sa: ptr, port_hi: u8, port_lo: u8) -> u32 {
    store8(sa, 0, 2);
    store8(sa, 1, 0);
    store8(sa, 2, port_hi);
    store8(sa, 3, port_lo);
    store8(sa, 4, 127);
    store8(sa, 5, 0);
    store8(sa, 6, 0);
    store8(sa, 7, 1);
    return 0;
}

fn _start() {
    let sa: ptr = alloc(64);
    let _z: u32 = sa_init(sa, 0, 0);
    let ls: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if ls < 0 {
        die(1, "FAIL socket(listener)\\n");
    }
    let one: ptr = alloc(8);
    store8(one, 0, 1);
    let so: i64 = syscall6(54, ls as u64, 1, 2, one as u64, 4);
    if so < 0 {
        die(2, "FAIL setsockopt(SO_REUSEADDR)\\n");
    }
    let bi: i64 = syscall6(49, ls as u64, sa as u64, 16, 0, 0);
    if bi < 0 {
        die(3, "FAIL bind\\n");
    }
    let li: i64 = syscall6(50, ls as u64, 8, 0, 0, 0);
    if li < 0 {
        die(4, "FAIL listen\\n");
    }
    let ln: ptr = alloc(8);
    store8(ln, 0, 16);
    let gn: i64 = syscall6(51, ls as u64, sa as u64, ln as u64, 0, 0);
    if gn < 0 {
        die(5, "FAIL getsockname\\n");
    }
    if load8(sa, 3) == 0 {
        die(6, "FAIL ephemeral port is 0\\n");
    }
    let cs: i64 = syscall6(41, 2, 1, 0, 0, 0);
    if cs < 0 {
        die(7, "FAIL socket(client)\\n");
    }
    let cn: i64 = syscall6(42, cs as u64, sa as u64, 16, 0, 0);
    if cn < 0 {
        die(8, "FAIL connect\\n");
    }
    let ac: i64 = syscall6(43, ls as u64, 0, 0, 0, 0);
    if ac < 0 {
        die(9, "FAIL accept\\n");
    }
    let pw: i64 = syscall6(1, cs as u64, str_ptr("ping") as u64, 4, 0, 0);
    if pw != 4 {
        die(10, "FAIL client write\\n");
    }
    let rb: ptr = alloc(64);
    let r1: i64 = syscall6(0, ac as u64, rb as u64, 4, 0, 0);
    if r1 != 4 {
        die(11, "FAIL server read\\n");
    }
    if load8(rb, 0) != 112 {
        die(12, "FAIL echo byte\\n");
    }
    let pw2: i64 = syscall6(1, ac as u64, str_ptr("pong") as u64, 4, 0, 0);
    if pw2 != 4 {
        die(13, "FAIL server write\\n");
    }
    let r2: i64 = syscall6(0, cs as u64, rb as u64, 4, 0, 0);
    if r2 != 4 {
        die(14, "FAIL client read\\n");
    }
    if load8(rb, 1) != 111 {
        die(15, "FAIL echo byte 2\\n");
    }
    w(1, "ECHO OK\\n");

    let tv: ptr = alloc(16);
    store8(tv, 8, 224);
    store8(tv, 9, 147);
    store8(tv, 10, 4);
    let st1: i64 = syscall6(54, ac as u64, 1, 20, tv as u64, 16);
    if st1 < 0 {
        die(16, "FAIL setsockopt(SO_RCVTIMEO)\\n");
    }
    let r3: i64 = syscall6(0, ac as u64, rb as u64, 4, 0, 0);
    if r3 != -11 {
        die(17, "FAIL read-timeout is not -EAGAIN\\n");
    }
    w(1, "TIMEOUT OK\\n");

    let extra: i64 = syscall6(1, cs as u64, str_ptr("xxxx") as u64, 4, 0, 0);
    if extra != 4 {
        die(18, "FAIL client write 2\\n");
    }
    syscall6(3, cs as u64, 0, 0, 0, 0);
    let big: ptr = alloc(2048);
    let _x1: i64 = syscall6(44, ac as u64, big as u64, 2048, 16384, 0);
    let _x2: i64 = syscall6(44, ac as u64, big as u64, 2048, 16384, 0);
    w(1, "SURVIVED\\n");
    syscall6(3, ac as u64, 0, 0, 0, 0);
    syscall6(3, ls as u64, 0, 0, 0, 0);
    syscall4(60, 0, 0, 0);
}
"""

SOCK_GOLDEN = (0, b"ECHO OK\nTIMEOUT OK\nSURVIVED\n")


def _shim_blob():
    """现算一遍 shim 机器码 + 导入表（`--dump-win-shim` 冻的就是这两段）。"""
    a = lomelf.Asm(0)
    em = lomelf.PeEmitter([])
    em.asm = a
    idata, slots = lomelf.build_pe_idata()
    lomelf.emit_win_shim(em, slots)
    return a.finalize(), idata, slots, a.labels


@test
def test_pe_shim_dispatches_the_documented_numbers():
    """派发面 == `PE_DISPATCH` 那张登记表：注释、文档、机器码三者不能各自漂。

    跨平台（纯数据），所以**门禁的 ubuntu runner 也跑得到** —— 它盯的是
    "文档说支持哪些号"这件事本身，而不是那些号在 Windows 上跑得对不对。
    """
    blob, _idata, _slots, labels = _shim_blob()
    i = labels["__win_syscall"]
    while not (blob[i] == 0x3D and blob[i + 5] == 0x0F and 0x80 <= blob[i + 6] <= 0x8F):
        i += 1                                   # 跳过序言（push rbp / mov rbx,…）
    seen = []
    while blob[i] == 0x3D and blob[i + 5] == 0x0F and 0x80 <= blob[i + 6] <= 0x8F:
        seen.append(struct.unpack_from("<i", blob, i + 1)[0])
        i += 11                                  # cmp eax,imm32 (5) + jcc rel32 (6)
    assert seen == lomelf.PE_DISPATCH, f"派发面与登记表不符:\n  {seen}\n  {lomelf.PE_DISPATCH}"
    assert blob[i:i + 7] == b"\x48\xC7\xC0\xFF\xFF\xFF\xFF", "兜底不是 `mov rax,-1`"
    print(f"      {len(seen)} 个号逐个对上 `PE_DISPATCH`，兜底 -1")


@test
def test_pe_imports_two_dlls_with_the_socket_surface():
    """导入表：两条描述符 + 终止项、两个 DLL 名、整个 ws2_32 面，且塞得进孪生那块落点。"""
    idata, slots = lomelf.build_pe_idata()
    d = lomelf.PE_IDATA_RVA
    got, k = [], 0
    while True:
        ilt, _ts, _fc, name_rva, iat = struct.unpack_from("<IIIII", idata, k * 20)
        if ilt == 0 and name_rva == 0:
            break                                # 全零终止项
        got.append(idata[name_rva - d:idata.index(b"\x00", name_rva - d)].decode())
        k += 1
    assert got == [n for n, _f in lomelf.PE_DLLS], f"描述符指向的 DLL 不对: {got}"
    for nm in lomelf.PE_IMPORTS_WS2:
        assert nm in slots, f"ws2_32 少了 `{nm}`"
    assert len(slots) == len(lomelf.PE_IMPORTS) + len(lomelf.PE_IMPORTS_WS2), \
        "函数名有重名（`slots` 是按名字索引的平表，重名会静默覆盖）"
    # Import Directory 的 size 必须跟着描述符条数走（原先写死 40 = 一条 + 终止项）
    assert lomelf.PE_IMPORT_DESC_BYTES == 20 * (len(lomelf.PE_DLLS) + 1)
    tiny = "define void @_start() {\nentry:\n  ret void\n}\n"
    blob, _info = lomelf.compile_pe(tiny)
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    opt = pe + 24
    dir_rva, dir_sz = struct.unpack_from("<II", blob, opt + 112 + 8)
    assert (dir_rva, dir_sz) == (lomelf.PE_IDATA_RVA, lomelf.PE_IMPORT_DESC_BYTES), \
        f"可选头里的 Import Directory = {hex(dir_rva)}/{dir_sz}，与描述符条数不符"
    # 孪生侧给导入表留的落点只有 TB_EXT-TB_IDATA 宽，**撑破是静默截断**
    twin = (ROOT / "loment" / "tools" / "lomelf.lomt").read_text(encoding="utf-8")
    tb = int(re.search(r"const TB_IDATA: u32 = (\d+);", twin).group(1))
    ext = int(re.search(r"const TB_EXT: u32 = (\d+);", twin).group(1))
    assert len(idata) <= ext - tb, f"导入表 {len(idata)} B 撑破了孪生落点的 {ext - tb} B"
    print(f"      {len(got)} 个 DLL、{len(slots)} 个导入；导入表 {len(idata)} B ≤ 孪生落点 {ext - tb} B")


@test
def test_frozen_shim_blob_matches_the_reference():
    """树里冻的那份（`win_shim_data.lomt`）== 参考实现现算的那份 —— 孪生照抄的就是它。

    这条盯的是"改了 shim 却忘了重新冻"：没有它，自举链接器会照抄一份**旧的**机器码，
    而参考实现编出的 PE 是新的 —— 两边在 `test_pe_selfhost_mirror_matches_reference`
    之前都不会有人喊。
    """
    src = (ROOT / "loment" / "tools" / "win_shim_data.lomt").read_text(encoding="utf-8")
    blob, idata, _slots, _labels = _shim_blob()

    def decode(block: str, n: int) -> bytes:
        got = {int(a): b for a, b in
               re.findall(r'if i == (\d+) \{ return "([0-9a-f]*)"; \}', block)}
        assert sorted(got) == list(range(n)), f"分片编号不连续: {sorted(got)[:4]}…"
        return bytes.fromhex("".join(got[i] for i in range(n)))

    n_shim = int(re.search(r"pub fn n_shim\(\) -> u32 \{ return (\d+); \}", src).group(1))
    n_idata = int(re.search(r"pub fn n_idata\(\) -> u32 \{ return (\d+); \}", src).group(1))
    sblock = re.search(r"pub fn shim_part.*?(?=pub fn n_idata)", src, re.S).group(0)
    iblock = re.search(r"pub fn idata_part.*", src, re.S).group(0)
    assert decode(sblock, n_shim) == blob, "shim 机器码与冻结的那份不符 —— 跑 `--dump-win-shim`"
    assert decode(iblock, n_idata) == idata, "导入表与冻结的那份不符 —— 跑 `--dump-win-shim`"
    print(f"      冻结的 shim {len(blob)} B / 导入表 {len(idata)} B 与现算的逐字节相同")


@test
def test_elf_socket_program_runs_on_linux():
    """Linux 上**真跑**一遍联网语料 —— CI 的 ubuntu runner 跑得到这一条。

    PE 那一侧要靠 Windows 机器（见下一条），所以这条是门禁里**唯一**会真的收发字节的联网判据。
    """
    if not sys.platform.startswith("linux"):
        print("      SKIP: 非 Linux（PE 那半由 test_pe_and_elf_agree_on_a_socket_program 钉）")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "sockdemo.lomt"
        src.write_text(SOCK_DEMO, encoding="utf-8", newline="\n")
        ll = _ir(src, td)
        elf = td / "sockdemo.elf"
        elf.write_bytes(lomelf.compile_ll(ll.read_text(encoding="utf-8"))[0])
        elf.chmod(0o755)
        r = subprocess.run([str(elf)], capture_output=True, timeout=60, shell=False)
        assert (r.returncode, r.stdout) == SOCK_GOLDEN, \
            f"Linux 上联网语料没跑对: rc={r.returncode} out={r.stdout!r} err={r.stderr[:200]!r}"
    print("      回显 / 读超时 -EAGAIN / 对端挂断存活：Linux 原生真跑通过")


@test
def test_pe_and_elf_agree_on_a_socket_program():
    """同一份 `.lomt`：PE（本机原生）与 ELF（WSL）**输出逐字节相同、退出码相同**。

    这就是"孪生联网"的正题 —— 不是"两个平台各自能跑"，而是**同一份源在两边行为一致**。
    """
    if not (_on_windows() and _wsl()):
        print("      SKIP: 非 Windows 或无 WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        src = td / "sockdemo.lomt"
        src.write_text(SOCK_DEMO, encoding="utf-8", newline="\n")
        ll = _ir(src, td)
        text = ll.read_text(encoding="utf-8")
        exe = td / "sockdemo.exe"
        exe.write_bytes(lomelf.compile_pe(text)[0])
        elf = td / "sockdemo.elf"
        elf.write_bytes(lomelf.compile_ll(text)[0])
        got = _run_native(exe, timeout=60)
        want = _run_in_wsl(elf, "sockdemo", td, timeout=60)
        assert got == want, f"两个平台不一致:\n  PE   {got!r}\n  ELF  {want!r}"
        assert got == SOCK_GOLDEN, f"联网语料没跑对: {got!r}"
    print(f"      回显 / 读超时 / 对端挂断：PE 与 ELF 同为 {SOCK_GOLDEN[1]!r}")


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
