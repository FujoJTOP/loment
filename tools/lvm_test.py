#!/usr/bin/env python3
"""lvm_test.py — LVM 的 T0 判据（`docs/243` §4）。

**判 A：产物等价。** 同一份程序，`lomelf` 编出来的**原生产物**与 VM 各跑一遍，
比 **stdout 的字节 + 退出码**。不看时间、不看中间表示 —— 与 `loment_elf_test.py`
同一把尺子（那一条比的是 clang 路与 lomelf 路）。

**判 B：支持面相同。** 语料里每一份若 VM 说"不支持"，则 `lomelf` 也必须发不出来 ——
用一张**棘轮**表钉，只许变短。

**判 F：命令单元看到的是自己。** 读 `/proc/self/cmdline` 的那两份，走**真前门**跑，
断言它们看到的名字是**自己被指到的那条路径**（`docs/243` §3.4）—— 不是解释器的。

用法: python tools/lvm_test.py
"""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf  # noqa: E402
import lvm  # noqa: E402
import lomentc  # noqa: E402
import loment_elf_test as ELF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: ⚠ **语料复用 `loment_elf_test.CORPUS`，不另抄一份** —— 抄了必然漂
#: （仓里对"同一份清单抄第二遍"的判词很硬）。那一批的入选条件正是本格要的：
#: 有 `_start`、会终止、原生后端支持。

#: **棘轮**：VM 跑不了、而原生产物跑得了的那些。**只许变短** ——
#: 每条都要写清"为什么现在跑不了"，哪天跑通了这条判据会红，逼着把条目删掉。
VM_KNOWN_GAPS: dict[str, str] = {
    # `cmd_hello` / `register_hello`（257 openat）**已经收进来了**（2026-10-15，issue #330）：
    # VM **合成**一份"被解释程序自己的"命令行 —— 见下面判 F 与 `docs/243` §3.4.1。
    "selfcheck": (
        "要系统调用 **317** —— 而 317 在 Linux 上是 **`seccomp`，不是 `getrandom`**（318 才是）。"
        "原生产物拿到 -1，于是 `selfcheck` 自己报 `FAIL getrandom` 并退 1；VM 表里没有 317，"
        "**点名拒**（退 2，stdout 为空）。两者**不是同一个形状** —— 一个是"
        "「程序自己失败」，一个是「VM 不支持」 —— 所以它留在棘轮里。"
        "⚠ 这一条曾被我按名字猜成 getrandom 并实现掉，是判据 A 把那个臆造纠出来的："
        "**VM「通过」了，而原生失败**。"),
}


