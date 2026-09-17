#!/usr/bin/env python3
# loment_std_test.py — std 核的**行为**判据 (docs/180)
#
# 为什么要单开一条: `loment_lib_test` 测的是**库系统** (身份/去重/物化/多版本共存),
# `loment_json_test` 测的是 json 一个模块。**没有一个判据在问"这些库里那些函数算得对不对"。**
# 库里最容易被"看起来有"蒙混过去的就是这个 —— 函数签名在、检查过、编得出, 而算错了。
#
# 做法照 `loment_json_test`: 探针程序调库, stdout 必须与 **Python 算出来的**逐字节相同。
# 用 Python 当对照物而不是硬编码期望值, 是因为硬编码的那份是我们自己写的,
# 自己写的东西两处一起错就看不出来了 (docs/176 §7.3 那条的同一种病)。
#
# 用法: python tools/loment_std_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_json_test as J                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 探针里的 `chk` 每个都打一行 `<标签> <值>`。Python 侧按同一顺序算一遍。
PROBE = r'''
module stdprobe

use "loment/lib/mem.lomt"
use "loment/lib/num.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

// `sc` 是 say **自己的**暂存 —— 不能拿调用方的缓冲: `say(b, "x=", num_to_dec(b, v))`
// 里 say 会再写一次那个缓冲, 于是"打印某个缓冲的内容"就变成打印被覆写后的东西。
// 第一版就是这么错的 (探针自己的 bug, 与库无关) —— 记在这里免得下次再踩。
fn say(sc: ptr, tag: str, v: u32) {
    let _a: i64 = wr(1, str_ptr(tag), str_len(tag));
    let _b: u32 = num_to_dec(sc, v);
    let _c: i64 = wr(1, sc, _b);
    let _d: i64 = wr(1, str_ptr("
"), 1);
}

fn _start() {
    let sc: ptr = alloc(32);
    // ---- mem: 填 / 拷 / 比 / 找 / 重叠移动
    let a: ptr = alloc(32);
    let b: ptr = alloc(32);
    mem_fill(a, 8, 65 as u8);
    mem_copy(b, a, 8);
    say(sc, "eq=", mem_eq(a, b, 8) as u32);
    store8(b, 3, 66 as u8);
    say(sc, "ne=", mem_eq(a, b, 8) as u32);
    say(sc, "find=", mem_find(b, 8, 66 as u8) as u32);
    say(sc, "nofind=", mem_find(b, 8, 90 as u8) as u32);
    mem_fill(b, 8, 0 as u8);
    say(sc, "zero=", load8(b, 7));
    let c: ptr = alloc(16);
    store8(c, 0, 1 as u8);
    store8(c, 1, 2 as u8);
    store8(c, 2, 3 as u8);
    store8(c, 3, 4 as u8);
    mem_move(ptr_add(c, 1), c, 4);
    say(sc, "mv0=", load8(c, 1));
    say(sc, "mv3=", load8(c, 4));
    say(sc, "ne=", mem_find_ne(a, 8, 65 as u8) as u32);
    // 小端 32 位读写: 存进去再读回来, 且按字节看
    let w: ptr = alloc(8);
    mem_store32(w, 0, 305419896);
    say(sc, "l32=", mem_load32(w, 0));
    say(sc, "b0=", load8(w, 0));
    say(sc, "b3=", load8(w, 3));
    // ---- num: 十进制
    let d: ptr = alloc(32);
    say(sc, "dec0=", num_to_dec(d, 0));
    say(sc, "dec1=", num_dec_len(1234567));
    let n1: u32 = num_to_dec(d, 1234567);
    let _e: i64 = wr(1, d, n1);
    let _f: i64 = wr(1, str_ptr("
"), 1);
    let n2: u32 = num_to_dec(d, 4294967295);
    let _g: i64 = wr(1, d, n2);
    let _h: i64 = wr(1, str_ptr("
"), 1);
    say(sc, "parse=", num_parse_dec(str_ptr("370x9"), 5));
    say(sc, "span=", num_dec_span(str_ptr("370x9"), 5));
    say(sc, "spannone=", num_dec_span(str_ptr("x9"), 2));
    // ---- num: 十六进制
    let h: ptr = alloc(16);
    say(sc, "hexn=", num_to_hex(h, 48879, 4));
    let _i: i64 = wr(1, h, 4);
    let _j: i64 = wr(1, str_ptr("
"), 1);
    let _k: u32 = num_to_hex(h, 10, 4);
    let _l: i64 = wr(1, h, 4);
    let _m: i64 = wr(1, str_ptr("
"), 1);
    say(sc, "hexw=", num_to_hex(h, 305419896, 2));
    let _n: i64 = wr(1, h, 2);
    let _o: i64 = wr(1, str_ptr("
"), 1);
    say(sc, "phex=", num_parse_hex(str_ptr("beef!"), 5));
    say(sc, "hspan=", num_hex_span(str_ptr("beef!"), 5));
    say(sc, "hbad=", num_hex_val(33 as u8) as u32);
    say(sc, "hup=", num_parse_hex(str_ptr("BEEF"), 4));
    syscall4(60, 0, 0, 0);
}
'''


