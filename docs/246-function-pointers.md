# 246 · 函数指针与间接调用：线 B 的规范与落点

> 状态: **规范；实现停在参考侧（未合）**（2026-10-14）· 判据: 待落地 · 门禁: `ci.py --static-only`
> 本文按 `docs/176` §7.3 的规矩先立规范与落点 —— **没有落点的那条，就是这一改动的真实代价**。
> 线 A（`thread_spawn`，默认可用、不动任何既有保证）已经落地，见 `docs/245`。
> 线 B 的**拦路石**在 §2 末尾（自举符号表不存形参类型）—— 读过那一段再动手。

## 0. 这是哪一条线，为什么要单独一笔

用户 2026-10-13 的裁决是**两条线，用 `choose` 开关分家**。线 A 走的是"入口是**编译期的
名字**、产物里一条**直接调用**"，所以静态调用图一个字没动。这一份是**线 B**：真的
**函数指针** + **间接调用**，整体挂在开关下。

它买到的：`thread_spawn` 的"入口名必须是本单元里写着的函数"那条限制松开；qsort 那种
"比较函数"的接口写得出来；一张分派表（跳转表）写得出来。

它要付的代价，**两条，都必须在明处**（探索于 2026-10-13，此前没人记过）：

### 代价一：它是当年**刻意排除**的

`docs/232:179-190`（§3.3）：本语言没有函数指针，所以 `qsort` 的第四个参数是 `kind`
而不是比较函数 —— 它把"按什么比"从**运行期的一个地址**变成**产物里的一个常量**，于是
"这个排序按什么比"是**不读源码可判的**。那是这个项目反复看重的性质
（同族：`gc_ladder`、端口表、`docs/210` §3）。

**开关一开，这条性质就在那一份产物里让位** —— 所以它不是"顺手加个特性"，是把一条已经
写在账上的性质换掉。不写清楚就做，等于悄悄把账做平。

### 代价二：它会掀掉承重门禁的前提

`docs/208:188-220`：**静态调用图**是 `closed` 与 GC 可达性两条门禁的前提。
间接分派/间接跳转一进来，那两条的前提要**重写**（不是"再加一条判据"，是"原来那条
不再成立"）。

## 1. 形状

```rust
choose fp;                                   // 开关（发行版里是编译错误，见 §4）

fn add(a: u64, b: u64) -> u64 { return a + b; }
fn mul(a: u64, b: u64) -> u64 { return a * b; }

fn apply(f: fn(u64, u64) -> u64, a: u64, b: u64) -> u64 {
    return f(a, b);                          // 间接调用
}

fn _start() {
    let p: fn(u64, u64) -> u64 = fn_addr("add");
    let q: fn(u64, u64) -> u64 = fn_addr("mul");
    let r: u64 = apply(p, 3, 4) + apply(q, 3, 4);
    ...
}
```

三件东西：

| | |
|---|---|
| 类型 | `fn(参数类型列表) -> 返回类型` —— **Loment 自己的调用约定**（实参走栈） |
| `fn_addr("名字")` | 取一个本单元里函数的地址；返回类型**就是那个函数的 `fn(...)` 类型** |
| 间接调用 | `p(a, b)`：`p` 是一个 `fn(...)` 类型的**值** |

### 1.1 调用约定必须在类型里（这一条最容易被忽略）

`docs/158:39`：**调用约定不是统一的** —— Loment 内部函数走**栈**（每标量 8 字节，
`tools/lomelf.py:16-17`），`extern fn` 走**平台 C ABI**（`docs/173`）。所以"一个
`fn(…)` 值"**没有唯一的约定**：同一个类型名底下可能是两种完全不同的调用序列。

**v1 的裁决：`fn(...)` 只表示 Loment 内部约定**。指向 `extern fn` 是**编译错误并点名**
（`fn_addr("malloc")` → "extern 函数走平台 ABI，本语言的 `fn(...)` 表示的是内部约定"）。
不做第二个类型名，理由是**今天没有消费方**：真要用 C 回调，先要有 C 面的类型系统
（`docs/205` C2 那一档），那时再一起设计。**把说不清的那一半挡在门外，比给它编一个
名字好。**

### 1.2 `fn_addr` 的实参是**字面量**，与 `thread_spawn` 同一条理由

产物里那是一条 `ptrtoint ptr @add to i64` —— 符号必须在**编译期**定下来。写成变量只会在
链接期变成"未定义的符号"，对写代码的人没有信息量。三条检查也因此在编译期做得了：
是字面量、名字合法、**本单元里真有这个函数**（`thread_spawn` 那三条的同一套机器）。

## 2. 落点表（`docs/176` §7.3：两个实现各找一个落点）

