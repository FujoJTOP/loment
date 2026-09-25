#!/usr/bin/env python3
# loment_elf_test.py — 原生 ELF 后端: 不吃 clang 也能产出可执行文件 (docs/167)
#
# 判据: 同一份 `.ll`, 两条路各产一个 ELF, **行为逐值一致** ——
#   (a) 参考路: clang --target=x86_64-unknown-linux-gnu -nostdlib -static -fno-pie -fuse-ld=lld
#   (b) 原生路: tools/lomelf.py (纯 Python, 不调 clang)
#   比 stdout 的**字节**与**退出码**。
#
# 语料只放**会终止**的 `_start` 程序。`loment/examples/native_entry.lomt` 是 `while true`
# 的裸机入口 (M32 验证集), 按设计不终止, 不进这个判据 —— 它属于"能不能编出来", 不属于
# "跑出来一样"。
#
# **例子集里每一份都要有去处** (docs/205 R6): `CORPUS` 里的是真跑的, `NOT_RUN` 里的是
# 跑不了的 —— 后者每一条都带一个**当场能核**的理由 (下面 `test_not_run_reasons_hold`
# 会真的去链一次、真的编一次), 不是一句注释。"漏掉一个"因此不是一种可能。
#
# 已知边界 (与 lomelf.py 头注同源, 逐条记账):
#   * 调用约定是我们自己的 (实参走栈), **不是 System V** —— 原生产物 v0 不给 C 调;
#   * 结构体动态下标 GEP / 间接调用 / 浮点 v0 不支持 (会报 [ERR] 而不是静默错编);
#   * 自举侧的镜像是 loment/tools/lomelf.lomt —— 自举那几条用例钉它与参考逐字节相同,
#     其中一条专门钉 FFI (`--link` 外部目标文件, docs/173)。
#
# 运行: python tools/loment_elf_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os
import re

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc           # noqa: E402
import lomelf            # noqa: E402
import loment_ffi_test as ffitest   # noqa: E402  (共用 FFI 那份 C 夹具与 Loment 源码)

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent

#: 真跑的一批: 有 `_start`、会终止、原生后端支持 —— 两条路各编一个, 比 stdout 与退出码。
CORPUS = [
    "loment/examples/user_hello.lomt",
    "loment/examples/bootprobe.lomt",
    "loment/examples/selfcheck.lomt",
    "loment/examples/all_loment.lomt",
    "loment/examples/tour.lomt",
    "loment/examples/native_match_full.lomt",
    "loment/examples/switch.lomt",
    "loment/examples/lumtui_demo.lomt",
]

#: 跑不了的一批, 键 = 文件名主干, 值 = (类别, 理由)。**类别决定判据怎么核这条理由** ——
#: `test_not_run_reasons_hold` 会对每一类**真做一遍**那件事, 断言它失败得跟你说的一样。
#: 所以这三段文字不是笔记, 是每次门禁都重新成立一次的事实。
_NO_ENTRY = (
    "no-entry",
    "没有 `_start` —— 它给别的判据当**库**用（入口由 C 驱动 / QEMU / 逐字节 IR 对照提供），"
    "不是能自己跑起来的程序",
)
_IR_GAP = (
    "ir-gap",
    "IR 后端缺口: `inb` 还没实现（M20 只在 Rust 那条路上）—— 挡住它的是**后端**, 不是入口",
)
_NONTERM = (
    "nonterm",
    "有 `_start` 但**设计上不终止**（`while true` 的裸机入口, M32 验证集）: 跑它只能等到超时, "
    "而超时不是一条判据",
)

NOT_RUN: dict[str, tuple[str, str]] = {
    # 22 份: 定义函数、由调用方给入口（p7 的 C 驱动 / p9 的交叉编译 / p8 的 IR 对照）。
    **{n: _NO_ENTRY for n in (
        "ahci", "allocator", "bytes", "demo", "fuc_node", "mathutil", "native",
        "native_agg", "native_bits", "native_brk", "native_cap", "native_chain",
        "native_concat", "native_gen", "native_gen_sig", "native_mem", "native_mut",
        "native_res", "native_slice", "native_str", "native_trait", "toolchain")},
    "native_raii": _IR_GAP,
    "native_entry": _NONTERM,
}

#: 自举镜像 (`loment/tools/lomelf.lomt`) **还链不了**的那几份 —— 键是 CORPUS 里的主干名,
#: 值 = (现象, 为什么)。镜像语料 = CORPUS 减去这张表。
#:
#: 这不是"放宽判据": `test_lomelf_selfhost_matches_reference` 会拿这条表**反向核**一遍 ——
#: 表里每一份都必须**仍然链不过**。哪天它链过了, 判据红, 逼你把它挪回语料。
#: 表只许变短。
MIRROR_GAP: dict[str, tuple[str, str]] = {
    "tour": ("SIGILL (rc=132)",
             "镜像在**模块级 `i64` 全局**上崩掉（元素类型 64 位 + 初值不是 zeroinitializer）——"
             "`@__loment_caps` 正是那个形状。参考实现链得过, 所以这是镜像的缺口, 不是 IR 的问题。"
             "见 **issue #102**"),
    "native_match_full": ("回填表满了 (rc=1)",
                          "镜像的回填表容量上限 (TB_FIX_MAX=32768)。它是**点名拒**的, "
                          "参考实现没有这个上限 —— 属容量, 不属缺陷"),
}

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


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


