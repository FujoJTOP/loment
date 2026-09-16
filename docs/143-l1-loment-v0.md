# 143 · L1 Loment v0：语言规范与编译器

> 状态: **已实现并通过自检**（2026-09-08）· 编译器: `tools/lomentc.py`
> 自检: `tools/lomentc_test.py` **18/18** · 示例: `loment/examples/demo.lomt`
> 一句话: **语法是 Rust 的严格子集，只加"能力声明"这一层语义；先转译到 Rust，不写后端。**

## 1. 定位与两条硬约束

L1 是 docs/140 三层架构的语言本体：**人 / Agent 写系统的那一门语言**。

1. **语法不新，语义才新**（docs/140 §4）：表面语法取 Rust 的严格子集——`module` / `use` /
   `fn` / `let` / `if` / `while` / `return` 与 Rust 一致，类型名一致，运算符一致。
   新增的只有 `capability` 声明。这样 LLM 的 Rust 先验可直接迁移，规避 docs/110 §1 的
   "零语料"否决。
2. **先转译，后自举**：v0 的后端是 **Loment → Rust 的转译器**，不是原生后端。
   可行性判据 = **不写后端就能跑**（见 §5 实测）。

Loment 是 `.lom`（L0）的超集：**声明来自 L0，行为来自本文件**。通过 `use "<path>.lom"`
引入布局/常量，编译器把它们内联进产物，并导出到 Potato 形式对象。

## 2. 语法

```
module <ident>

use "<relative .lom 路径>"                                  // L0 布局复用 (可多次)
use "<relative .lomt 路径>"                                 // L1 模块导入: 路径形式 (可多次)
use <名字>                                                  // L1 模块导入: 名字形式 (可多次)

capability <ident> : <space>[<lo>..<hi>] [revocable]        // 能力声明

excluded "<说明>"                                           // 出界声明 (可多次)

const <NAME>: <int-type> = <int> ;                          // 整型常量

struct <Name> { <field>: <type>, ... }                      // L1 原生数据类型

enum <Name> { <Variant>[(<type>)], ... }                   // 变体可带单载荷 (v1)

fn <ident>(<arg>: <type>, ...) -> <type> {
    let <ident> : <type> = <expr> ;
    <ident> = <expr> ;
    if <expr> { ... } [else { ... }]
    while <expr> { ... }
    for <ident> in <lo>..<hi> { ... }
    match <expr> { <Name>::<V>[(<bind>)] => { ... }  _ => { ... } }
    return <expr> ;
    <call> ;
}
```

- 类型: `u8 u16 u32 u64 i8 i16 i32 i64 bool`、已声明的 `struct` 名、定长数组 `[T; N]`
- 表达式: 字面量 / 标识符 / 调用 / 字段访问 `a.b` / 下标 `a[i]` / 枚举路径 `E::V` /
  结构体字面量 `Name { f: e }` / 数组字面量 `[e, ...]` / 一元 `- !` / 二元运算
- 运算符优先级（低 → 高，与 Rust 一致）: `||` · `&&` · `== !=` · `< <= > >=` ·
  `|` · `^` · `&` · `<< >>` · `+ -` · `* / %`
- 注释: `//` 与 `/* */`
- 条件位置禁止结构体字面量（`if p { }` 的歧义按 Rust 的规则消解）

## 3. 语义规则（生成期强制，`lomentc.check`）