**行号是 2026-10-14 在 `feat/threads` 上核过的**（`docs/245` 落地之后）。

| 规则 | 参考侧 `tools/lomentc.py` / `tools/lomelf.py` | 自举侧 `loment/selfhost/` 与 `loment/tools/` |
|---|---|---|
| 类型语法 `fn(…) -> T` | `Parser.type_name :1666`；`_type_ok :645`；`TYPES :31` | `checker.lomt chk_ty_of_tok :136` / `chk_ty_range :247` / `chk_ty_eq :223`；`chk_ty_new :180`（复合表）；`parser.lomt put_type` |
| 类型相容 | 字符串 `==`（`expr_type :2779`）；`_type_ok` 认不认这个形状 | `chk_ty_eq :223`（**新 TY kind**，payload 指向一张实参类型表） |
| `fn_addr(name)` | `BUILTINS :34`；`_walk_expr :2900` 的内建分支（三条检查照抄 `thread_spawn` 的）；`_Ir.expr` 发射 | `is_builtin :520` 的名字串；`chk_builtin_arity` / `chk_builtin_arg` / `chk_builtin_ret`；`chk_tex_atom_call :1707`；`codegen.lomt expr_atom` |
| 间接调用 | `_walk_expr :2900`（`Call` 的分派：callee 是 `fn(...)` 类型的变量时走这条）+ `_Ir.expr` | `chk_tex_atom_call :1707`；`codegen.lomt expr_atom` |
| 两个链接器 | `tools/lomelf.py:1092` 那条 `Unsupported("v0 不支持间接调用")` 换成真实现 | `loment/tools/lomelf.lomt` 的同一条 |
| `choose fp` 的闸 | `CORE_DIMS :1277` 加第六维 + `CORE_DIM_ORDER :1304` + `CORE_DEFAULTS :1310` + `CORE_DIM_ZH :1314`；闸的样板在 `check()` 里 `_eff["vm"] == "vm" and loment_channel.RELEASE`（`:3939`） | 同名的那几张表 + `driver.lomt` |

**间接调用在栈机上怎么落**（`lomelf` 是**不碰对齐、不做寄存器分配**的栈机）：实参照旧
一个个 `push`，**callee 的地址在 push 完之后读进 `rcx`**（push 不碰 `rcx`），最后
`call rcx`（`FF D1`）。先读 callee 再 push 不行 —— `get` 用 `rax` 求值，而 `push` 也用它，
两条会互相踩。

**自举那侧的坑（试做之后查出来的，比预想的多一层）**：

1. **`chk_ty_eq :223` 比的是"编码相等"，而 `fn(...)` 是复合类型** —— `chk_ty_new :180`
   那张记录只有**两个 u32 字段**。`fn(A, B) -> C` 塞不进去，得改成**链表**：
   `TY_FN` 一格 = `(实参链, 返回)`，实参链 = `TY_ARGS` 一格 = `(元素类型, 尾巴)`，
   尾巴以一个哨兵收尾（空实参表也要能表示）。`chk_ty_eq` 与"取第 k 个实参"都跟着递归。
   表写漏了不会报错，只会让两个类型**看起来不等** ⇒ 合法代码报类型错。
2. **`fn_addr` 的返回类型算不出来** —— 这是**真正的拦路石**。参考实现算它靠的是
   `funcs[name].params`（形参类型表）；而自举的符号表（`declare :578`，stride 20）
   **只存了返回类型（+12）与形参个数（+8），没存形参类型**。于是
   `let p: fn(u64, u64) -> u64 = fn_addr("add");` 在自举侧只能给"未知"，而未知与任何
   非 0 的类型**不相等**（`chk_ty_eq` 的 `a == 0 || b == 0` 那条）—— 参考接受、自举拒绝。
   **两个实现就此分家。**

   要修就得**动自举符号表的布局**（stride 20 → 24，或另开一张形参类型表），而那要过
   `docs/200` 那条"表是静默溢出、越界写邻居"的纪律：动之前先量 `TB_G` / `TB_LBL` /
   `TB_FIX` / `IN_CAP` 的余量。**这是一笔单独的、有容量风险的改动**，不该塞进这一笔
   顺手做掉 —— 所以 `docs/246` 这一份**先停在规范**。

**结论（2026-10-14）**：线 B 在**参考侧**已经跑通（`fn(...)` 类型 + `fn_addr` + 间接调用
+ `lomelf` 的 `call rcx`，一个 `apply(p,3,4) + apply(q,3,4) == 19` 的探针端到端过了），
但**没有落进树**：按上面第 2 条，自举侧现在做不出同一个判定，而"两个实现必须一致"是
这一行的硬约束 —— **半条线不许合**。

## 3. 消费方清单（`docs/182` §1.9：**每个读 L1 源的入口各有一条判据**）