def _ref_ir(src: Path, td: Path) -> Path:
    """参考实现发射 IR (与 clang 路、原生路喂同一份字节)。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / (src.stem + ".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    return ll


def _link_clang(clang: str, ll: Path, out: Path) -> None:
    r = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(ll), "-o", str(out)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"clang 链接失败: {r.stderr[-300:]}"


def _run_bin(elf: Path, name: str, td: Path, timeout: int = 20) -> tuple[int, bytes]:
    """在 WSL 里跑 ELF, 返回 (退出码, stdout 字节)。

    stdout 落文件、退出码单独 echo —— 不能把两者混在一个流里再切尾巴 (那会把程序自己的
    输出当成退出码, 判据会假绿)。
    """
    binn = f"{_T}lomelf_{name}.bin"
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


# ---------------------------------------------------------------- 自举镜像 (只建一次)

_MIRROR: Path | None = None
_MIRROR_TD: tempfile.TemporaryDirectory | None = None


def _mirror() -> Path:
    """`loment/tools/lomelf.lomt` 的产物 —— **能替用户干活的那份**链接器。

    种子 ->(clang 一次)-> stage1 ->(自举, 无 Python 无 clang)-> 镜像。clang 只在第一步
    出现, 那一步的终点是 docs/167 §5 记的 genesis。

    整条链**只跑一次**并在本进程内缓存: 三段自举编译每次一分多钟, 三条判据各建一遍是纯浪费。
    """
    global _MIRROR, _MIRROR_TD
    if _MIRROR is not None:
        return _MIRROR
    clang = _clang()
    assert clang, "缺 clang"
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    _MIRROR_TD = tempfile.TemporaryDirectory()      # 活到进程结束 (缓存产物在它里面)
    td = Path(_MIRROR_TD.name)
    s1 = td / "stage1"
    r = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-300:]
    binn = f"{_T}lomelf_mir_s1.bin"
    script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomelf.lomt")
    rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                        capture_output=True, timeout=900, shell=False)
    assert rr.returncode == 0, f"stage1 编译镜像失败: {rr.stderr[-300:]}"
    mir_ll = td / "lomelf.ll"
    mir_ll.write_bytes(rr.stdout)
    assert len(rr.stdout) > 100000, f"镜像 IR 太小 ({len(rr.stdout)}B)"
    mir = td / "lomelf.bin"
    r2 = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(mir_ll), "-o", str(mir)],
        capture_output=True, text=True, shell=False)
    assert r2.returncode == 0, f"镜像链接失败: {r2.stderr[-300:]}"
    _MIRROR = mir
    return mir


@test
def test_free_really_reclaims():
    """`free` **真的归还**（`docs/175` §3.4 的 `gc_manual`）—— 判据是**一对**程序。

    **Why**：在这一版之前 `free` 是**占位** —— `alloc` 是纯 bump（只涨不落），写 `free`
    编得过、检查得过，跑起来**什么都不发生**（`SKILL.md` 的内建表自己就写着"占位"）。
    于是"**总量超过 arena 但峰值很小**"的程序会耗尽 —— 而语言**早就允诺**了 `free`。
    这正是本仓反复认过的那种形状：**允诺却不兑现**（`docs/188` 的"宁拒勿猜"、
    `docs/175` §3.4 点名的就是它）。

    **How to apply**：两只程序**逐字同源**，只差 `free(p);` 那一行 ——
      * 带 `free`   → 跑完，退出码 = `2000 × 7 % 256 = 176`；
      * 不带 `free` → **必须崩**（arena 耗尽 → abort）。这一半是**证伪**：
        它保证上面那一半不是"反正都跑得通"。少了它，这条判据对"什么都没改"也是绿的。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcfree\n\nfn _start() {\n"
            "    let i: u32 = 0;\n"
            "    let sum: u32 = 0;\n"
            "    while i < 2000 {\n"
            "        let p: ptr = alloc(64);\n"
            "        store8(p, 0, 7);\n"
            "        let v: u32 = load8(p, 0);\n"
            "        sum = sum + v;\n"
            "        %s"
            "        i = i + 1;\n"
            "    }\n"
            "    syscall4(60, (sum %% 256) as u64, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, extra in (("free", "free(p);\n        "), ("nofree", "")):
            src = td / f"{tag}.lomt"
            src.write_text(body % extra, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, tag, td, timeout=20)[0])
    # 2000 次 × 64 B ≈ 144 KB **总量**，而**峰值**只有一个块 —— 64 KiB 的 arena 装得下
    # 的只有峰值，所以"跑得通"这件事只能是回收换来的。
    assert got[0] == 176, (
        f"带 `free` 的那只没跑通：退出码 {got[0]}（期望 176 = 2000×7%256）。"
        "要么分配器没归还，要么回收把已分配的内存还回去了 —— 两种都要查")
    assert got[1] != 176, (
        f"**不带 `free` 的那只也跑通了**（退出码 {got[1]}）—— 那说明这条判据测不出回收："
        "arena 没被耗尽，说明它比 64 KiB 大，或者 `free` 那一半根本不是回收在起作用")
    print(f"      带 free 跑通 (rc={got[0]})；不带 free 耗尽 (rc={got[1]}) —— 回收是真的")


