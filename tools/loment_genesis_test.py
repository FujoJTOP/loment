#!/usr/bin/env python3
# loment_genesis_test.py — 链条的起点: genesis 起头能不能在**没有 clang** 的环境里跑通
# (docs/167 §5 ②, 审计主张 C21)
#
# 判据两条:
#   1. genesis 二进制与 `loment/build/genesis/SHA256SUMS` 的哈希一致, 且 `bootstrap.sh` 真的引用它;
#   2. 在**把 clang 从 PATH 里拿掉**的环境下跑 `sh loment/bootstrap.sh`, 四条证明全过
#      (种子自复现 + 三阶段定点 + 非自身入口一致)。
#
# 第二条是这条主张的全部意思: 重建这套工具链**不需要 C 编译器, 也不需要解释器** ——
# 链条上每一条 `.ll` 都由 genesis (提交进仓库的那个二进制) 自己汇编。
#
# 运行: python tools/loment_genesis_test.py   (无 WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_genesis  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


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


@test
def test_genesis_hash_and_reference():
    """genesis 的哈希与它自己的 SHA256SUMS 一致, 且 bootstrap.sh 引用了它。"""
    rc = loment_genesis.check()
    assert rc == 0, "loment_genesis --check 未通过 (缺文件 / 哈希不符 / 启动脚本没引用)"
    assert loment_genesis.GEN.exists(), "缺 genesis 二进制"
    print(f"      genesis {loment_genesis.GEN.stat().st_size} B, 哈希一致, bootstrap.sh 引用它")


@test
def test_bootstrap_needs_no_clang():
    """把 clang 从 PATH 拿掉, `sh loment/bootstrap.sh` 仍要跑通四条证明。"""
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    boot = ROOT / "loment" / "bootstrap.sh"
    assert boot.exists(), "缺 bootstrap.sh"
    # PATH 只留 /usr/bin:/bin (WSL 的 clang 不在里面); 显式把 LOMENT_USE_CLANG 清掉
    script = (f"cd {_wsl_path(ROOT)} && unset LOMENT_USE_CLANG CC && "
              f"env PATH=/usr/bin:/bin sh loment/bootstrap.sh")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=1800, shell=False)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"bootstrap 失败 rc={r.returncode}: {out[-400:]!r}"
    assert "genesis" in out, f"起点不是 genesis: {out[:400]!r}"
    assert "SEED BOOTSTRAP OK" in out, f"没有跑到结论行: {out[-300:]!r}"
    assert "1630436" in out.replace(" ", "").replace(",", ""), f"种子字节数不符: {out[-200:]!r}"
    print("      PATH 里没有 clang, bootstrap 仍四条全过 (genesis 起头)")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_genesis_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
