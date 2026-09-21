# 199 · std 真正可用：包内模块按名字可达 + 平名字冲突收口

> 2026-09-20。用户："现在彻底修复 std 问题"。上游：`docs/168`（库系统）· `docs/170`
> （lompi 与 store）· `docs/143` §2（装载规则）· `docs/158` §5（改冻结面的四条流程）。
>
> 本文件是**执行记账**：先说清"std 问题"到底是什么（三层，不是一层），再说改了什么、
> 判据落在哪、以及**顺带撞出来的两个既有 bug**。

## 0. 一句话

**标准库躺在包里，而包只有"整包入口"一个出口 —— 那个出口又把 128 个模块拖进同一个
单元，1836 个顶层名字挤在平的发射符号空间里。** 于是 std 既看不见（文档三处说法互相矛盾）
也够不着（`use vec` 解析不到、`use std` 被 11 处重名挡住）。这一轮把出口补成"按模块
可达"，并把撞名修掉。

## 1. 量出来的现状（改之前）

| 事实 | 数字 |
|---|---|
| `lompi/store/std/0.1.0/` | **127** 个模块 + `std.lomt` 门面；`pub fn` 共 **1356** 个 |
| `lompi/store/host/0.1.0/` | 7 个模块（syscall / fd / argv / 目录 / stat / 日志） |
| `use vec` / `use fs` / `use numfmt` | **E018 找不到** —— 解析器只找 `<名字>/<版本>/<名字>.lomt` |
| `use std`（门面） | **11 处 E13 平名字冲突**（见 §3） |
| 文档 | README 只提 `loment/lib/` 的 5 个；`SKILL.md` §3 写"**没有标准库**"；`std.lomt`
  自己的头注劝人别用 `use std`，理由是"打包版编译器最多 8 条 use、菱形会崩" |
| 自举编译器编译 127 个模块 | **超线性**：n=4 → 3.8s，8 → 6.5s，16 → 11.7s，32 → 40.9s，
  64 → 187s；127 个模块是几十分钟量级（不是死循环，是复杂度） |

后两条是**同一个原因的两面**：门面把整包塞进一个单元，既撞名又超线性。

## 2. 出口：包内模块按名字可达（装载规则改动）

`use <名字>` 从四层变六层（`docs/143` §2 已重写）：

```
①  <项目根>/deps/<名字>/<名字><后缀>            项目本地（包入口）
①b <项目根>/deps/<包>/<名字><后缀>             项目本地、包内模块      ← 新
②  内置四根 loment/{lib,examples,selfhost,tools}/<名字><后缀>   要求命中唯一
③  <store>/<名字>/<版本>/<名字><后缀>           随包自带（包入口）
③b <store>/<包>/<版本>/<名字><后缀>             随包自带、包内模块      ← 新
```

`<store>` 也是**两个候选**（按序）：装出来的前缀 `<工具目录>/../share/lompi/store`，以及
**开发 checkout 的 `<仓根>/lompi/store`** —— 仓里的 store 不摆 `share/` 那一层（那是打包时
拷出来的）。没有后一条，在本仓写 `use vec` 一律 E018，而"本仓能不能用 std"正是要回答的。

### 2.1 §归属：②与③谁在前面，看导入方住在哪边

这一条**不是设计出来的，是撞出来的**。两个方向都有真东西：

| 情形 | 后果 |
|---|---|
| ③（商店）放②前面 | `loment/selfhost/comefor.lomt` 的 `use interp` 拿到 `store/std/0.1.0/interp.lomt`，而那份没有 `CT_HEAP_BYTES` —— **驱动直接编不过** |
| ②（四根）放③前面 | `store/host/0.1.0/stat.lomt` 的 `use mem` 拿到 `loment/lib/mem.lomt`，而那份没有 `mem_load64` —— **host 包编不过** |

所以规矩是"**在谁的库里，先用谁的名字**"：导入方自己住在 `<store>` 之下时先搜③，其余文件
先搜②。用户项目走的是"先②"那条 —— 与加这一层之前**逐字节一致**：这一层没有改动任何既有
解析结果，它只补上了原先够不着的名字（`interp` 仍是 `loment/selfhost/interp.lomt`，
`mem` 从用户项目看仍是 `loment/lib/mem.lomt`）。

### 2.2 命中多处要报错