| 规则 | 判据 |
|---|---|
| 名字唯一 | 函数名、能力名、参数名不得重复 |
| 定义先用 | 变量必须先 `let` 再使用/赋值 |
| 类型一致 | `let x: T = e` 与 `x = e` 要求 `e` 的类型等于 `T`（整型字面量由上下文定宽） |
| 条件为布尔 | `if` / `while` 的条件类型必须是 `bool` |
| 返回类型 | `return e` 的类型必须等于函数声明的返回类型 |
| 调用存在 | 被调函数必须已定义；实参个数必须匹配 |
| 能力域合法 | `0 ≤ lo ≤ hi` |
| 结构体合法 | 名字不得与基类型同名；字段名唯一且非空；字段类型必须已声明 |
| 结构体字面量 | 字段不得缺失、不得重复、不得未知 |
| 字段访问 | 只能对结构体取字段，且字段必须存在 |
| 常量 | 名字不得与 struct / 函数 / 其他常量重名；类型必须是整型 |
| 数组 | 长度必须为正；数组字面量不得为空、元素类型必须一致、长度须与声明一致；下标只能作用于数组且下标须为整型 |
| 赋值左值 | 只能是变量或数组元素（`a[i] = e`） |
| 枚举 | 名字不得与基类型/struct 重名；变体名唯一且非空 |
| `match` | 主体必须是枚举；模式必须是该枚举的变体；模式不得重复；必须穷尽（覆盖全部变体或带 `_`） |
| 载荷 | 载荷类型必须已声明；有载荷的变体在模式中必须绑定变量，无载荷的不得绑定；绑定变量在臂内具有载荷类型的作用域；构造 `E::V(e)` 只对有载荷变体合法，且实参类型须匹配 |
| `for` | 上下界必须是整型；循环变量类型取上下界的整型类型（全为字面量时默认 `u32`） |
| 模块导入 | **两种写法**。路径形式 `use "a/b.lomt"` 解析相对**仓库根**或**导入文件所在目录**（先前者，后后者）；名字形式 `use 名字` **按层搜、先命中先用**：① 项目本地 `<项目根>/deps/<名字>/<名字>.lomt`（项目根 = 入口文件所在目录；lompi 物化的落点）② 工具链自带的库 `<工具目录>/../share/lompi/store/<名字>/<版本>/<名字>.lomt`（版本那一层**只允许一个**：编译器不做版本选择，多个就报错并指向 `deps/`）③ 内置四根 `loment/lib`、`loment/examples`、`loment/selfhost`、`loment/tools` 下的 `<名字>.lomt`。**只有第③层要求命中唯一**（找不到或多处命中都报错，不静默取第一个）—— 前两层是搜索路径，先命中先用。名字形式**只解析 `.lomt`**：L0 布局只能用路径形式，所以不存在「这个名字算 L0 还是 L1」的歧义；同理，**包内自己目录里的伴生文件要用路径形式**。一个文件里两种写法的**条数上限 300**（合起来算），超过报 E020 —— 超限报错，**不静默丢**。同一模块只解析一次；循环导入报错；本模块声明的符号不得与导入符号重名 |
| 常量可见性 | 模块级 `const` 在函数体内可直接引用 |

## 4. 转译契约（Loment → Rust）

| Loment | Rust |
|---|---|
| `module X` | 注释头 |
| `use "lom/y.lom"` | 内联 `lomc.emit_rust` 的常量块（同一份 L0 单源） |
| `capability c : s[a..b] revocable` | `CAP_C_SPACE/LO/HI/REVOCABLE` 四个常量 |
| `fn f(...) -> T` | `pub fn f(...) -> T` |
| `struct S { a: T }` | `#[derive(Clone, Copy)] pub struct S { pub a: T }` |
| `const C: T = v;` | `pub const C: T = v;` |
| `enum E { A, B }` | `#[derive(Clone, Copy, PartialEq)] pub enum E { A, B }` |
| `enum E { A(u32) }` | `pub enum E { A(u32) }` |
| `E::A(e)` | `E::A(e)` |
| `E::A(x) => { .. }` | `E::A(x) => { .. }` |
| `E::V` | `E::V` |
| `match x { E::A => { .. } _ => { .. } }` | 同形 |
| `for i in a..b { }` | `for i in a..b { }` |
| `use "x.lomt"` | 被导入模块的代码按依赖序先输出（每个一次），本模块在后 |
| `[T; N]` / `[e, ...]` | 同形 |
| `a[i]` | `a[(i) as usize]`（Rust 下标必须是 `usize`，Loment 允许任意整型） |
| `S { a: e }` | `S { a: e }` |
| `x.a` | `x.a` |
| `excluded "..."` | 进入 Potato 的 `excluded`（不进 Rust） |
| `let x: T = e;` | `let mut x: T = e;` |
| `if` / `while` / `return` | 同形 |
| 表达式 | 加括号保持结合性，语义逐位相同 |

