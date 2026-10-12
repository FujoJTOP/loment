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
#: 探针：`choose hosted`（`docs/222` §4）。**发行版里同样非法** —— 那扇门要的是真 libc /
#: 真共享库，而那条路还没完工（`docs/219` §8b 的 S2 还差三件）。2026-10-12 用户定：
#: 0.1.4.5 里 libc 不可调用。与 `vm` 那条同一个闸、同一个理由。
#: 入口写 `fn main`（`docs/222` §4.3：hosted 产物由 C 运行期起）—— 这一条只看驱动器收不收。
PROBE_HOSTED = "module probe_hosted\n\nchoose hosted\n\nfn main() -> u32 {\n    return 1;\n}\n"

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
def test_release_seed_differs_by_exactly_one_byte():
    """发行版种子与 dev 种子的差**恰好是那一个常量的效果**。

    `docs/231` §2.3 承诺的是"包里**除那一行之外**逐字节等于仓库"。这条判据把它钉成
    可证伪的：两个种子**长度相同**、**只差一个字符**（`chk_channel` 的 `ret i32 0` 对
    `ret i32 1`）。哪天注入改歪了（比如说换错了行、或者多改了一处），长度或差异个数
    会立刻变 —— 那正是这条要抓的。
    """
    dev = loment_seed.reference_ir("dev")
    rel = loment_seed.reference_ir("release")
    assert len(dev) == len(rel), (
        f"两个种子长度不同（dev {len(dev)}B vs release {len(rel)}B）—— 注入改的"
        "**不止那一个常量**（`docs/231` §2.3）")
    diff = [i for i in range(len(dev)) if dev[i] != rel[i]]
    assert diff, "两个种子一模一样 —— 渠道注入**根本没生效**（发行版会照收 `choose vm`）"
    assert len(diff) == 1, (
        f"两个种子差 {len(diff)} 处（第 {diff[:5]} 个字符）—— 只该差**一个**："
        "`chk_channel` 里那个 `ret i32 0` / `ret i32 1`")
    i = diff[0]
    assert (dev[i], rel[i]) == ("0", "1"), (
        f"差的那个字符是 {dev[i]!r} → {rel[i]!r}，不是 '0' → '1'（`docs/231` §2.2）")
    # 而那一个字符必须落在渠道那个函数里 —— 否则是"碰巧也只差一处"。
    line = dev[:i].count("\n")
    around = dev.splitlines()[line]
    assert "i32 0" in around, f"那一处在别的地方：{around!r}"


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
def test_the_python_checker_refuses_hosted_in_the_release_channel():
    """参考实现的**端口闸**（`docs/222` §4）：`loment_channel.RELEASE` 为真时
    `choose hosted` 报错、为假时不报。

    **不需要 clang** —— 这一条只走 `load` + `check`，所以它在哪儿都能跑。两端都测：
    只测"拒"的话，一个"什么都拒"的检查器也能绿。
    """
    import lomentc  # noqa: PLC0415
    import loment_channel  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "h.lomt"
        p.write_text(PROBE_HOSTED, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, Path(td), entry=p)
        old = loment_channel.RELEASE
        try:
            loment_channel.RELEASE = False
            dev = lomentc.check(mod, deps=deps)
            loment_channel.RELEASE = True
            rel = lomentc.check(mod, deps=deps)
        finally:
            loment_channel.RELEASE = old
    assert not dev, f"开发通道也拒了 `choose hosted` —— 那这一维就永远是死的：{dev}"
    assert rel, "发行版收下了 `choose hosted` —— 端口闸没生效（`docs/222` §4）"
    assert any("端口" in e for e in rel), rel
    print("      Python 检查器：dev 收 / 发行版拒 `choose hosted` —— 两端都核过")


