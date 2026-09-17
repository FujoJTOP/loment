# 178 · Potato v2：项目模式进形式对象

> 状态: **已实现**（2026-09-17）· 生产者: `tools/lomentc.py --emit-potato` ·
> 校验器: `tools/potato.py` · 判据: `potato_test`（9/9，反例 36 条）+ `lomentc_test`（113/113）
> 一句话: **v2 = v1 + 一个必填字段 `mode`**。它把"这个程序是 std 还是 no_std"变成
> 读者面（Potato）能看见的东西 —— 不读源码、不读二进制。

## 1. 为什么升版本，而不是往 v1 加字段

`mode` 是**必填**的。判据是 `docs/175` §8 那一条：

> **模式进 Potato** | 从形式对象里删掉该字段 → 独立校验器**必须**红

"必填"与"往 v1 加字段"直接冲突：v1 的校验器只检查"没有未知字段"（`potato.py` 的
`未知顶层字段 {k!r}`），往白名单里加一个**必填**键，会让**既有的 v1 对象全部变成非法**。
而 v0/v1 是承诺过能回放的 —— `docs/147` §5、冻结样本 `loment/build/legacy/demo.v0.json`
一直在 `potato_test` 里跑。

所以走 v1 当年走过的那条路（`docs/142` 的 v0 → `docs/147` 的 v1）：

| | 允许的顶层字段 | 必填 |
|---|---|---|
| `v0` | `TOP_KEYS_V0` | — |
| `v1` | `v0` + `traits`/`impls`/`generics`/`instances`/`guards` | `traits`/`impls`/`generics`/`instances` 是数组；`guards` 非负整数 |
| **`v2`** | **`v1` + `mode`** | **`mode` ∈ {`std`, `no_std`}** |

**旧对象不会因为 v2 发布而失效**：校验器接受 `v0`/`v1`/`v2` 三个版本，按对象自带的版本号
选白名单。"未知版本 = 非法"这一条（`docs/147` §5）不变。

## 2. 字段语义

```json
{
  "potato": "v2",
  "unit": "demo",
  "language": "loment",
  "mode": "std",
  "imports": ["mathutil"]
}
```

- **`mode` = 整个程序的运行模式**，取值就是 `choose` 的两个模式名（`docs/143` §3.2）。
- **默认 `std`**：源码里没写 `choose` 就是它。所以对象里**永远显式有两个值之一**，
  不存在"缺这项"的合法形态 —— 这正是 `docs/175` §8 那条判据要的形状。
- **它是根单元自己的值，不是从依赖推导的**：一个编译单元产出一个对象（依赖走 `imports`），
  而 `choose` 只许出现在根单元（库写了就是 E022）。所以这里没有"该听谁的"这种歧义。
- **拼错的模式名进不了对象**：源码那边是编译期的 E022（`docs/158` §5），对象这边是
  校验器的 `mode 必须是 ('std', 'no_std') 之一`。

## 3. 判据

| 判据 | 在哪 | 证伪方式 |
|---|---|---|
| 正向：v2 + 合法 mode 通过 | `potato_test::test_v2_object_validates_with_mode` | 改成 `v1` 白名单 → 红 |
| 反向：缺 `mode` 必须红 | `potato_test` `MUTATORS` 的 `v2 缺 mode` | 去掉必填检查 → 红 |
| 反向：拼错 / 非字符串必须红 | 同上两条 | 同上 |
| 正向：`mode` 跟着源码走 | `lomentc_test::test_mode_follows_choose_and_defaults_to_std` | 写 `choose no_std` 而对象仍是 `std` → 红 |
| 旧对象回放 | `potato_test::test_m51_legacy_v0_replays` | 拿 v0 冻结样本按 v2 校验 → 红 |
| 每个示例都导出一份合法 v2 | `lomentc_test::test_m45_every_example_exports_valid_v2` | 少一个对象 / 版本不对 → 红 |

## 4. 跨线影响（要知会 compat 线）

**这是 Potato 接口的一次升版**，而 Potato 正是内核线消费的那一层（`docs/140` §9.4）。

- **生产侧**：`lomentc --emit-potato` 现在发 `v2`。内核侧若有代码断言
  `"potato" == "v1"`，会红 —— 那是**该看见的信号**，不要靠放宽断言消掉
  （与 `CLAUDE.md` 第 2 条"新增/删除原语 = 契约升版"同一个道理）。
- **消费侧**：`LinuxFUAI/` 那份**独立**校验器需要学会接受 `v2` 并按
  §1 的表选白名单。**本机没有那份仓**，所以这一条**在本仓无法验证** ——
  `docs/175` §8 那条"独立校验器必须红"的判据，本机只能验到"`tools/potato.py` 会红"，
  另一半要等 compat 线那边跑。**记在这里，不假装验过。**
- **回放不受影响**：v0/v1 对象照旧合法。

## 5. 一份产物面之外的东西：生成器

升 v2 时发现**有几件产物只有判据、没有生成器**（`lom_audit` 检查
`selfhost_*.potato.json`，但没有任何工具能重新写出它们）。搬到开发口之后旧树不在了，
这个缺口才露出来。

所以 `tools/lom_audit.py` 补了 `--emit`：把**它本来就会去核对**的那一族生成物写出来。
审计与生成**共用同一次计算**（`generated_wants` / `transpile_wants` / `example_potato_wants`
/ `selfhost_potato_wants` / `cap_asserts_want` / `syscalls_want`）—— 另写一份"产物该长什么样"
就是第二份真相，两份必然漂，而漂法是静默的（判据绿、产物错）。

顺带修掉一处**同族假红**：`loment_build.py` 与 `loment_boot.py` 写产物时没给
`newline="\n"`，默认的平台转换在 Windows 上写出 CRLF，而 `.gitattributes` 定的是 `eol=lf`
—— 于是"重生成的产物 == 仓库里的"这条判据在 Windows 上**必然假红**。
实测：28 个 `.rs` 全报"变了"，逐行看却一模一样（13 个是纯行尾，15 个是根本没提交的构建产物）。
`.gitattributes` 自己记过同族的坑（"IR 逐字节比较会假红"）。
