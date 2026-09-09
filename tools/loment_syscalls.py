#!/usr/bin/env python3
# loment_syscalls.py — 从 lom/fuai.lom 生成 Loment 系统调用层 (M72, docs/149)
#
# 判据: 46 个 FUAI 原语的 opcode 单一真源 -> Loment 包装函数 -> 与内核 dispatch 对账。
#   python tools/loment_syscalls.py --emit loment/build/fuai_syscalls.lomt
#   python tools/loment_syscalls.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FUAI = ROOT / "lom" / "fuai.lom"
SYS = ROOT / "kernel" / "src" / "syscall.rs"
HEADER = ("// 由 tools/loment_syscalls.py 从 lom/fuai.lom 生成 —— 请勿手改。\n"
          "// 46 个 FUAI 原语的 Loment 包装: syscall6(opcode, a0, a1, a2, a3, a4)。\n")


def opcodes() -> list[tuple[str, int]]:
    mod = lomc.load(FUAI)
    e = next(x for x in mod.enums if x.name == "Opcode")
    return [(v.name, v.value) for v in e.variants]


def emit() -> str:
    out = [HEADER, "module fuai_syscalls", ""]
    for name, code in opcodes():
        out.append(f"pub fn fuai_{name}(a0: u64, a1: u64, a2: u64, a3: u64, a4: u64) -> i64 {{")
        out.append(f"    return syscall6({code}, a0, a1, a2, a3, a4);")
        out.append("}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def kernel_has(code: int) -> bool:
    src = SYS.read_text(encoding="utf-8")
    return bool(re.search(rf"0x{code:04x}\s*=>", src, re.I))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_syscalls")
    ap.add_argument("--emit", metavar="PATH")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    want = emit()
    ops = opcodes()
    missing = [(n, c) for n, c in ops if not kernel_has(c)]
    if a.check:
        dest = ROOT / "loment" / "build" / "fuai_syscalls.lomt"
        if not dest.exists():
            print(f"[ERR] {dest.relative_to(ROOT)} 缺失 (运行 --emit)")
            return 1
        if dest.read_text(encoding="utf-8") != want:
            print(f"[ERR] {dest.relative_to(ROOT)} 与 lom/fuai.lom 不一致")
            return 1
        print(f"[OK] fuai_syscalls.lomt 一致 ({len(ops)} 个原语); "
              f"内核 dispatch 覆盖 {len(ops) - len(missing)}/{len(ops)}")
        for n, c in missing:
            print(f"  [NOTE] {n} (0x{c:04x}) 未在 kernel/src/syscall.rs 找到 (host-ext?)")
        return 0
    if a.emit:
        Path(a.emit).write_text(want, encoding="utf-8")
        print(f"[OK] {a.emit} ({len(ops)} 个包装函数)")
        return 0
    print("[ERR] 需要 --emit 或 --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
