#!/usr/bin/env python3
# loment_seed_test.py — 无 Python 自举的闸门 (docs/159)
#
# 判据三条 (前两条不需要 clang, 第三条要 clang + 能跑 Linux ELF 的环境):
#   1. 种子 == 参考实现为 selfhost/driver.lomt 发射的 IR (种子不许过期);
#   2. 启动脚本静态检查: 命令位置不许出现任何第三方语言解释器, 且 LF 换行;
#   3. `sh loment/bootstrap.sh` 全绿 —— 即"只有 clang"能复现编译器:
#      stage1(种子) 编译自己 == 种子, stage2/stage3 逐字节相同, 跨阶段对非自身入口一致。
#
# 运行: python tools/loment_seed_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@test
def test_seed_matches_reference():
    """种子必须等于参考实现的产物 —— 自举产物一变, 种子就得重新固化。"""
    assert loment_seed.SEED.exists(), \
        f"缺 {loment_seed.SEED.relative_to(ROOT)} (python tools/loment_seed.py --emit)"
    rc = loment_seed.check()
    assert rc == 0, "种子过期 (见上面 [DIFF]; 运行 python tools/loment_seed.py --emit)"


@test
def test_bootstrap_script_is_python_free():
    """启动脚本不许调用解释器 (clang/sh 以外), 且必须是 LF —— WSL 的 sh 认不了 CRLF。"""
    rc = loment_seed.script_ok()
    assert rc == 0, "启动脚本违反静态判据 (见上面 [FAIL])"


@test
def test_seed_bootstrap_fixed_point():
    """只有 clang: 种子 -> stage1 -> (自复现) -> stage2 -> stage3 定点。

    这条是"脱离 Python"的判据本体。没有 clang (或没有 sh/WSL) 时打印 SKIP 而不是失败 ——
    但在 CI 机器上它必须真的跑过 (与其它自举测试同一个约定)。
    """
    import shutil
    if not (shutil.which("wsl") or shutil.which("sh")):
        print("      SKIP: 无 sh/WSL")
        return
    rc = loment_seed.bootstrap()
    assert rc == 0, "无 Python 自举失败"


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_seed_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