①b/③b 与②同一条纪律：两个包里都有 `<名字>.lomt` 时"先搜到哪个"不能变成隐藏语义。
参考实现与自举侧都**硬报错**（自举侧**不能**返回 0 往下落 —— 往下落可能被内置四根里同名
的一份接住，那就成了"静默用错模块"）。

## 3. 平名字：11 处冲突收口

单元的发射符号是**平的**（`docs/158` §2），所以包内任意两个模块的同名顶层声明都是硬错。
127 个模块、4585 个顶层声明里撞了 11 处：

| 名字 | 撞在哪 | 改后 |
|---|---|---|
| `cfg` | ini ↔ toml | `i_cfg` / `t_cfg` |
| `sc` | toml ↔ bufio | `t_sc` / `bi_sc` |
| `hv` `hx` | mpk ↔ cbor ↔ asn1（三家） | `m_*` / `cb_*` / `a_*` |
| `mk` | rational ↔ udiff | `ra_mk` / `ud_mk` |
| `eqn` | hexdump ↔ udiff | `xd_eqn` / `ud_eqn` |
| `bl_bytes` | blit ↔ bloom（都是 `pub`） | bloom 改成 `bm_bytes`（与它自己的私有前缀一致） |
| `RA_ERR` `RA_NO` | raster ↔ rational（都是 `pub const`） | raster 改成 `RASTER_ERR` / `RASTER_NO` |

改名一律**贴各模块已有的私有前缀**，不改语义。改完 `use std` 的门面**装得进一个单元**了
（参考实现 5.6 秒、7.7 MB IR）—— 判据钉住"它现在装得下"（见 §4）。

### 3.1 host 包里那三处 `use std`

`host/{io,log,stat}.lomt` 原先 `use std`（把 128 个模块拖进来，只为拿 `numfmt`/`out`/`mem`
里的一两个函数）。改成**按模块取**：`use numfmt` / `use out` / `use mem` —— 这同时是②§归属
那条规矩的第一个消费者（它们住在商店里，所以拿到的是 std 包的那几份）。

## 4. 判据

装载器规则走 E018 那条路（**不进** `loment_rule_parity`，`BUDGET` 不动 —— 套件喂的是
自包含单文件，结构上装不下"解析一个导入名"，`docs/158` §5 记过这条处理方式）。

参考实现（`tools/lomentc_test.py`，+5）：

- `test_name_import_reaches_a_module_inside_a_package` —— `use area` 落到
  `deps/geom/area.lomt`；同时钉住"包入口那条路没被挤掉"（`use geom` 仍命中 `geom.lomt`）。
- `test_name_import_package_module_ambiguous_is_rejected` —— 两个包都有 `area.lomt`
  **必须报错**，且消息里点出是哪两个包。
- `test_store_modules_resolve_by_name_in_this_checkout` —— 本仓就是 store 消费者：
  `use vec` / `use std` / `use fs` 各自落到哪儿。
- `test_store_packages_do_not_shadow_the_built_in_roots` —— `interp` / `mem` 仍是四根那份，
  **并断言商店里确实存在同名的包内模块**（否则这条判据测的是空气）。
- `test_std_package_is_one_unit_without_name_collisions` —— std 门面在平名字空间里
  **零冲突**，且装进来 >100 个模块。**这是给后来人看的棘轮**：往 std 里加模块时撞名，
  门禁当场红，而不是等某个用户 `use std` 才发现。
  **只在参考实现这一侧跑** —— 自举侧装载同一份语料是几十分钟量级，拿它当判据会把门禁
  变成等待；"重名要拒"这条**规则**的等价性由 `loment_p8_test` 的 `neg_across` 与
  `loment_rule_parity` 管，这里管的是**语料**。

自举侧（`tools/loment_lompi_test.py`，+2，都逐字节比参考的 IR）：

- `test_selfhost_reaches_a_module_inside_a_package` —— `deps/geom/area.lomt`。
- `test_selfhost_reaches_a_module_in_the_dev_checkout_store` —— `<CWD>/lompi/store/...`。

**验过它会红**：把 `bloom.lomt` 里的 `bm_bytes` 改回 `bl_bytes`，
`test_std_package_is_one_unit_without_name_collisions` 当场报"平名字撞了"。

## 5. 两个既有 bug（这一轮**顺带撞出来**，都不是本轮的改动引起的）

### 5.1 混合宽度算术产出的 IR，LLVM 校验器不收