def _run_native(elf: Path, td: Path, argv0: str, timeout: int = 20) -> tuple[int, bytes]:
    """跑原生产物。**Linux 上直接跑；Windows 上借 WSL**（交叉编出来的 ELF 本机跑不了）。

    ⚠ **不能只走 WSL**：门禁跑在 ubuntu 上、那儿没有 `wsl` —— 只用 WSL 的话这条判据
    在 CI 里会**整个 SKIP**，等于永远不跑（`loment_elf_test` 就是那样）。这条的意义
    全在"两条路真的都跑过、产物真的逐字节比过"。

    ⚠ `argv0` **不是可有可无的**（`docs/243` §3.4）。`cmd_hello` 那种命令单元读
    `/proc/self/cmdline` 是为了**看自己叫什么**，而两条路的进程是**两个不同的文件**
    （`prog` 与 VM），各自的名字当然不同。**把 argv[0] 钉成同一个**，比的才是
    "同一份命令行下的行为" —— 这正是判据该量的那个量。不钉的话，比的是两个文件名
    像不像，那是**另一个问题**，而且答案永远是"不像"。
    """
    if sys.platform != "win32":
        p = td / "prog"
        p.write_bytes(elf.read_bytes())
        p.chmod(0o755)
        # POSIX：`executable=` 加上 argv[0] 想写什么就写什么（这是 execve 的语义）。
        r = subprocess.run([argv0], executable=str(p), capture_output=True,
                           timeout=timeout, shell=False)
        return r.returncode, r.stdout
    binn = f"/tmp/lvm_native_{os.getpid()}.bin"
    outp = td / "prog.out"
    script = (f"rm -f {binn} && cp {ELF._wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd /tmp && timeout {timeout} "
              f"env A0={shlex.quote(argv0)} bash -c 'exec -a \"$A0\" {binn}' "
              f"> {ELF._wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, timeout=timeout + 120, shell=False)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


def test_vm_matches_native() -> None:
    """判 A：同名程序，两条路各跑一遍，比 stdout 字节 + 退出码。"""
    if sys.platform == "win32" and not ELF._wsl():
        print("      SKIP: Windows 上没有 WSL —— 交叉编出来的 ELF 本机跑不了")
        return
    ran, gap, out_of_reach = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for rel in ELF.CORPUS:
            src = ROOT / rel
            stem = src.stem
            mod = lomentc.load(src)
            deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
            errs = lomentc.check(mod, deps=deps)
            if errs:
                out_of_reach.append((stem, f"检查不过: {errs[0][:60]}"))
                continue
            ir = lomentc.emit_llvm(mod, ROOT, deps)
            # 原生产物：同一条 `lomelf` 后门 —— 编不出来就是"目标之外"，不是缺口。
            try:
                blob, _ = lomelf.compile_ll(ir, [])
            except (lomelf.ElfError, lomelf.Unsupported) as e:
                out_of_reach.append((stem, str(e)[:60]))
                continue
            elf = Path(td) / f"{stem}.elf"
            elf.write_bytes(blob)
            # 程序"叫什么" = 它在语料里的路径。**两边同一个字符串**（见 `_run_native`）。
            rc_native, out_native = _run_native(elf, Path(td), rel)
            buf = io.BytesIO()
            try:
                rc_vm = lvm.run_ir(ir, buf, None, [rel])
            except Exception as e:  # noqa: BLE001
                gap.append((stem, f"{type(e).__name__}: {e}"[:70]))
                continue
            out_vm = buf.getvalue()
            if (rc_native, out_native) != (rc_vm, out_vm):
                gap.append((stem, f"两条路产物不同: 原生 rc={rc_native} "
                                  f"{out_native[:40]!r} / VM rc={rc_vm} {out_vm[:40]!r}"))
                continue
            ran.append(stem)
    # **棘轮**（两个方向都判）：没登记的分歧 ⇒ 红；登记了却**跑通了** ⇒ 也红（逼着删条目）。
    unregistered = [f"{s}: {w}" for s, w in gap if s not in VM_KNOWN_GAPS]
    assert not unregistered, "这两条路对不上，而它们**没有登记**在 VM_KNOWN_GAPS 里：\n  " \
                             + "\n  ".join(unregistered)
    for stem, why in VM_KNOWN_GAPS.items():
        assert any(s == stem for s, _ in gap), (
            f"{stem}: 登记在 VM_KNOWN_GAPS 里，但它**已经对上了** —— 请删掉那条登记"
            f"（{why[:60]}…）")
    print(f"      两条路逐字节相同: {len(ran)}/{len(ran)}（{', '.join(ran)}）")
    if gap:
        print(f"      已知缺口（棘轮，只许变短）: {', '.join(s for s, _ in gap)}")
    if out_of_reach:
        print(f"      目标之外（参考实现自己也发不出来）: "
              f"{', '.join(s for s, _ in out_of_reach)}")
    assert ran, "一份都没跑成 —— 判据没测到东西"


def test_command_unit_sees_its_own_name() -> None:
    """判 F：`/proc/self/cmdline` 给的是**被解释程序自己的**命令行（issue #330）。

    判 A 是"给两份产物**同一个** argv[0]，行为还一样不一样"；这一条问的是另一件事：
    走**真前门**（`lvm <entry>`）时，VM 挑的那个名字是**什么**。

    这里量的是"VM 把**它被指到的那条路径**当作程序的名字" —— 与它自己的名字无关。
    证伪很直接：一个**透传**实现（把宿主进程的 cmdline 递过去）在这里打印的是
    `pyth`（跑 `lvm.py` 的是 Python），而不是 `lome`；`argc` 也会是 host 的 3 而不是 1。
    所以这两条断言都不是"复述实现"，它们各自钉着一半。
    """
    entry = "loment/examples/cmd_hello.lomt"
    with tempfile.TemporaryDirectory() as td, open(td + "/sink", "wb") as sink:
        real, sys.stdout = sys.stdout, io.TextIOWrapper(sink, encoding="utf-8")
        try:
            rc = lvm.main([entry, "--root", str(ROOT)])
        finally:
            sys.stdout.flush()
            sys.stdout = real
        with open(td + "/sink", "rb") as f:
            got = f.read()
    assert got == entry[:4].encode(), (
        f"命令单元打印出来的应当是**它自己**名字的头 4 字节 {entry[:4]!r}，得到 {got!r} —— "
        "如果是 interpreter 自己的名字，说明 `cmdline` 那一处是透传不是合成")
    assert rc == 1, f"`argc` 应当是 1（只有程序名一个字段），VM 报的退出码是 {rc}"
    print(f"      真前门：命令单元看到的是自己 {entry[:4]!r}，argc=1")


def test_no_start_is_refused() -> None:
    """反例：没有 `_start` 的单元必须**点名拒**，不是静默什么都不做。"""
    ir = "define i64 @f() {\nentry:\n  ret i64 0\n}\n"
    try:
        lvm.run_ir(ir, io.BytesIO())
    except lvm.LvmError as e:
        assert "_start" in str(e), e
        return
    raise AssertionError("没有 _start 却跑起来了")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nlvm_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