@test
def test_gc_l2_epoch_bounds_the_heap():
    """L2 **真的发生**：同一份程序，`gc_manual` 耗尽 / `gc_auto_alpha` 跑通（`docs/210` §5）。

    **Why**：`docs/210` §4.4 说 L2 是"唯一**不扫描**的批量回收"，而它买的是栈纪律 ——
    循环体末尾把前沿退回去，**整段一次性不存在**。这件事只有跑起来才看得见：
    静态判据（`lomentc_test::test_l2_block_epoch_rule`）只能钉"编译器决定了开纪元"。

    **How to apply**：两只程序**逐字同源**，只差 `choose` 那一行 —— 循环体里每次分配
    **动态尺寸**的一块（所以 L0 不接：它只提字面量），累计 40000 × 64 B ≈ 2.56 MB，
    而 arena 只有 64 KiB。
      * 不带（默认 `gc_manual`）→ **必然耗尽**（实测 rc = 132，abort）—— 这一半是**证伪**：
        它保证上面那一半不是"反正都跑得通"；
      * `gc_auto_alpha` → **跑完**（rc = 7 = 循环真的转满了 40000 圈）。

    **峰值只有一个块**，所以"跑得通"这件事只能是回退换来的。这条也是**端到端**的：
    IR 过的是**自举镜像链接器**（`lomelf.compile_ll`），即包内那一只，不是 clang。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcl2\n%s\nfn _start() {\n"
            "    let n: u32 = 64;\n"
            "    let i: u32 = 0;\n"
            "    let s: u32 = 0;\n"
            "    while i < 40000 {\n"
            "        let p: ptr = alloc(n);\n"
            "        store8(p, 0, 1);\n"
            "        s = s + load8(p, 0);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if s == 40000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("alpha", "choose gc_auto_alpha\nchoose runtime"),
                        ("manual", "choose gc_manual")):
            src = td / f"{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, tag, td, timeout=30)[0])
    assert got[0] == 7, (
        f"`gc_auto_alpha` 那一只没跑完：退出码 {got[0]}（期望 7）。"
        "要么前沿没回退（纪元那条 store 没发或发错地方），要么回退把还活着的内存也退了")
    assert got[1] != 7, (
        f"**默认档那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出 L2："
        "arena 没被耗尽，说明它比 64 KiB 大得多，或者那些分配根本没落到 arena 上")
    print(f"      alpha 跑通 (rc={got[0]})；manual 耗尽 (rc={got[1]}) —— 块纪元是真的")


#: 只测一件事的 IR：`%j = phi [.., %i]` 与 `%i = phi [.., %i2]` 是**同一前驱上的一对**，
#: `%j` 因此每次回边取 `%i` 的**旧值**。转 3 圈后 `%j` 必须是 2。
_PHI_PAIR_IR = '''define i32 @_start() {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i2, %body ]
  %j = phi i32 [ 100, %entry ], [ %i, %body ]
  %d = icmp uge i32 %i, 3
  br i1 %d, label %out, label %body
body:
  %i2 = add i32 %i, 1
  br label %loop
out:
  %c = zext i32 %j to i64
  %r = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 60, i64 %c, i64 0, i64 0)
  ret i32 0
}
'''


@test
def test_mirror_phi_is_simultaneous():
    """镜像的 **phi 是同时赋值的**（LLVM 语义），不是"按声明顺序读一个写一个"。

    **Why**：同一前驱上的多个 phi 必须**一起**生效。最典型的形状是链表遍历里的那一对
    —— `%cur = phi [.., %nxt]` 与 `%prev = phi [.., %cur]`。一趟顺序写时，先写 `%cur`
    再读 `%cur` 给 `%prev`，`%prev` 拿到的就成了 `%nxt`。

    实测（2026-09-25）：这一条把 `__loment_free` 的空闲链表插入变成**每次都插在头部**
    —— 链表恒为 1 个节点，`free` 过的块再也取不回来；`gc_auto` 的收集器因此回收不掉，
    arena 一路涨到 OOM。**两个实现都错**（`tools/lomelf.py` 与 `loment/tools/lomelf.lomt`），
    而 `loment_p8_test` 一直绿 —— 它比的是**文本**，phi 的语义不在文本里。

    **How to apply**：这份 IR 只测这一件事（3 圈后 `%j` 该得 **2**；顺序写会得 **3**）。
    两半都要：
      * **跑出来的值**：参考实现编出来的产物得 2；
      * **两边的字节**：同一份 IR 过自举镜像，与参考逐字节相同 —— 少了这半，
        `loment/tools/lomelf.lomt` 退回一趟写也照样绿（那正是它当时的状态）。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    want_elf, _info = lomelf.compile_ll(_PHI_PAIR_IR)
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        nat = td / "phipair.native"
        nat.write_bytes(want_elf)
        rc, _out = _run_bin(nat, "phipair", td, timeout=20)
    assert rc == 2, (
        f"phi 不是同时赋值：`%j` 得 {rc}（期望 2；顺序写会得 3）。"
        "查 `_emit_phis_then` / `emit_phis` 是不是走了一趟（因果链见 docstring）")
    if not _clang():
        print("      参考侧: phi 同时赋值 (得 2)；自举侧: 无 clang, 跳过字节比对")
        return
    mir = _mirror()
    llrepo = ROOT / "loment" / "build" / "_phipair.ll"
    elfrepo = ROOT / "loment" / "build" / "_phipair.elf"
    try:
        llrepo.write_text(_PHI_PAIR_IR, encoding="utf-8", newline="\n")
        rcm, err = _mirror_run(mir, "loment/build/_phipair.ll", "loment/build/_phipair.elf")
        assert rcm == 0, f"镜像退出 {rcm}: {err[-200:]}"
        natm = elfrepo.read_bytes()
    finally:
        elfrepo.unlink(missing_ok=True)
        llrepo.unlink(missing_ok=True)
    assert natm == want_elf, (
        f"phi 的两份实现不同 ({len(natm)}B vs {len(want_elf)}B) —— "
        "自举侧多半还是有 phi 就一趟写")
    print("      phi 同时赋值: 参考得 2, 自举侧逐字节相同")