```rust
pub fn sh(a: u64, n: u32) -> u64 { return a >> n; }
```
参考实现发出 `lshr i64 %t1, %t2`，而 `%t2` 是 **i32** —— `clang -c -x ir` 直接
`error: '%t2' defined with type 'i32' but expected 'i64'`。`add` / `shl` 同病。
**两个实现一致**（所以逐字节判据抓不到它），`lomelf` 也照收（自举链不走 clang），
所以正常工具链路径**能跑**；受影响的是 clang 那条路（`-O1` 构建、调试器的 `clang -g`）。
`use std` 的 7.7 MB IR 里这一类错误只有一种（第一个报的就是它）。
**修它要定语义**（C 那样放宽 vs. Rust 那样要求同宽 `as`），属语言规则 → 没动，单独报告。

**它与 `docs/158` §4 的第 11 条同源**（那条记的是**比较**：`u32 == u8` 参考只提升左边，
产出非法 IR）。这一轮撞到的是**移位/算术**那一半（`a >> n`、`a + n`，左边 u64 右边 u32），
同一条根因、同一类后果；第 11 条的"现在的绕法"（两边显式对齐宽度）对移位同样适用。

### 5.2 整数字面量的类型：参考 i64、自举 i32

```rust
pub fn f() -> u64 { return 3 * 4294967296 + 5; }
```
参考发出 `mul i64`，自举发出 `mul i32`；`x == <同一个字面量>` 时参考 `icmp eq i1`、
自举 `icmp eq i32`。**用 HEAD 的种子（未改动的自举）跑同一份源码，差异一模一样** ——
既有分歧，与本轮无关。它只在 `dir` / `argv` / `log` / `stat` 这几个**此前根本够不着**的
模块上露出来（语料里没有这个形状，所以 `loment_p8_test` 一直是绿的）。
`vec` / `map` / `numfmt` / `out` / `mem` / `io` 六个模块两边**逐字节相同**，上面那两条
自举判据就用这些。

## 6. 不主张 / 已知红

- **不动 `D:\Dev\Lolment-ku\store`**（lompi 标准库的仓外正本，不在 git 下）—— 用户 2026-09-20
  明确"先不动仓外"。所以 `loment_lompi_test::test_dev_copy_and_repo_copy_do_not_silently_drift`
  现在**红**：仓内 14 个文件（`std` 11 + `host` 3）领先于正本。**这是知会 lompi 线的事**，
  不是本仓的回归；同步回去跑 `python tools/lompi_sync.py --to-dev` 即可。
  （已核过：那 14 个文件的正本与"改动之前的仓内版"**逐内容相同**，只差行尾 ——
  推过去不会覆盖任何并行改动。）
- **不动 L0**（`lom/*.lom`）、不动 Potato 形式对象、不动 `loment/lib/` 那四个与 std 同名的
  核心模块（`mem`/`num`/`proc`/`sha256` 两边都有，是既有的布局重叠；本轮只保证
  §2.1 的归属规则让两边各自自洽）。
- **不动 `use std` 的门面语义**：它现在是**正确但慢**的。`docs/175` §1 那条"std 分层"的
  方向不变。

## 7. 改了哪些文件

手写那条提交（`docs/158` 的"手写一条、机械产物一条"分开）：

| 文件 | 改什么 |
|---|---|
| `tools/lomentc.py` | `resolve_name` 六层 + `store_roots` / `_pkg_module_hits` / `_in_store_root`；`resolve_deps` 把导入方目录传下去 |
| `loment/selfhost/driver.lomt` | `store_base` / `try_pkg_module` / `subdir_at` / `in_store_path` / `try_store_layers`；`resolve_use_name` 分归属；暂存布局重排（`SCR_CAP` 17504 → 23904） |
| `lompi/store/std/0.1.0/*.lomt` | 11 处改名（11 个文件）+ `std.lomt` 头注改写 |
| `lompi/store/host/0.1.0/{io,log,stat}.lomt` | `use std` → `use numfmt` / `+ use out` / `+ use mem` |
| `tools/lomentc_test.py` · `tools/loment_lompi_test.py` | +5 / +2 条判据 |
| `tools/loment_diag.py` | E018 的说明卡按新层序重写 |
| `docs/143` · `docs/168` · `docs/158` | 规范 + 记账 |
| `README.md` · `.claude/skills/loment/SKILL.md` | "没有标准库"改成真的（§3.1 新节） |

机械产物那条：`loment/build/selfhost_driver.ll`（`loment_seed.py --emit`）、
`loment/build/SHA256SUMS` 与发布清单、`docs/manual/`。
