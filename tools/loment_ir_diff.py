#!/usr/bin/env python3
"""Loment 版 codegen 与参考实现的 .ll 差异定位 (M82 的调试面, docs/150/156)。

用法:
    python tools/loment_ir_diff.py native_res.lomt          # 前 60 行 diff
    python tools/loment_ir_diff.py demo.lomt 200            # 前 200 行
    python tools/loment_ir_diff.py --all                    # 全语料: 只报"一致 / 首个差异偏移"

判据与 tools/loment_p8_test.py 相同 (同一份夹具构造 codegen 可执行文件 + 拼接编译单元),
这里只负责把"哪一行开始不一样"摆出来 —— 门禁里不跑, 是定位工具不是检查项。
"""
from __future__ import annotations

import argparse
import difflib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_p8_test as T  # noqa: E402
import lomentc  # noqa: E402

ROOT = T.ROOT


def _load(target: Path):
    mod, deps = lomentc.load_unit(target, ROOT)     # 唯一入口（docs/182 §1.10）
    return lomentc.emit_llvm(mod, ROOT, deps)


def _corpus() -> list[Path]:
    return sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                  + list((ROOT / "loment" / "selfhost").glob("*.lomt")))


def _first_diff(got: str, want: str) -> int | None:
    return next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_ir_diff", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="示例名 (demo.lomt) 或路径")
    ap.add_argument("lines", nargs="?", type=int, default=60, help="打印的 diff 行数 (默认 60)")
    ap.add_argument("--all", action="store_true", help="跑全语料, 汇总")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as td:
        exe = T._build_codegen(td)
        targets = _corpus() if args.all else None
        if targets is None:
            if not args.target:
                ap.error("需要 target 或 --all")
            p = Path(args.target)
            targets = [p if p.exists() else ROOT / "loment" / "examples" / args.target]

        bad = 0
        for target in targets:
            try:
                want = _load(target)
            except Exception as e:  # noqa: BLE001  参考实现的 IR 后端自己就不发这个示例
                print(f"非目标 {target.name}: {type(e).__name__}: {e}")
                continue
            got = T._run_codegen(exe, target, td)
            i = _first_diff(got, want)
            if got == want:
                if args.all:
                    print(f"一致 {target.name} ({len(want)}B)")
                else:
                    print(f"{target.name}: 逐字节一致 ({len(want)}B)")
                continue
            bad += 1
            print(f"不一致 {target.name}: want {len(want)}B got {len(got)}B, 首个差异 @{i}")
            if not args.all:
                d = list(difflib.unified_diff(want.splitlines(), got.splitlines(),
                                              "python", "loment", lineterm="", n=2))
                for line in d[:args.lines]:
                    print(line)
                print(f"... 共 {len(d)} 行 diff")
        return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
