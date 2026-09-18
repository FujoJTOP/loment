#!/usr/bin/env python3
"""loment_ct_test.py — **编译期子集解释器**的判据（S4.0，`docs/184` §9）。

语料在 `loment/ct/*.lomt`：每支是编译期子集里的一个可求值程序，入口是 `fn main() -> u64`。
**期望值写在语料文件里**（`// expect: OK <十进制>` 或 `// expect: ERR <种类>`）——
判据**不另抄一份**，所以两边不会漂。

判据两条，一条已落地、一条是 S4.0 的核心：

1. `test_reference_interpreter_matches_corpus` —— 参考侧（`tools/loment_interp.py`）
   跑语料，逐支对上期望值。**这是规范**，自举侧要镜像的就是它。
2. 自举侧（`loment/selfhost/interp.lomt`，**用 Loment 写的 Loment 解释器**）与参考侧
   **逐字节同结果** —— S4.0 的核心判据，随 S4.0b 落地（`docs/184` §9）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_interp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CT = ROOT / "loment" / "ct"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


#: 语料自描述的期望值。`OK <十进制>` 或 `ERR <种类>`（种类必须两边一致 —— 见 docstring）。
_EXPECT = re.compile(r"^//\s*expect:\s*(\S.*?)\s*$", re.M)


def _corpus() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for p in sorted(CT.glob("*.lomt")):
        m = _EXPECT.search(p.read_text(encoding="utf-8"))
        assert m, f"{p.name}: 缺 `// expect:` 行 —— 语料必须**自描述**期望值"
        out.append((p, m.group(1)))
    assert out, f"{CT} 里一支语料都没有 —— 判据会空转"
    return out


@test
def test_reference_interpreter_matches_corpus():
    """参考侧解释器跑编译期语料，逐支对上**写在文件里**的期望值。

    这条同时是**规范**：自举侧要镜像的语义就是它跑出来的。
    """
    cases = _corpus()
    bad: list[str] = []
    for p, want in cases:
        _rc, got = loment_interp.run(p)
        if got != want:
            bad.append(f"{p.name}: 期望 {want!r}, 实得 {got!r}")
    assert not bad, "\n".join(bad)
    print(f"      参考侧: {len(cases)} 支语料全部对上期望值")


def main() -> int:
    failed: list[str] = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_ct_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