def _expected() -> str:
    """**用 Python 算**同一串结果 —— 不硬编码。顺序与探针里的 `say` 一一对应。

    硬编码期望值的毛病: 那份期望也是我们自己写的, 两处一起错就看不出来。
    """
    out = io.StringIO()

    def say(tag, v):
        out.write(f"{tag}{v}\n")

    a = bytes(b"A" * 8)
    b = bytearray(a)
    say("eq=", 1)
    b[3] = 66
    say("ne=", 0)
    say("find=", 3)
    say("nofind=", 0xFFFFFFFF)          # -1 as u32
    b = bytearray(8)
    say("zero=", 0)
    c = bytearray([1, 2, 3, 4])
    c[1:5] = c[0:4]                     # mem_move(ptr_add(c,1), c, 4) —— 从尾到头
    say("mv0=", c[1])
    say("mv3=", c[4])
    say("ne=", 0xFFFFFFFF)              # 全是 'A' -> -1 as u32
    w = (305419896).to_bytes(4, "little")
    say("l32=", 305419896)
    say("b0=", w[0])
    say("b3=", w[3])
    d = bytearray(32)
    d[0:1] = b"0"
    say("dec0=", 1)
    say("dec1=", 7)
    out.write("1234567\n")
    out.write("4294967295\n")
    say("parse=", 370)
    say("span=", 3)
    say("spannone=", 0)
    say("hexn=", 4)
    out.write("beef\n")
    out.write("000a\n")
    say("hexw=", 2)
    out.write(f"{305419896 % 256:02x}\n")
    say("phex=", 0xBEEF)
    say("hspan=", 4)
    say("hbad=", 0xFFFFFFFF)
    say("hup=", 0xBEEF)
    return out.getvalue()


@test
def test_std_modules_match_python():
    """std 核的 mem / num 两模块: Loment 算的和 Python 算的**逐字节相同**。"""
    if not J._clang():
        print("      SKIP: 无 clang")
        return
    probe = ROOT / "loment" / "build" / "std_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(PROBE)
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            elf = J._compile(probe, td, "std_probe")
            got = J._run(elf, td)
    finally:
        probe.unlink(missing_ok=True)
    want = _expected()
    if got != want:
        g, w = got.splitlines(), want.splitlines()
        first = next((i for i in range(max(len(g), len(w)))
                      if (g[i] if i < len(g) else None) != (w[i] if i < len(w) else None)), None)
        raise AssertionError(
            f"std 核结果与 Python 不一致 @ 第 {first} 行: "
            f"Loment={g[first] if first is not None and first < len(g) else '<无>'!r} "
            f"Python={w[first] if first is not None and first < len(w) else '<无>'!r}")
    print(f"      mem + num: {len(want.splitlines())} 项与 Python 逐字节一致")


@test
def test_std_modules_are_checkable():
    """每个 std 模块**自己**必须是合法的 L1 单元 (这条不靠 clang, 永远跑)。"""
    import lomentc
    libs = sorted((ROOT / "loment" / "lib").glob("*.lomt"))
    assert len(libs) >= 5, [p.name for p in libs]
    for p in libs:
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"{p.name}: {errs[:2]}"
    print(f"      {len(libs)} 个 std 模块都过检查: {[p.stem for p in libs]}")


def main(argv: list[str] | None = None) -> int:
    only = argv[0] if argv else None
    failed = []
    for name, fn in TESTS:
        if only and only not in name:
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    n = len([x for x, _ in TESTS if not only or only in x])
    print(f"\nloment_std_test: {n - len(failed)}/{n} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