@test
def test_the_release_launcher_shuts_the_port_door():
    """发行版启动器（`docs/222` §4）：`--hosted` **点名拒**，且**永不**给链接器
    `--allow-ports`。

    两层一起才成立：链接器的默认是"拒"，而"发行版不传那面旗"这件事靠启动器里的
    `@CHANNEL@` 替换 —— 替换错了、或者哪次改动让那条 `dev` 分支活过来，这一条就红。
    """
    import loment_dist  # noqa: PLC0415

    for name, tpl in (("launcher.sh", loment_dist.LAUNCHER_SH),
                      ("launcher.cmd", loment_dist.LAUNCHER_CMD)):
        dev = loment_dist._subst(tpl, "dev")
        rel = loment_dist._subst(tpl, "release")
        if name.endswith(".sh"):
            # 开发版：条件是 `"dev" = "dev"` ⇒ **活的**（`portarg` 真的被设上）。
            assert '[ "dev" = "dev" ]' in dev, name
            # 发行版：左边操作数换成 release ⇒ **死的** ⇒ `portarg` 恒为空 ⇒ 链接器默认生效。
            assert '[ "release" = "dev" ]' in rel, (
                f"{name}: 发行版启动器里那条 dev 分支还活着 —— 它会把 `--allow-ports` "
                "传给链接器，于是包里的 libc 又可调用了")
        else:
            assert '"dev"=="dev"' in dev, name
            assert '"release"=="dev"' in rel, (
                f"{name}: 发行版启动器里那条 dev 分支还活着 —— 它会把 `--allow-ports` "
                "传给链接器，于是包里的 libc 又可调用了")
    # sh 那一份还多一道**点名的门**：`--hosted`（或源码里的端口标记）在发行版里直接拒。
    sh_rel = loment_dist._subst(loment_dist.LAUNCHER_SH, "release")
    assert "-n \"$hosted\"" in sh_rel and "release build" in sh_rel, (
        "发行版启动器没有把 `--hosted` 点名拒掉 —— 那扇门就只剩编译器那道闸"
        "（源码里写 `choose hosted` 会被拒，但命令行 `--hosted` 绕过编译器）")
    assert "--hosted" in loment_dist.LAUNCHER_SH, "开发版启动器必须留着 `--hosted`"
    print("      两份启动器：dev 传 `--allow-ports` / 发行版不传且点名拒 `--hosted`")


@test
def test_release_driver_refuses_dev_only_dimensions_and_dev_driver_accepts():
    """**判据 D**：发行版驱动器拒 `choose vm` 与 `choose hosted`，dev 驱动器收 —— 两端都要测。

    跳过条件与 `loment_p8_test` 同：要 clang（建 ELF）与 WSL（跑 ELF）。
    """
    if not P8._clang() or not P8._wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    probe_dir = ROOT / "loment" / "build"
    probe_dir.mkdir(parents=True, exist_ok=True)
    #: (标签, 源码, 发行版里必须拒吗) —— `plain` 那行是**对照组**：它在两边都得过。
    probes = [("plain", PROBE_PLAIN, False),
              ("vm", PROBE_VM, True),
              ("hosted", PROBE_HOSTED, True)]
    made: list[Path] = []
    try:
        for tag, src, _refuse in probes:
            p = probe_dir / f"channel_probe_{tag}.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            made.append(p)
        rel = "loment/build/channel_probe_%s.lomt"
        with tempfile.TemporaryDirectory() as td:
            # ELF 与产物都落在**这个** td 里 —— 上面的 `_build_linux_elf` 返回的是
            # td 里的路径，td 一销毁它就是悬空的（第一版踩过）。
            dev_elf = P8._build_linux_elf(SEED.read_text(encoding="utf-8"), td, "chan_dev")
            rel_elf = P8._build_linux_elf(loment_seed.reference_ir("release"), td, "chan_rel")
            for tag, _src, refuse in probes:
                # **dev 每一份都得过** —— 不然"发行版拒"可能只是因为它什么都拒。
                P8._run_driver(dev_elf, rel % tag, td, f"cd_{tag}")
                rc, _out, err = P8._run_driver_raw(rel_elf, rel % tag, td, f"cr_{tag}")
                if refuse:
                    assert rc != 0, (
                        f"发行版驱动器收下了 `choose {tag}` —— 渠道闸没生效"
                        f"（`docs/231` §2 / `docs/222` §4）")
                else:
                    assert rc == 0, f"对照组在发行版里被拒了：{err[-200:]}"
        print("      dev 收 / 发行版拒：vm 与 hosted 两维 —— 两端都核过")
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