产物确定性：无时间戳、声明序稳定、LF 行尾 → 可用 `--check` 逐字节对账。

## 5. 验证证据（实测）

示例 `loment/examples/demo.lomt`（`fib` / `gcd` / `popcount` / `in_domain` + 一个能力声明
+ `use "lom/fujr.lom"`）：

```
python tools/lomentc.py loment/examples/demo.lomt \
    --emit-rust loment/build/demo.rs --emit-potato loment/build/demo.potato.json
python tools/potato.py validate loment/build/demo.potato.json      # [OK]
rustc -O -o loment/build/demo_exe.exe loment/build/demo_main.rs
./loment/build/demo_exe.exe
```

输出（逐行比对通过）：

```
55            # fib(10)
21            # gcd(1071, 462)
8             # popcount(0xF0F0)
true          # in_domain(4)
false         # in_domain(5)
cap=disk[0..4] revocable=true
fujr magic=0x524A5546 header=64 section=32     # 来自 use 的 L0 布局
8             # blk_end(Blk { off: 3, len: 5 })   — L1 原生 struct
205           # mask_low(0xABCD, 8)  = 0xCD       — 位运算
true          # has_flag(0b1000, 3)               — 位运算 + bool
8             # MAX_BLKS                          — 常量
10            # sum_array([1, 2, 3, 4])           — 数组参数 + 下标
0 2 4 6       # fill_incr()                       — 数组元素赋值
1 2 0         # color_code(Red/Green/Blue)        — 枚举 + match
45            # sum_range(10)                     — for 0..n
40 8 3        # quadruple(10) / max_blocks() / pair_sum(Pair{1,2})
              #   ↑ 跨模块调用 double() / 模块级常量 / 导入的 struct
12 9 0        # shape_area(Circle(2)/Square(3)/Empty)  — 带载荷枚举 + 绑定
```

即：**语言 → 转译 → 原生编译 → 正确运行**，且能力声明、出界声明、常量、枚举、原生
struct、数组、L0 布局常量全部贯通到 Potato 形式对象。

## 6. 验收判据

- `lomentc_test` **54/54**：正例解析/转译确定性/产物形态 + 导入解析（去重/循环/缺失/重名）+ 33 类语义负例 + 9 类 Potato 负例 + `--check` 漂移检出。
- `ci.py --static-only` 静态门禁含 `lomentc_test`（**4/4**）。
- 生成的 Rust 用 `rustc` 编译通过并输出与预期一致（§5）。

## 7. 路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| v0 | Rust 子集 + 能力声明 + 转译 + Potato 导出 | ✅ |
| v1 | 位运算/移位、原生 struct 与字段访问、`excluded` 出界声明 | ✅ |
| v1 | 整型常量、定长数组与下标、数组元素赋值 | ✅ |
| v1 | 无载荷枚举 + `match`（含穷尽性检查）、`for` 区间循环 | ✅ |
| v1 | 模块间 `use .lomt`（依赖序输出/去重/循环检测/重名检查） | ✅ |
| v1 | 带载荷枚举 + 模式绑定 + 构造 | ✅ |
| v1 | 切片（与 Rust 借用语义纠缠，仓库暂无消费方 — 暂缓） | 待定 |
| v2 | 原生后端（LLVM 或自研），脱离 Rust 转译 | 待做 |
| v3 | 自举（用 Loment 写 Loment 编译器） | 待做 |
