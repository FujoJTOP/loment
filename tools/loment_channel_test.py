#!/usr/bin/env python3
"""渠道判据（`docs/231` §2、§5 判据 D 与 F）。

**这一条为什么必须存在**：`choose vm` 在**发行版**里是编译错误、"只在 dev 通道可用"
（用户 2026-10-11 定）。而"发行版"不是一个命令，是**这条流水线**（`loment_dist.py`）——
它把自举侧那一行渠道常量翻成 `1` 再建包。**没有判据的话，那一步悄悄失效谁也不知道**，
表现恰好是最坏的一种：发行版里 `choose vm` 又能编过了，而文档还写着它非法。

两半：

* **F（轻，到处可跑）**：发行版种子 = f(仓树, channel=release)，**可重算**；
  而且它与 dev 种子的差**恰好是那一个常量的效果** —— 差**一个字节**，长度相同。
* **D（重，要 clang + WSL）**：真建一个发行版驱动器，跑 `choose vm` ⇒ **必须被拒**；
  同一份源跑 dev 驱动器 ⇒ 必须通过。**两端都要**：只测"拒"的话，一个"什么都拒"的
  驱动器也能绿。

用法: python tools/loment_channel_test.py     (退出码 0 = 全过)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_p8_test as P8  # noqa: E402
import loment_seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = loment_seed.SEED

#: 探针：一份**声明了 `choose vm`** 的最小单元。它只在 dev 通道合法。
PROBE_VM = "module probe_vm\n\nchoose vm\n\nfn f() -> u32 {\n    return 1;\n}\n"
#: 对照组：同样一份、**不声明** `choose vm` —— 两个驱动器都必须收。
PROBE_PLAIN = "module probe_plain\n\nfn f() -> u32 {\n    return 1;\n}\n"
#: 探针：一份 `use libl` 的最小单元。**发行版里必须被拒**（`docs/232` §1.1，用户 2026-10-12：
#: 「LibL 仍然在仓库中，和 LVM 一样发行再锁」）。
#:
#: `choose runtime` 不能省：`libl` 是门面，它拉的那四份都声明了 `choose runtime`（库里写
#: `choose` 是**需求**，根必须满足，`docs/227` §2.2）。少了它就先在运行期那关被拒，
#: 测到的就不是渠道闸了。
PROBE_LIBL = ("module probe_libl\n\nchoose runtime\n\nuse libl\n\n"
              "fn f() -> u32 {\n    return 1;\n}\n")

#: 渠道常量在源码里被**读到**的处数 —— 也就是 dev/release 两个种子该差几处。
#:
#: 今天 2：`checker.lomt` 的 `check()`（`choose vm` 那条闸）与 `driver.lomt` 的
#: `use <名字>` 分支（`use libl` 那条闸，`docs/232` §1.1）。每读一次，常量就在 IR 里
#: 被具体化一次 —— 所以产物差 2 个字符（长度仍相同）。
#:
#: **改这个数之前先读那条判据的 docstring**：理由是"不为此往自举单元里加函数"。
CHANNEL_READ_SITES = 2

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- F：轻

@test
def test_repo_seed_is_dev_channel():
    """仓里那份种子是 **dev** —— 于是本仓的工具链接受 `choose vm`（`docs/231` §2.2）。"""
    got = loment_seed.reference_ir("dev")
    assert SEED.exists(), f"缺种子 {SEED}（跑 python tools/loment_seed.py --emit）"
    want = SEED.read_text(encoding="utf-8")
    assert got == want, ("仓里的种子与 dev 渠道的参考产物不一致 —— 先跑 "
                         "`python tools/loment_seed.py --emit`")


@test
def test_release_seed_differs_only_in_the_channel_constant():
    """发行版种子与 dev 种子的差**只有那个常量的取值**，而且**处数是可数的**。

    `docs/231` §2.3 承诺的是"包里**除那一行之外**逐字节等于仓库"（源码层：打包只替换
    `checker.lomt` 里的 `LOMENT_CHANNEL` 那一行）。这条判据把它的**产物层**后果钉成可证伪的：

      * 两个种子**长度相同**；
      * 差异**只在**那些"常量被读出来的地方"（IR 里是一条 `store i32 0/1, ptr %rel…`）；
      * 每一处都是 `'0' → '1'`；
      * **处数恰好等于** `CHANNEL_READ_SITES`。

    **为什么不是"恰好一个字符"**（2026-10-12 改）：渠道常量现在被**读两处** ——
    `check()` 里那条 `choose vm` 的闸，和 `driver.lomt` 里 `use libl` 那条（`docs/232` §1.1）。
    常量在每一处各被具体化一次，于是产物差 **2** 个字符而不是 1。**长度仍相同**。

    **没有把它合并成一个读取点**（那样差又回到 1）：合并要往自举单元里**加一个函数**，
    而 `checker.lomt` 那条注释记着实测 A/B —— 加函数那版 **62.9s（红）**、纯净树 58.24s（过），
    60s 护栏只剩 ~1% 余量。**用一个新函数换回一个好看的数字，不值得。**

    于是这里钉的是**处数**：`CHANNEL_READ_SITES` 写死 2。以后多一处读取点 ⇒ 这条红，
    逼改的人**先想清楚**那是不是有意的（并顺手更新 `docs/231` §5 判据 F 的口径）。
    """
    dev = loment_seed.reference_ir("dev")
    rel = loment_seed.reference_ir("release")
    assert len(dev) == len(rel), (
        f"两个种子长度不同（dev {len(dev)}B vs release {len(rel)}B）—— 注入改的"
        "**不止那个常量**（`docs/231` §2.3）")
    diff = [i for i in range(len(dev)) if dev[i] != rel[i]]
    assert diff, "两个种子一模一样 —— 渠道注入**根本没生效**（发行版会照收 `choose vm`）"
    assert len(diff) == CHANNEL_READ_SITES, (
        f"两个种子差 {len(diff)} 处（第 {diff[:5]} 个字符），而钉住的是 "
        f"{CHANNEL_READ_SITES} 处 —— 渠道常量被读到的**处数变了**。"
        f"多出来的那处要么是有意的（那就更新这个数与 `docs/231` §5 判据 F 的口径），"
        f"要么是注入改歪了")
    lines = dev.splitlines()
    for i in diff:
        assert (dev[i], rel[i]) == ("0", "1"), (
            f"第 {i} 个字符是 {dev[i]!r} → {rel[i]!r}，不是 '0' → '1'（`docs/231` §2.2）")
        # 每一处都必须落在"把常量存进那个局部变量"那一行上 —— 否则是"碰巧也只差这些处"。
        around = lines[dev[:i].count("\n")]
        assert "store i32 0, ptr %rel" in around, f"那一处在别的地方：{around!r}"


@test
def test_release_seed_is_deterministic():
    """重算两次得到同一串字节 —— 判据建立在"可重算"上（`docs/231` §5 判据 F）。"""
    a = loment_seed.reference_ir("release")
    b = loment_seed.reference_ir("release")
    assert a == b, "发行版种子两次算出来不一样 —— 判据没法建立在它上面"


# ---------------------------------------------------------------- D：重

@test
def test_dist_builds_with_the_release_seed():
    """`loment_dist.emit()` 走的是**发行版种子** —— 这是"包 = 发行版"的接线点。

    只钉**接线**（不建包）：把 `_use_release_seed()` 跑一遍，断言 `ACTIVE_SEED` 指到了
    `RELEASE_SEED` 那份、且那份的内容 == 现算的发行版种子。
    """
    import loment_dist  # noqa: PLC0415

    loment_dist._use_release_seed()
    assert loment_dist.ACTIVE_SEED == loment_dist.RELEASE_SEED, (
        "`emit()` 没有把打包用的种子换成发行版那份 —— 包里会是 dev 工具链，"
        "于是发行版照样收 `choose vm`（`docs/231` §2.3）")
    assert loment_dist.RELEASE_SEED.read_text(encoding="utf-8") == \
        loment_seed.reference_ir("release"), "落盘的发行版种子与现算的不一致"


@test
def test_release_driver_refuses_channel_gated_constructs():
    """**判据 D**：发行版驱动器拒**被渠道闸关住的东西**，dev 驱动器收 —— 两端都要测。

    被闸关住的今天有两个，形状不同、机制同一条（读同一个渠道常量）：

    * **`choose vm`** —— 一维核心模式，闸在 `check()`（`docs/231` §2）；
    * **`use libl`** —— 一个**名字**，闸在**名字解析**处（`docs/232` §1.1，用户 2026-10-12）。

    两个都测，而且**共用同一对驱动器**（各建一次 ELF 很贵）—— 两段闸读的是同一个常量，
    建两对驱动器只是把同一件事再做一遍。

    跳过条件与 `loment_p8_test` 同：要 clang（建 ELF）与 WSL（跑 ELF）。
    """
    if not P8._clang() or not P8._wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    probe_dir = ROOT / "loment" / "build"
    probe_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    try:
        for tag, src in (("plain", PROBE_PLAIN), ("vm", PROBE_VM), ("libl", PROBE_LIBL)):
            p = probe_dir / f"channel_probe_{tag}.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            made.append(p)
        rel = "loment/build/channel_probe_%s.lomt"
        with tempfile.TemporaryDirectory() as td:
            # ELF 与产物都落在**这个** td 里 —— 上面的 `_build_linux_elf` 返回的是
            # td 里的路径，td 一销毁它就是悬空的（第一版踩过）。
            dev_elf = P8._build_linux_elf(SEED.read_text(encoding="utf-8"), td, "chan_dev")
            rel_elf = P8._build_linux_elf(loment_seed.reference_ir("release"), td, "chan_rel")
            # **dev 三个都得过** —— 不然"发行版拒"可能只是因为它什么都拒。
            P8._run_driver(dev_elf, rel % "plain", td, "cd_p")
            P8._run_driver(dev_elf, rel % "vm", td, "cd_v")
            P8._run_driver(dev_elf, rel % "libl", td, "cd_l")
            # **发行版**：中性那份照过，两段闸各**必须非零退出**。
            P8._run_driver(rel_elf, rel % "plain", td, "cr_p")
            rc, _out, _err = P8._run_driver_raw(rel_elf, rel % "vm", td, "cr_v")
            assert rc != 0, "发行版驱动器收下了 `choose vm` —— 渠道闸没生效（`docs/231` §2）"
            rc2, _out2, err2 = P8._run_driver_raw(rel_elf, rel % "libl", td, "cr_l")
            assert rc2 != 0, ("发行版驱动器收下了 `use libl` —— 渠道闸没生效"
                              "（`docs/232` §1.1）")
            # **点名**：报文里要有那个名字，否则"拒了"与"因为别的理由拒了"分不开。
            assert "libl" in (err2 or ""), f"拒了但没点名 `libl`：{(err2 or '')[:120]!r}"
        print("      dev 收 `choose vm` 与 `use libl` / 发行版两个都拒（后者点名）—— 两端都核过")
    finally:
        for p in made:
            p.unlink(missing_ok=True)


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_channel_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