新**类型**进来之后要过的面（与 `docs/245` §4 列的那批**同一批**，但那次只加了内建，
这次还要动**类型**）：

* **两个 LSP**：`tools/loment_lsp.py:25` / `loment/tools/lsp.lomt:946` 各带**自己的**
  关键字与类型表，**绕过 `lomentc.load`** —— 新类型对它们不可见，且**静默**（`docs/182` §1.9.2）。
* **`lomfmt` 两份**：`tools/lomfmt.py:35 TOP_KW` / `loment/tools/lomfmt.lomt:72`（逐字节对拍）。
* **`lomdoc` 两份**：`lomdoc.py:31 _sig`；孪生 `lomdoc.lomt:293 ptype` **只认三种形状**
  —— `fn(…) -> T` 是**第四种**，不补它会把签名的类型印成空。
* **`lomcli` 五张手写表**：`syntax` / `builtins` / `types` / `keywords` / `cheat`
  —— 判据只要求"能跑"，**漏一个是静默的**（`docs/182:338`）。
* **七门表层语法的翻译器**：`tools/trans_core.py:519` 现在**明确拒绝** `int (*fp)(int)`。
  这一门是**双向**的：Loment 的 `fn(A) -> B` 要能译出去，C 的 `int (*fp)(int)` 要不要译
  进来是**另一个裁决**（v1 只做出去，进来的那条留空并写清楚）。
* **`potato` 的词法计数器**：`pt_cnt_boundary_call` 按 `NAME (` 匹配 —— 间接调用 `p(x)`
  会被当成普通调用计数。**要审**：`fn_addr` 与间接调用都是"越过语言保证的每一步"，
  但 `BOUNDARY_BUILTINS` 今天只分两族（见 `docs/245` §7）。
* **编辑器**：`editors/vscode/syntaxes/loment.tmLanguage.json` 的 `#types` / `#fn-signature`
  + `editors/vim/syntax/loment.vim`；判据 `vim ⊆ TextMate` + 孪生逐字节。
* **`lompotato.lomt chk_types`**：产物侧的类型名集合，新类型名不进去产物会被拒。

## 4. 开关（`choose fp`）

与 `choose vm` **同一条渠道闸**（`docs/231` §2 —— 读同一个渠道常量
`loment_channel.RELEASE`，打包期只换那一行）。**发行版里 `choose fp` 是编译错误并点名**，
所以默认产物保持"静态调用图"那条保证，而"这份产物有没有间接分派"落在 **Potato 产物**里
—— 仍然是**不读源码可判**的。

加第六维要动的表（`tools/lomentc.py`）：`CORE_DIMS :1277`、`CORE_DIM_ORDER :1304`、
`CORE_DEFAULTS :1310`（默认 `no_fp` —— 默认档不许改变任何现有程序的行为）、
`CORE_DIM_ZH :1314`；自举侧是**同名的那四张**。判据：`loment_channel_test` 两端都测。

## 5. 分阶段（每一步自成一次可 review 的提交）

1. **规范**（本文）。
2. **类型 + `fn_addr` + 间接调用**：两个编译器 + 两个链接器；`choose fp` 的闸；
   `docs/208` 那两条门禁前提**改成带开关的模式**；`loment_rule_parity._CASES` 加最小负例
   **并抬高 BUDGET**（`docs/158` §5 四步：只调低 BUDGET 是放松门禁）。
3. **消费方清单 §3 逐条过**（每个入口一条判据）。
4. **文档与生成物**：`docs/208` 的门禁前提重写 + 两条提交（手写 / `--emit`）。

## 6. 约束（这一笔必须守的）

1. `docs/158` §5：两个实现在**同一次提交**；判据 `loment_rule_parity` 与
   `loment_tools_test::test_m81_builtin_tables_match` 两边都过。
2. `docs/176` §4 A：**语言面改动两条提交**（手写 / `--emit` 产物）。种子
   `loment/build/selfhost_driver.ll` 重算，不手工合并。
3. `docs/176:10` 的量：一次语言面改动里 **93% 是机械产物**。手写那条**必须能独立 review**。
4. 门禁口径是**不新增红**（`.github/gate-baseline.txt`）。
5. **不碰 `lom/*.lom`**（L0 跨线契约）；这一笔在 L1。

## 7. 与 `docs/245` 的关系

线 A 已经落地（`docs/245`）。这一笔**不改线 A 的形状**：`thread_spawn` 的入口仍然是
**编译期的名字**，它的"入口必须是本单元里写着的函数"那条限制，在 `choose fp` 开着时
可以**再放开**（届时入口也可以是 `fn(...)` 的值）—— 但那是**这一笔之后**的事，不在
第 2 步里。先把函数指针本身做出来。