@test
def test_gc_manual_free_list_recycles():
    """`gc_manual` 的**空闲链表真的被复用**（不只是前沿回退）—— 判据是**一对**程序。

    **Why**：`test_free_really_reclaims` 测的是**前沿回退**（LIFO：分配—释放—再分配，
    被释放的那块正好顶着前沿）。那条在**空闲链表**整条坏掉时照样绿 —— 链表只在
    "放掉的块**不是**栈顶"时才起作用。

    **How to apply**：循环里同时握两个块 —— `cur` 是新分配的（**它就是栈顶**），
    `prev` 是上一圈那个；`free(prev)` 时 `cur` 还活着，于是**回退永远不触发**
    （回退要求被释放的块顶着前沿），回收只能来自链表复用。
      * 带 `free`   → 跑完，退出码 7（`s == 12000`：每圈读到的都是 3）；
      * 不带 `free` → **必须崩**（rc = 132，arena 耗尽）。这一半是**证伪**：
        它保证"跑通"不是"反正都跑得通"。

    实测（2026-09-25）：这条在 phi 修好之前是 **rc = 132** —— 那时的链表恒为 1 个节点。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    body = ("module gclist\nchoose gc_manual\n\nfn _start() {\n"
            "    let prev: ptr = alloc(64);\n"
            "    store8(prev, 0, 3);\n"
            "    let i: u32 = 0;\n"
            "    let s: u32 = 0;\n"
            "    while i < 4000 {\n"
            "        let cur: ptr = alloc(64);\n"
            "        store8(cur, 0, 3);\n"
            "        %s"
            "        s = s + load8(prev, 0);\n"
            "        prev = cur;\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if s == 12000 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, extra in (("free", "free(prev);\n        "), ("nofree", "")):
            src = td / f"{tag}.lomt"
            src.write_text(body % extra, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"list_{tag}", td, timeout=30)[0])
    assert got[0] == 7, (
        f"带 `free` 的那只没跑通：退出码 {got[0]}（期望 7）。"
        "峰值只有两块、总量 4000×72 B ≈ 288 KB —— 跑不通就说明链表没复用"
        "（回退在这儿帮不上忙：被释放的块从来不是栈顶）")
    assert got[1] != 7, (
        f"**不带 `free` 的那只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出链表复用："
        "arena 没被耗尽，说明它比 288 KB 大得多")
    print(f"      带 free 跑通 (rc={got[0]})；不带 free 耗尽 (rc={got[1]}) —— 链表复用是真的")


@test
def test_gc_auto_collects_while_manual_exhausts():
    """`choose gc_auto` 的收集器**真的回收**（`docs/210` §2.1）—— 判据是**一对**程序。

    **Why**：静态判据能钉住"收集器发了、挂点挂了"，却对"收集器是个空函数"照样绿。
    这一条是那一半：跑起来看。

    **How to apply**：两只程序**逐字同源**，只差 `choose` 那一行。循环里每圈漏掉一块
    64 字节、只留 `a` 一块活到末尾（末尾 `load8(a, 0) == 5` 就是"活块没被误回收"的
    守门）。总量 20000×72 B ≈ 1.4 MB，而 arena 只有 64 KiB：
      * `gc_auto` → **跑完**（rc = 7）；
      * `gc_manual` → **必然耗尽**（rc = 132，abort）。这一半是**证伪**：arena 没被
        撑爆就说明它比 1.4 MB 大得多，那这条判据什么也没测。

    **端到端**：IR 过的是**包内镜像链接器**（`lomelf.compile_ll`）。

    ⚠ **这条判据的边界**（别当成"收集器万能"）：触发按**分配量**（32 KiB）记，
    而 arena 是 64 KiB —— 所以**活集超过约 32 KB 的程序今天照样会耗尽**。
    这是明写的限制（`docs/210` §7），不是本判据能证的东西。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    body = ("module gcauto\n%s\nchoose runtime\n\nfn _start() {\n"
            "    let a: ptr = alloc(8);\n"
            "    store8(a, 0, 5);\n"
            "    let i: u32 = 0;\n"
            "    while i < 20000 {\n"
            "        let t: ptr = alloc(64);\n"
            "        store8(t, 0, 1);\n"
            "        i = i + 1;\n"
            "    }\n"
            "    if load8(a, 0) == 5 {\n        syscall4(60, 7, 0, 0);\n    }\n"
            "    syscall4(60, 3, 0, 0);\n}\n")
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = []
        for tag, ch in (("auto", "choose gc_auto"), ("manual", "choose gc_manual")):
            src = td / f"{tag}.lomt"
            src.write_text(body % ch, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat = td / f"{tag}.native"
            nat.write_bytes(blob)
            got.append(_run_bin(nat, f"auto_{tag}", td, timeout=60)[0])
    assert got[0] == 7, (
        f"`gc_auto` 那一只没跑完：退出码 {got[0]}（期望 7）。要么收集器没被调用，"
        "要么它没真的回收（标记/清扫/前沿回退任一处坏了），要么它把活块也收了")
    assert got[1] != 7, (
        f"**`gc_manual` 那一只也跑通了**（退出码 {got[1]}）—— 那这条判据测不出回收："
        "arena 没被耗尽，说明它比 1.4 MB 大得多")
    print(f"      auto 跑通 (rc={got[0]}，1.4 MB 请求过 64 KiB arena)；"
          f"manual 耗尽 (rc={got[1]}) —— 自动回收是真的")


def _mirror_run(mir: Path, in_rel: str, out_rel: str, links: tuple[str, ...] = ()) -> tuple[int, str]:
    """在 WSL 里用镜像编一个**仓库内相对路径**的 `.ll`。返回 (退出码, stderr)。

    stdout 与退出码不混在一个流里: 先让它跑, 再单独 `echo -n $?` (与 `_run_bin` 同一条纪律)。
    """
    tag = f"{_T}lomelf_m.bin"
    extra = "".join(f" --link {x}" for x in links)
    script = (f"cp {_wsl_path(mir)} {tag} && chmod +x {tag} && cd {_wsl_path(ROOT)} && "
              f"{tag} {in_rel} {out_rel}{extra}; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, timeout=900, shell=False)
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, r.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------- 用例

@test
def test_lomelf_matches_clang_behavior():
    """同一份 IR: 原生产物与 clang 产物的 stdout 字节 + 退出码完全一致。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in CORPUS:
            src = ROOT / rel
            name = src.stem
            ll = _ref_ir(src, td)
            ref = td / f"{name}.clang"
            nat = td / f"{name}.native"
            _link_clang(clang, ll, ref)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat.write_bytes(blob)
            want_rc, want = _run_bin(ref, f"{name}_c", td)
            got_rc, got = _run_bin(nat, f"{name}_n", td)
            if (want_rc, want) != (got_rc, got):
                raise AssertionError(
                    f"[{name}] 行为不一致: rc clang={want_rc} native={got_rc}\n"
                    f"  clang  ({len(want)}B): {want[:200]!r}\n"
                    f"  native ({len(got)}B): {got[:200]!r}")
            ok += 1
    print(f"      {ok} 个程序的原生产物与 clang 产物 stdout 字节 + 退出码一致")


@test
def test_every_example_is_decided():
    """`loment/examples/*.lomt` 里**每一份**都得有去处: 要么在 CORPUS 里真跑, 要么在
    NOT_RUN 里带一个理由 —— 没有第三种状态 (docs/205 R6)。

    **Why**: R6 的判据是"包里的示例每一个都进判据"。在这条之前只有 4 份被跑过,
    另外的**没有任何东西看着** —— 新加一份示例, 谁也不会发现它没被覆盖。
    文档里那个"N example programs"的数字更是从来没人核过: 2026-09-23 实测,
    README 写 30、QUICKSTART 写 28、盘上是 32, 三个数字互不相同。

    **How to apply**: 加一份示例就**必须**在这里挑一边。挑 NOT_RUN 要写清理由,
    而理由会被 `test_not_run_reasons_hold` 当场核实。
    """
    names = sorted(p.stem for p in (ROOT / "loment" / "examples").glob("*.lomt"))
    ran = sorted(Path(r).stem for r in CORPUS)
    both = sorted(set(ran) & set(NOT_RUN))
    assert not both, f"CORPUS 与 NOT_RUN 两边都有: {both}"
    undecided = sorted(set(names) - set(ran) - set(NOT_RUN))
    assert not undecided, f"没人管的示例 (编排漏了): {undecided}"
    stale = sorted((set(ran) | set(NOT_RUN)) - set(names))
    assert not stale, f"台账里多了已经不存在的文件: {stale}"
    empty = [k for k, (_kind, why) in NOT_RUN.items() if not why.strip()]
    assert not empty, f"理由为空: {empty}"

    # 文档里的数字要**说得对**, 不是"看起来合理" —— 这就是 R6 说的"不能静默腐烂"。
    for doc in ("README.md", "QUICKSTART.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        hits = re.findall(r"(\d+) example programs", text)
        assert hits, f"{doc} 里找不到 'N example programs' 这句话"
        wrong = [h for h in hits if int(h) != len(names)]
        assert not wrong, (f"{doc} 写的是 {wrong} 份, 盘上是 {len(names)} 份"
                           " —— 数字不会自己跟上, 它得有人改")
    print(f"      {len(names)} 份示例: {len(ran)} 份真跑 + {len(NOT_RUN)} 份带理由")


@test
def test_not_run_reasons_hold():
    """`NOT_RUN` 里的理由**当场成立** —— 真去链一次、真去编一次, 不是信那句话。

    **Why**: 一份"跑不了"的台账最容易退化成一句没人核过的注释: 后端补齐了、入口加上了、
    `while true` 改成会返回了 —— 台账自己不会知道, 而它照样读起来是对的。
    这里按**类别**把理由跑一遍: `no-entry` 要真被链接器以"没有 _start"拒掉,
    `ir-gap` 要真编不出来, `nonterm` 要源码里真是 `while true`。理由不成立 = 判据红。
    """
    td = Path(tempfile.mkdtemp())
    for name, (kind, _why) in sorted(NOT_RUN.items()):
        src = ROOT / "loment" / "examples" / f"{name}.lomt"
        if kind == "nonterm":
            text = src.read_text(encoding="utf-8")
            assert "while true" in text, f"{name}: 说是设计上不终止, 源码里却没有 while true"
            continue
        try:
            ll, got = _ref_ir(src, td), None
        except Exception as e:  # noqa: BLE001
            ll, got = None, f"{type(e).__name__}: {e}"
        if kind == "ir-gap":
            assert got is not None and "IR 后端" in got, (
                f"{name}: 记的是后端缺口, 实际却编过去了 ({got})")
            continue
        assert got is None, f"{name}: 记的是没有入口, 实际连 IR 都编不出来 ({got})"
        try:
            lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            refused = None
        except Exception as e:  # noqa: BLE001
            refused = f"{type(e).__name__}: {e}"
        assert refused and "没有 _start" in refused, (
            f"{name}: 记的是没有 _start 入口, 链接器给的却是: {refused}")
    print(f"      {len(NOT_RUN)} 条理由都当场核过")


@test
def test_lomelf_reports_unsupported_instead_of_miscompiling():
    """不支持的东西必须**报错退出**, 不许静默编出一个错的 ELF。"""
    bad = [
        ("结构体动态下标 GEP",
         "define void @_start() {\nentry:\n  %p = alloca { i32, i32 }\n  %i = load i32, ptr %p\n  %q = getelementptr { i32, i32 }, ptr %p, i32 %i\n  ret void\n}\n"),
        ("间接调用",
         "define void @_start() {\nentry:\n  %f = alloca ptr\n  %g = load ptr, ptr %f\n  call void %g()\n  ret void\n}\n"),
        ("没有 _start",
         "define void @main() {\nentry:\n  ret void\n}\n"),
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for label, text in bad:
            f = td / "bad.ll"
            f.write_text(text, encoding="utf-8", newline="\n")
            try:
                lomelf.compile_ll(text)
            except lomelf.Unsupported as e:
                assert str(e), f"{label}: 报了 Unsupported 但没有消息"
                continue
            raise AssertionError(f"[{label}] 本该报 Unsupported, 却编过去了")
    print(f"      {len(bad)} 类不支持的输入都报了错")


@test
def test_lomelf_selfhost_reports_oversized_input():
    """自举镜像: 输入超过输入缓冲上限要**报错退出**, 不是截断成一个跑起来就崩的二进制 (docs/192)。

    与上面那条"不支持的要报错"同一个家族 —— **不是"能不能编", 是"编不出来时说不说"**。

    自举镜像的输入缓冲是**固定大小**（`lomelf.lomt` 的 `IN_CAP`，布局算出来的：
    `M_IN` 起、`M_TXT` 接，两者之差就是它 —— 所以**上限从源码里读**：2026-09-19 它从
    2 MiB 抬到 4 MiB（`docs/192` §2），把旧数钉死的话这条会跟着红），而 `read_all`
    **读满就返回**。不加守卫的话超限的 `.ll` 被**悄悄截断** —— `.ll` 是逐行语法，截断点
    之后的函数定义凭空消失，汇编出来的是一个缺胳膊少腿、跑起来就段错误的可执行文件。
    2026-09-18 实测：同一份 `.ll`，这边段错误、clang 汇编完全正常。

    **参考侧没有这个上限**（`lomelf.py` 整个读进来）—— 所以这条只钉自举镜像那一格。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        m = re.search(r"const IN_CAP: u32 = (\d+);",
                      (ROOT / "loment" / "tools" / "lomelf.lomt").read_text(encoding="utf-8"))
        assert m, "在 lomelf.lomt 里找不到 `const IN_CAP: u32 = <数>;`"
        cap = int(m.group(1))
        # 仓库里最大的那一份 `.ll` + 注释填充撑过上限（注释不动语义, 本判据只看"拒不拒"）
        big = td / "big.ll"
        pad = "; " + "x" * 78 + "\n"
        need = cap - seed.stat().st_size
        big.write_text(pad * (need // len(pad) + 2) + seed.read_text(encoding="utf-8"),
                       encoding="utf-8", newline="\n")
        assert big.stat().st_size > cap, f"填充没撑过 {cap} (判据自己坏了)"
        rc, err = _mirror_run(mir, _wsl_path(big), "/tmp/lomelf_oversized.probe")
        assert rc != 0, (f"{big.stat().st_size} B 的输入被接受了 (rc={rc}, 上限 {cap}) —— "
                         f"截断是静默的, 产出的二进制跑起来才会崩")
        assert "上限" in err, f"拒了, 但没说清是上限的事: {err[:200]!r}"
        print(f"      超限 {big.stat().st_size} B 的 .ll 被拒（上限 {cap} B, 从源码读的）")


@test
def test_lomelf_cli_check_and_usage():
    """CLI: `--check` 不落盘且 rc=0; 用法错误 rc=2。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ref_ir(ROOT / CORPUS[0], td)
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py"), str(ll), "--check"],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"--check rc={r.returncode}: {r.stderr[-200:]}"
        assert "[OK]" in r.stdout, r.stdout[-200:]
        assert not (td / "user_hello").exists(), "--check 不该落盘"
        r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py")],
                            capture_output=True, text=True, shell=False)
        assert r2.returncode == 2, f"无参数应当 rc=2, 得到 {r2.returncode}"
    print("      --check 不落盘 rc=0 · 无参数 rc=2")


@test
def test_lomelf_compiles_selfhost_ir_without_clang():
    """**主线判据**: 种子 →(clang 一次)→ stage1 → 发 IR → lomelf 编成 ELF 并跑起来。

    这正是"用户编译一个 Loment 程序不再需要 clang"的形状: clang 只在**从种子重建 stage1**
    那一步出现 (那是 genesis, docs/167 §4 记账), 之后编译用户程序全程没有 clang。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        # stage1 发 IR (自举路, 无 Python 无 clang)
        entry = ROOT / "loment" / "examples" / "user_hello.lomt"
        binn = f"{_T}lomelf_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/examples/user_hello.lomt")
        rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                            capture_output=True, timeout=300, shell=False)
        assert rr.returncode == 0, rr.stderr[-300:]
        ll = td / "from_stage1.ll"
        ll.write_bytes(rr.stdout)
        assert b"_start" in rr.stdout, "stage1 的产物不像 IR"
        # lomelf 编它并跑
        blob, _info = lomelf.compile_ll(rr.stdout.decode("utf-8"))
        elf = td / "from_stage1.elf"
        elf.write_bytes(blob)
        rc, out = _run_bin(elf, "from_stage1", td)
        assert rc == 0, f"stage1 发的 IR 编出来的 ELF 跑挂了 rc={rc}"
        assert out == b"M67 RESULT: PASS loment-user\n", f"输出不符: {out!r}"
        assert entry.name == "user_hello.lomt"
    print("      种子 -> stage1 -> IR -> 原生产物 -> 跑出 M67 PASS (编译用户程序全程无 clang)")


@test
def test_lomelf_selfhost_matches_reference():
    """自举侧镜像: `loment/tools/lomelf.lomt` 编出的 ELF 与参考实现**逐字节相同**。

    这条是"编译一个 Loment 程序不需要 clang"真正落脚的地方 —— 参考实现 (Python) 只是
    这格的规格书, 能替用户干活的是自举侧那份。链条里 clang 只出现在"种子 -> stage1"
    一步 (genesis, docs/167 §5 记账)。

    语料是 `CORPUS` 减去 `MIRROR_GAP`（镜像还链不了的那两份, 各带理由）。
    把语料从 4 份放宽到"能跑的每一份"时, 这条**立刻抓到两个缺口** —— 一个是容量上限
    （点名拒的）, 一个是崩（issue #102）。`MIRROR_GAP` 的执行方式是**只许变短**:
    表里每一份每轮都要**真的再链一次**, 链过了就红。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # 语料: 参考 IR -> 镜像编 -> 与参考的字节比。`MIRROR_GAP` 里的先跳过,
        # 下面那段会**反向**核它们 —— 说链不过的, 必须真的还链不过。
        total = 0
        for rel in [r for r in CORPUS if Path(r).stem not in MIRROR_GAP]:
            srcl = ROOT / rel
            ll = _ref_ir(srcl, td)
            want, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            llrepo = ROOT / "loment" / "build" / f"_mirror_{srcl.stem}.ll"
            elfrepo = ROOT / "loment" / "build" / f"_mirror_{srcl.stem}.elf"
            llrepo.parent.mkdir(parents=True, exist_ok=True)
            try:
                llrepo.write_bytes(ll.read_bytes())
                rc, err = _mirror_run(mir, f"loment/build/_mirror_{srcl.stem}.ll",
                                      f"loment/build/_mirror_{srcl.stem}.elf")
                assert rc == 0, f"[{srcl.stem}] 镜像退出 {rc}: {err[-200:]}"
                nat = elfrepo.read_bytes()
            finally:
                elfrepo.unlink(missing_ok=True)
                llrepo.unlink(missing_ok=True)
            assert nat == want, f"[{srcl.stem}] 镜像产物与参考不同 ({len(nat)}B vs {len(want)}B)"
            total += 1

        # 反向核: MIRROR_GAP 里说"链不过"的, **必须真的还链不过**。哪天链过了, 判据红,
        # 逼你把它挪回语料 —— 这就是这张表"只许变短"的执行方式。
        gone = []
        for stem, (seen, why) in sorted(MIRROR_GAP.items()):
            src = ROOT / "loment" / "examples" / f"{stem}.lomt"
            if not src.exists():
                gone.append(f"{stem}: 文件不在了")
                continue
            ll = _ref_ir(src, td)
            llrepo = ROOT / "loment" / "build" / f"_gap_{stem}.ll"
            elfrepo = ROOT / "loment" / "build" / f"_gap_{stem}.elf"
            try:
                llrepo.write_bytes(ll.read_bytes())
                rc, _err = _mirror_run(mir, f"loment/build/_gap_{stem}.ll",
                                       f"loment/build/_gap_{stem}.elf")
            finally:
                elfrepo.unlink(missing_ok=True)
                llrepo.unlink(missing_ok=True)
            if rc == 0:
                gone.append(f"{stem}: 镜像**已经链得过**了 (记的是 {seen}, {why[:40]}…)")
        assert not gone, ("MIRROR_GAP 该变短了 —— 这几份现在能链, 把它们挪回语料: "
                          + " / ".join(gone))
    print(f"      {total} 个程序: 自举镜像与参考逐字节相同; "
          f"另有 {len(MIRROR_GAP)} 份记在 MIRROR_GAP 里, 已反向核实仍然链不过")


@test
def test_both_linkers_agree_on_a_negative_immediate():
    """**负立即数**: 操作数的跨度要含 `-`。

    LLVM IR 的立即数允许负号（`gc_manual` 的分配器里那句 `and i32 %need0, -8` 是第一处），
    而镜像里的操作数是拿"词"量出来的 —— 那一刻它不认 `-`，于是 `-8` 被量成**长度 0**：
    常量物化读到空串，**镜像发 `0`、参考发 `-8`**。

    这条**不依赖分配器**（它哪天不再用负立即数，这条照样在）：把一份语料里的某个正常量
    **取负**，两份链接器仍必须逐字节相同。替换点**找不到就红** —— 免得例句改过之后
    这条静默地什么也没测。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ref_ir(ROOT / "loment" / "examples" / "user_hello.lomt", td)
        text = ll.read_text(encoding="utf-8")
        old = "(i64 1, i64 %t1, i64 %t4, i64 %t8)"
        assert text.count(old) == 1, f"夹具漂了: user_hello 的 IR 里那个实参串出现 {text.count(old)} 次"
        neg = td / "_neg.ll"
        neg.write_text(text.replace(old, "(i64 -1, i64 %t1, i64 %t4, i64 %t8)"),
                       encoding="utf-8")
        want, _info = lomelf.compile_ll(neg.read_text(encoding="utf-8"))
        llrepo = ROOT / "loment" / "build" / "_neg_imm.ll"
        elfrepo = ROOT / "loment" / "build" / "_neg_imm.elf"
        try:
            llrepo.write_bytes(neg.read_bytes())
            rc, err = _mirror_run(mir, "loment/build/_neg_imm.ll", "loment/build/_neg_imm.elf")
            assert rc == 0, f"镜像退出 {rc}: {err[-200:]}"
            nat = elfrepo.read_bytes()
        finally:
            elfrepo.unlink(missing_ok=True)
            llrepo.unlink(missing_ok=True)
        assert nat == want, (f"负立即数: 镜像与参考不同 ({len(nat)}B vs {len(want)}B) —— "
                             f"量操作数跨度时把 `-` 丢了?")
    print("      负立即数: 两份链接器逐字节相同")


@test
def test_lomelf_rebuilds_the_compiler_without_clang():
    """**重建不需要 clang**: lomelf(种子) -> 一个能用的 Loment 编译器, 且它自编译
    `driver.lomt` 的产物**与种子逐字节相同**（定点成立）。整条链没有 clang 参与。

    这是 0.1.4 Alpha2 那条最严口径（"连发布/重建都不需要 C 编译器"）在**参考侧**的落地；
    真正消掉"第一个二进制"的是 docs/167 §5 第 4 条的 genesis。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    drv, _info = lomelf.compile_ll(seed.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = td / "drv.elf"
        elf.write_bytes(drv)
        binn = f"{_T}lomelf_drv.bin"
        outp = td / "drv2.ll"
        errp = td / "drv2.err"
        # **stderr 落文件而不是 /dev/null**: 这条曾经偶发红过一次, 而当时错误被丢掉了,
        # 只留下"退出码 1" —— 一份说不出理由的红对本仓库没有用 (旁边那条重建用例一直是这么做的)。
        script = (
            f"cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
            f"cd {_wsl_path(ROOT)} && {binn} loment/selfhost/driver.lomt "
            f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?"
        )
        r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                           capture_output=True, timeout=900, shell=False)
        etxt = errp.read_text(encoding="utf-8", errors="replace")[-400:] if errp.exists() else ""
        assert r.stdout.decode().strip() == "0", \
            f"原生编译器退出非零: {r.stdout[:40]!r} stderr={etxt!r}"
        got = outp.read_bytes()
    want = seed.read_bytes()
    assert got == want, f"[定点不成立] 自编译产物 {len(got)}B != 种子 {len(want)}B"
    print(f"      lomelf(种子) -> 编译器 -> 自编译产物 == 种子 ({len(got)}B), 全程无 clang")


@test
def test_lomelf_selfhost_rebuilds_the_compiler():
    """**自举侧也能重建编译器**: 种子 ->(clang 一次)-> stage1 -> 编出镜像 `lomelf.lomt`
    -> 镜像把**种子**编成一个编译器 -> 那个编译器自编译 `driver.lomt` 的产物 == 种子。

    也就是说"重建这套工具链不需要 C 编译器、也不需要解释器"在**自举侧**成立 ——
    clang 只在第一步（种子 -> stage1）出现，那一步的终点是 docs/167 §5 记的 genesis。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    mir = _mirror()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        # 镜像编种子 -> 新的编译器
        seed_c = td / "seed.elf"
        r3 = subprocess.run(
            ["wsl", "-e", "bash", "-lc",
             f"cp {_wsl_path(mir)} {_T}lomelf_rb2.bin && chmod +x {_T}lomelf_rb2.bin && "
             f"cd {_wsl_path(ROOT)} && {_T}lomelf_rb2.bin loment/build/selfhost_driver.ll "
             f"{_wsl_path(seed_c)}"],
            capture_output=True, text=True, timeout=900, shell=False)
        assert r3.returncode == 0, f"镜像编种子失败: {r3.stderr[-300:]}"
        # 用它自编译 driver.lomt, 与种子比
        outp = td / "again.ll"
        r4 = subprocess.run(
            ["wsl", "-e", "bash", "-lc",
             f"cp {_wsl_path(seed_c)} {_T}lomelf_rb3.bin && chmod +x {_T}lomelf_rb3.bin && "
             f"cd {_wsl_path(ROOT)} && {_T}lomelf_rb3.bin loment/selfhost/driver.lomt "
             f"> {_wsl_path(outp)} 2> {_wsl_path(td / 'err.txt')}; echo -n $?"],
            capture_output=True, text=True, timeout=900, shell=False)
        errf = td / "err.txt"
        etxt = errf.read_text(encoding="utf-8", errors="replace")[-300:] if errf.exists() else ""
        assert r4.stdout.strip() == "0", f"重建出的编译器退出非零: {r4.stdout[:40]!r} {etxt!r}"
        got = outp.read_bytes()
    want = seed.read_bytes()
    assert got == want, f"[自举侧定点不成立] {len(got)}B != 种子 {len(want)}B"
    print(f"      镜像 -> 编译器 -> 自编译产物 == 种子 ({len(got)}B), 自举侧全程无 clang 无解释器")


@test
def test_lomelf_selfhost_links_foreign_object():
    """**自举链接器也读外部 `.o`** (docs/173 FFI): 镜像 + `--link` 的产物与参考**逐字节相同**,
    而且跑出正确答案。

    这是 `docs/174 §4.1` 那条缺口的判据 —— 在此之前自举侧对 `--link` 是**硬拒**, 也就是说
    **装好的工具链根本链不了 `extern`** (仓库里能跑、包里的不能)。判据落在"镜像能链 +
    产物与参考一致 + 跑对", 不是"IR 里有 declare"。

    两个场景: **单对象** (C ABI 传参) 与 **双对象** (再加"第一个对象的符号名不能被第二个
    对象的读入盖掉")。双对象那条是**必须**的: 名字池的基若放错, 单对象照样绿。

    夹具与 `loment_ffi_test` 共用 (同一份 C 与同一份 Loment 源码), 不抄第二遍 —— 抄一份
    就会漂一份, 而"非交换运算"那条教训正是夹具本身的问题。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    mir = _mirror()
    bld = ROOT / "loment" / "build"
    # (标签, Loment 源码, C 源文件, --link 的顺序, 期望退出码)
    cases = [
        ("ffi", ffitest.LOMENT_SOURCE,
         [(ffitest.C_SOURCE, "c")], ["c"], 52),
        ("ffi2", ffitest.LOMENT_TWO_OBJECTS_SOURCE,
         [(ffitest.C_SOURCE, "c"), (ffitest.C2_SOURCE, "d")], ["c", "d"], 75),
        # 跨对象重定位 (docs/173 阶段 2): a.o 里的 PLT32 指向只有 b.o 知道的 c_sub。
        # 这条必须逐字节比 —— 自举侧重定位算错的话, 参考侧照样绿。
        ("ffi3", ffitest.LOMENT_MULTI_SOURCE,
         [(ffitest.C_CALLER_SOURCE, "a"), (ffitest.C_SUB_SOURCE, "b")], ["a", "b"], 22),
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        bld.mkdir(parents=True, exist_ok=True)
        for tag, lomt_src, csrcs, order, want_rc in cases:
            objs, clones = {}, []
            for text, stem in csrcs:
                (td / f"{stem}.c").write_text(text, encoding="utf-8", newline="\n")
                assert ffitest.compile_c(_clang(), td / f"{stem}.c", td / f"{stem}.o") == 0, \
                    "C 编译失败"
                objs[stem] = td / f"{stem}.o"
            src = td / f"{tag}.lomt"
            src.write_text(lomt_src, encoding="utf-8", newline="\n")
            ll = _ref_ir(src, td)
            want, _info = lomelf.compile_ll(
                ll.read_text(encoding="utf-8"),
                [lomelf.ForeignObject(objs[s]) for s in order])
            # 外部对象与 IR 都要放成**仓库内相对路径** —— 镜像在 WSL 里按相对路径找。
            llrepo = bld / f"_mirror_{tag}.ll"
            elfrepo = bld / f"_mirror_{tag}.elf"
            rel_objs = [f"loment/build/_mirror_{tag}_{s}.o" for s in order]
            try:
                llrepo.write_bytes(ll.read_bytes())
                for stem, rel in zip(order, rel_objs):
                    p = ROOT / rel
                    p.write_bytes(objs[stem].read_bytes())
                    clones.append(p)
                nat_rc, err = _mirror_run(mir, f"loment/build/_mirror_{tag}.ll",
                                          f"loment/build/_mirror_{tag}.elf", tuple(rel_objs))
                assert nat_rc == 0, f"[{tag}] 镜像 + --link 退出 {nat_rc}: {err[-300:]}"
                nat = elfrepo.read_bytes()
                assert nat == want, f"[{tag}] 镜像的 FFI 产物与参考不同 ({len(nat)}B vs {len(want)}B)"
                rc, out = _run_bin(elfrepo, f"ffi_{tag}", td)
                assert rc == want_rc, f"[{tag}] 跑出的结果不对: rc={rc} (期望 {want_rc}) {out[:120]!r}"
            finally:
                for p in [elfrepo, llrepo, *clones]:
                    p.unlink(missing_ok=True)
    print("      镜像 + --link 三例 (单对象/双对象/跨对象重定位): 与参考逐字节相同, "
          "退出码 52 / 75 / 22")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_elf_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
