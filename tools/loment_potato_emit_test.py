#!/usr/bin/env python3
"""loment_potato_emit_test.py — 自举侧的 **Potato v5 发射** 逐字节判据（`docs/189` S1）。

对照面是 `tools/lomentc.py::emit_potato`：同一份源，自举驱动发的形式对象与参考实现发的
必须**逐字节相同**。形式对象不是"差不多就行"的那种产物 —— 它是**跨线契约的那一侧**
（`docs/147` §5 承诺过旧版可回放），差一个字节就是两个对象。

    stage1 <unit.lomt> --emit-potato     ←→     lomentc.emit_potato(...)

## 三条判据，各管一件事

1. `test_potato_emit_matches_reference` —— **语料上逐字节**。语料是**枚举的**
   （`COVERED`），而且 `test_every_example_is_decided` 钉住"`loment/examples` 下每一份
   要么在 `COVERED`、要么在 `REFUSED`、要么在 `SKIPPED` 里"。新加一份示例而没做决定会
   红在那一条 —— 这一格最怕的不是测错，是**新语料悄悄不测**。
2. `test_out_of_subset_is_refused_by_name` —— 子集外的每一条轴**点名拒绝**（退出码 1，
   话里出现那个轴的名字），而不是发一个空壳或者错壳出去。这一条是这一格的**下限**：
   绿在"覆盖的那片对"上不算数，还得保证"没覆盖的那片不会被静默糊过去"。
3. `test_seed_is_not_stale` —— 判据跑的是**种子链成的 stage1**。种子与树不同步时这一条
   先红，免得"绿"是在测上一版二进制（`loment_seed_test` 也管这件事，这里重说一遍是因为
   **这一格的绿完全依赖它**）。

用法: python tools/loment_potato_emit_test.py     （缺 clang 时 SKIP，退出码 0）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"

#: 这一格**覆盖**的语料: 单文件（不带 `use`）、非泛型、无 trait/impl 的那一片。
#: 逐份与参考实现比字节 —— 不挑"有代表性"的几份。
COVERED = (
    "bootprobe.lomt",
    "bytes.lomt",
    "mathutil.lomt",
    "native.lomt",
    "native_agg.lomt",
    "native_bits.lomt",
    "native_brk.lomt",
    "native_cap.lomt",          # capability + guard(2) + revocable
    "native_concat.lomt",
    "native_entry.lomt",
    "native_match_full.lomt",
    "native_mem.lomt",
    "native_mut.lomt",          # `mut [u32]` / `[u32]` 形参 —— 类型渲染的边界
    "native_slice.lomt",
    "native_str.lomt",
    "native_trait.lomt",        # trait + 两个 impl —— `traits`/`impls` 的原始视图 +
                                # impl 方法的 `self`（改名 `__self` + 填接受者类型）
    # ---- 泛型**函数/类型**的单态化（`instances` 那一格 + 函数表被替换）
    "all_loment.lomt",          # `max_of_u32` —— 实例名 = 基名 + 实参类型
    "native_res.lomt",          # M7: 预置 `Result<T, E>` 的实例化（`Result_u32_u32`）——
                                # 枚举实例 + 签名里的改名 + `generics` 那三组
    "native_gen_sig.lomt",      # M7: 泛型 struct 的实例化（`Box_i32` / `Box_u32`）+ 字段替换,
                                # **两份实例**用来钉住"按串排序"（不排就会分叉）
    "native_gen.lomt",          # **字段访问当泛型实参**（`max(p.a, p.b)`）—— 参考实现按
                                # **字段**类型推（`p.a` → `u32`，于是实例名 `max_u32`），
                                # 这一格 2026-09-23 起也这么推（`pt_field_arg_ty`）。
                                # 泛型 struct 的字段类型**就是那个类型形参**时要换成**声明处
                                # 写的实参** —— 那一段 token 本来就在单元里，指回去即可。
    "demo.lomt",                # M-L0: `use "lom/fujr.lom"` —— `layouts` 那一格要**读那个 L0 文件**
                                # 并解析它的 `record`（Header/Section；字段按偏移排）
    "tour.lomt",                # 同上；同时带着 `Entry`/`Kind` 两张大表
    "selfcheck.lomt",
    "switch.lomt",              # 开关取值要进对象: `switches` 那一格
    "toolchain.lomt",
    "user_hello.lomt",
    # ---- 带 `use` 的（`imports` 那一格 + **根/依赖的边界**）
    "ahci.lomt",                # 一个依赖
    "allocator.lomt",           # 一个依赖 + const
    "native_chain.lomt",        # **链式泛型**（泛型函数体里再调泛型函数）—— 2026-09-23 起
                                # 不拒了：`outer<T>` 的实例 `outer_u32` 的体里那句
                                # `pick(a, b)` 推得出 `pick_u32`（M6 改成跑到**不动点**，
                                # 与参考实现那 8 轮同构）。实例的**创建顺序**也要对上。
    "fuc_node.lomt",            # 一个依赖
    "lumtui_demo.lomt",         # 五个 `use` —— 依赖的**传递闭包**共 6 份（`lumtui_font`
                                # 是被 `lumtui_layout` 拉进来的），顺序也要对上
)

#: **点名拒绝**的那些（文件 -> 拒绝话里必须出现的那个轴的名字）。
#: 每一条都对应 `loment/selfhost/potato.lomt` 头上写的那几条边界。
#:
#: **2026-09-23 起这里是空的** —— 原来唯一那条是 `native_chain.lomt`（链式泛型）。
#: 它现在**不拒了**：自举侧的 M6 改成了**跑到不动点**（走单元里的非泛型声明，再逐轮走
#: 新建**实例的体**，形参类型按实参换掉），于是 `outer_u32` 的体里那句 `pick(a, b)`
#: 推得出 `pick_u32` —— 与参考实现那 8 轮同构。它进了 `COVERED`，逐字节相同。
REFUSED: dict[str, str] = {}

#: 连**检查**都还没过的（与这一格无关，但必须有一格，否则"没做决定"那条判据会把它当成漏网）。
SKIPPED = {
    "native_raii.lomt": "自举检查器还不认 `Drop`（trait/impl 那一格之前的事）",
}

#: `loment/examples` 之外再点几份 —— 它们各自钉住一处在示例里**覆盖不到**的边界。
EXTRA_COVERED = (
    # 常量值的量级。`num.lomt` 的 `4294967296` 与 `hash.lomt` 的 `14695981039346656037`
    # 都**装不进 u32** —— 这一格第一版按 u32 累加，就在这里发出了回绕过的数（`value: 0`）。
    # 这三份是那次实测的**回归钉子**，不是随手挑的语料。
    "lompi/store/std/0.1.0/num.lomt",
    "lompi/store/std/0.1.0/hash.lomt",
    "lompi/store/std/0.1.0/f64bits.lomt",
    # 工具链核心库（`use <名字>` 两边的名字冲突规则就是它们定的）
    "loment/lib/num.lomt",
    "loment/lib/mem.lomt",
    # 自举侧自己的 IR 单元：`[u8; N]` / 切片 / 大段 const 都有
    "loment/selfhost/ir_mem.lomt",
    "loment/selfhost/ir_for.lomt",
    "loment/tools/lomsyscalls.lomt",
    # ---- 带依赖的库与工具（`imports` 那一格在真实规模上的样子）
    "lompi/store/host/0.1.0/fs.lomt",       # 一个依赖
    "lompi/store/std/0.1.0/text.lomt",      # 两个依赖 —— `imports` 的顺序要按依赖序
    "loment/tools/lomcli.lomt",             # 三个依赖、31 KB 的对象（这一格最大的语料）
    "loment/lib/lumtui.lomt",               # 11 KB、一大批 const 与 struct
    # `addin` 拉进来的开关设定：这一份是**仓库级 sweep 抓出来的**——
    # 参考实现走 `load_unit`（带开关预扫）时 `switches` 里有 addin 那条定义，
    # 而直接 `load()` 只有根自己那份。它钉住"这一格是**整个程序**的表"。
    "loment/examples/addin/main.lomt",
)

#: 语料之外单独点的拒绝轴：`comefor` 与外部代码块都由**驱动器**拒（那不是这一格的判断，
#: 但必须是"点名拒绝"而不是"发个壳出去"）。
EXTRA_REFUSED = {
    "loment/comefor/def_dialect.lomt": "comefor",
    "loment/extblock/evil.lomt": "外部代码块",
    # `choose write grammar python` 那种源：自举侧的前门本来就收不了（docs/188 §7.1），
    # 与这一格无关，但"收不了"也要看得见。
    "loment/lib/lumtui_math.lomt": "grammar",
    # **嵌套泛型实参**（`Outer<Inner<u32>>`）。这一条是 2026-09-23 补的**夹具**，
    # 补的时候发现的事比夹具本身重要：**两边都没有** —— 参考实现发出一个非法实例名
    # （`Outer_Inner<u32>`）然后被它自己的自检拒掉，自举侧是点名拒。
    # 下一条判据（`test_nested_generic_is_refused_by_both`）钉住"参考侧也过不去"那一半。
    "loment/examples/nested_gen/main.lomt": "嵌套泛型",
}

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _stage1() -> Path | None:
    """种子 -> stage1（走发行包构建的同一条路）。seed 不新鲜时由 `_assert_seed_fresh` 报。"""
    try:
        import loment_dist  # noqa: PLC0415
        return loment_dist.build_stage1()
    except Exception as e:  # noqa: BLE001
        print(f"      SKIP: 拿不到 stage1 ({type(e).__name__}: {e})")
        return None


def _run(stage1: Path, rel: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(stage1), rel, "--emit-potato"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False, timeout=300)


def _want(rel: str) -> str:
    """参考实现给这份源发的对象 —— **走 CLI 那一个入口**（`lomentc.load_unit`）。

    两处不这么走就会**算的是另一件事**：

    * `load_unit` 里带**开关预扫**（`prescan_switches`）。直接 `load()` 的话
      `mod.switches` 只有**根自己那份**，而 `addin` 拉进来的开关设定不在里面 ——
      `loment/ examples/addin/main.lomt` 就是这么被扫出来的（参考 `[]`、
      自举侧有那条定义，两边"都算出了对象"却不是一个）。
    * `lom_root` 是 `--lom-root` 的默认值（**仓根**，不是 `loment/`）：`use "lom/fujr.lom"`
      那种 L0 布局是相对仓根找的，传错了会让 `layouts` 那一格静静地变成空表。
    """
    mod, deps = lomentc.load_unit(ROOT / rel, ROOT)
    return lomentc.emit_potato(mod, ROOT, deps)


@test
def test_seed_is_not_stale():
    """判据跑的是种子链成的 stage1 —— 种子过期就**先红这里**，别让绿落在旧二进制上。"""
    from loment_seed import reference_ir  # noqa: PLC0415
    seed = (ROOT / "loment" / "build" / "selfhost_driver.ll").read_text(encoding="utf-8")
    want = reference_ir()
    assert seed == want, (
        f"种子过期：盘上 {len(seed)}B vs 参考 {len(want)}B —— 跑 "
        f"`python tools/loment_seed.py --emit`（改了 driver 链就必须跟上）"
    )
    print(f"      种子与参考逐字符一致（{len(want)}B）")


@test
def test_potato_emit_matches_reference():
    """语料上逐字节相同 —— 这一格就是它。"""
    stage1 = _stage1()
    if stage1 is None:
        return
    ok = 0
    for name in COVERED:
        rel = f"loment/examples/{name}"
        want = _want(rel)
        r = _run(stage1, rel)
        assert r.returncode == 0, f"{name}: stage1 退出码 {r.returncode}，stderr={r.stderr[-300:]}"
        got = r.stdout
        if got != want:
            g, w = got.splitlines(), want.splitlines()
            i = next((k for k in range(min(len(g), len(w))) if g[k] != w[k]),
                     min(len(g), len(w)))
            raise AssertionError(
                f"{name}: 第 {i + 1} 行不同\n  盘: {g[i] if i < len(g) else '<eof>'!r}\n"
                f"  参: {w[i] if i < len(w) else '<eof>'!r}")
        ok += 1
    for rel in EXTRA_COVERED:
        want = _want(rel)
        r = _run(stage1, rel)
        assert r.returncode == 0, f"{rel}: stage1 退出码 {r.returncode}，stderr={r.stderr[-300:]}"
        got = r.stdout
        if got != want:
            g, w = got.splitlines(), want.splitlines()
            i = next((k for k in range(min(len(g), len(w))) if g[k] != w[k]),
                     min(len(g), len(w)))
            raise AssertionError(
                f"{rel}: 第 {i + 1} 行不同\n  盘: {g[i] if i < len(g) else '<eof>'!r}\n"
                f"  参: {w[i] if i < len(w) else '<eof>'!r}")
        ok += 1
    print(f"      {ok} 份单元的形式对象与参考实现逐字节相同")


@test
def test_out_of_subset_is_refused_by_name():
    """子集外必须**点名拒绝**：退出码 1 + 话里出现那个轴的名字。"""
    stage1 = _stage1()
    if stage1 is None:
        return
    n = 0
    for name, axis in REFUSED.items():
        rel = f"loment/examples/{name}"
        ref = None
        try:
            ref = _want(rel)
        except Exception:  # noqa: BLE001
            pass
        if ref is None:
            # 参考实现自己都发不出来（那这一份就不该挂在"拒绝"名下）—— 挪去 SKIPPED。
            raise AssertionError(f"{name}: 参考实现发不出对象，它不属于 `REFUSED`（挪到 `SKIPPED`）")
        r = _run(stage1, rel)
        assert r.returncode != 0, f"{name}: 在子集外却发了对象（静默的那种错）"
        assert axis in r.stderr, f"{name}: 拒绝话里没有轴 {axis!r}：{r.stderr[-200:]!r}"
        n += 1
    for rel, axis in EXTRA_REFUSED.items():
        r = _run(stage1, rel)
        assert r.returncode != 0, f"{rel}: 在子集外却发了对象"
        assert axis in r.stderr, f"{rel}: 拒绝话里没有轴 {axis!r}：{r.stderr[-200:]!r}"
        n += 1
    print(f"      {n} 份子集外的单元各自点名拒绝（没有一个静默发出去）")


@test
def test_nested_generic_is_refused_by_both():
    """**嵌套泛型实参**（`Outer<Inner<u32>>`）：**两边都过不去**，只是失败方式不同。

    这一条钉的是"**这不是自举侧的单方面缺口**"。`docs/205` 原先把它记成"参考实现那 8 轮
    迭代才削得干净" —— 2026-09-23 补夹具时实测更正：那 8 轮削的是**链式**（走*实例*的体），
    嵌套**类型实参**参考实现自己也发不出来 —— 它拼出的实例名 `Outer_Inner<u32>` **不是合法
    标识符**，于是被它**自己的**形式对象自检拒掉（`types[1].name 非法`）。

    三种形状试下来崩法一样（局部 + 字面量 / 只要类型注解 / 非泛型 struct 的字段），
    所以不是夹具挑得怪。要往前走，先得定**嵌套实例怎么命名**（`Outer_Inner_u32` 会与两实参
    的 `Outer<Inner, u32>` 撞名，所以需要一个分隔约定）—— 那是**格式层**的改动，
    两个实现 + 形式对象校验器 + `docs/147` 要一起动。
    """
    p = EX / "nested_gen" / "main.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    assert not lomentc.check(mod, deps=deps), "夹具本身要能过检查 —— 它只出界在形式上"
    got = None
    try:
        m2, d2 = lomentc.prepare(mod, deps)
        lomentc.emit_potato(m2, ROOT, d2)
    except Exception as e:  # noqa: BLE001
        got = str(e)
    assert got is not None, (
        "参考实现**也**发不出嵌套泛型的形式对象 —— 它现在能发了？那这一格要重判"
        "（自举侧就不该再拒，得改成逐字节对上）")
    assert "非法" in got, f"参考侧的失败方式变了（不再是自检拒非法名）：{got[:200]}"
    print("      嵌套泛型：参考侧发非法实例名被自检拒 / 自举侧点名拒（**两边都不支持**）")


@test
def test_every_example_is_decided():
    """`loment/examples` 下**每一份**都得有一个说法：覆盖 / 拒绝 / 跳过。

    没有这一条，这一格的"绿"会被**新增一份没测到的示例**悄悄稀释 —— 而这一格最怕的
    正是"少测了却不知道"。新加一份示例时必须在这里显式表态。
    """
    decided = set(COVERED) | set(REFUSED) | set(SKIPPED)
    have = {p.name for p in EX.glob("*.lomt")}
    undecided = sorted(have - decided)
    assert not undecided, f"这些示例还没被这一格表态（加进 COVERED/REFUSED/SKIPPED）: {undecided}"
    stale = sorted(decided - have)
    assert not stale, f"这些名字在列表里但文件没了: {stale}"
    missing = sorted(m for m in SKIPPED if m not in have)
    assert not missing, f"SKIPPED 里有不存在的文件: {missing}"
    print(f"      {len(have)} 份示例全部有说法（{len(COVERED)} 覆盖 / "
          f"{len(REFUSED)} 拒绝 / {len(SKIPPED)} 跳过）")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_potato_emit_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
