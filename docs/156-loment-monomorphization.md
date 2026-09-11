# 156 · 单态化与 trait 派发的可核对规则（M6/M7/M8 → M82 收尾的长杆）

> 状态: **规则已固化 + 金标工具就位**（2026-09-10）· 工具: `tools/mono_trace.py`
> 读者: 要给自举版 codegen（`loment/selfhost/codegen.lomt`）补单态化的人。
> 一句话: **参考实现用 AST 做单态化，自举版只吃 token 流 —— 这份文档给出"应该生成什么"的可核对金标，
> 免得实现时反复去读 `lomentc.prepare()`。**

## 0. 为什么需要这份文档

`lomentc.prepare()` 在 **AST** 上做实例化：把 `max<T>` 展开成 `max_u32`、把 `Result<u32,u32>` 改写成
`Result_u32_u32`、把 `impl Trait for Small` 的方法命名为 `Small_measure`。
自举版 `codegen.lomt` 只有 token 流，没有 AST，所以这一切要在 **token 层重建一遍**。
重建的正确性判据是**逐字节等于参考输出**（`loment_p8_test`），所以先把"应该生成什么"钉死。

## 1. 命名规则（唯一一条）

```
实例名 = 泛型声明名 + "_" + "_".join(具体类型名)
```

| 声明 | 使用点 | 实例名 |
|---|---|---|
| `fn max<T>` | `max(p.a, p.b)`（`p: Pair<u32>`） | `max_u32` |
| `fn max<T>` | `max(x, y)`（`x, y: i32`） | `max_i32` |
| `struct Pair<T>` | `let p: Pair<u32>` | `Pair_u32`（**只影响类型名，不产生函数**） |
| `enum Opt<T>` | `let a: Opt<u32>` | `Opt_u32`（同上） |
| `enum Result<T, E>` | `-> Result<u32, u32>` | `Result_u32_u32` |

`tools/mono_trace.py --check` 会断言全语料的实例名都满足这条规则（当前 24 个文件 / 3 个实例 / 0 问题）。

## 2. 发射顺序（两条）

1. **非泛型函数按源码顺序发射**；泛型声明本身（`fn max<T>`）**不发射**。
2. **实例追加在最后**，按"首次被用到"的顺序。

`native_gen.lomt` 的金标（`python tools/mono_trace.py loment/examples/native_gen.lomt`）：

```
发射顺序: call_pair_max -> call_max_i32 -> call_opt -> max_u32 -> max_i32
实例 max_u32: 由 max<u32> 而来, 参数 [('a','u32'), ('b','u32')] -> u32
实例 max_i32: 由 max<i32> 而来, 参数 [('a','i32'), ('b','i32')] -> i32
```

注意 `max_u32` 在 `max_i32` 之前 —— 顺序取决于**调用点出现的先后**，不是类型名大小。

## 3. 类型名也会被改写（容易漏）

`prepare()` 不只改函数名：**签名与注释里的泛型类型名同样被实例化**。实测 `native_res.lomt`：

```
; parse_small -> Result_u32_u32          <- 注释里是实例名, 不是 Result
define { i32, i64 } @parse_small(...)    <- LLVM 类型是结构化的, 与名字无关
```

所以自举版要同时做到：
- **注释行**（`emitted_name`）输出实例名（`Result_u32_u32`）；
- **LLVM 类型**（`emit_ty`）输出结构化结果（带载荷枚举 → `{ i32, i64 }`），它由*类型实参*决定。

## 4. trait 静态派发（M8）

金标（`native_trait.lomt`）：

```
发射顺序: Small_measure -> Big_measure -> call_small -> call_big
```

规则：
1. `impl Trait for Small { fn measure(&self) -> u32 { ... } }` → 函数名 **`Small_measure`**，**发射位置就在 `impl` 块出现处**（不是追加到最后）。
2. 方法体里的 `self` 一律改写成 **`__self`**（参数名、`alloca`、`load`、`store` 都要改）。
3. 调用点 `x.measure()` → `call <ret> @Small_measure(<Small 的类型> x)`；接收者类型由 `expr_type(x)` 决定，
   mangled 名 = `接收者类型名 + "_" + 方法名`。

## 5. 自举版实现要点（token 层怎么落）

- **类型参数替换**：状态块低区（值栈搬到 8192 之后 `16..475` 全空）放一张小替换表 ——
  计数 `16`、表项 `24+i*8`（`param_tok | value_tok`），配 `subst_tok()` 在
  `emit_ty` / `is_signed` / `bit_width` 的入口做一次替换。
- **变量的实例化必须随变量走**：`let p: Pair<u32>` 之后再写 `p.a`，字段类型表里存的是声明时的 `T`，
  所以局部/参数表要**多存两个类型实参槽**（或等价地在读取字段时用变量的实例化替换）。
  这一条是 `native_gen` 能否对齐的关键。
- **实例发现**：扫到"调用泛型函数"时，用 `expr_type(实参)` 推出类型实参；已见过的 (函数, 实参组合)
  不重复生成。发现顺序即发射顺序（所以先发射完非泛型函数、再按发现序发实例）。
- **调用点改名**：被调方是泛型时，名字要按 §1 拼出来（基名 token + `_` + 各类型 token 文本）。

## 6. 现状与靶子

| 示例 | 差多少 | 卡在哪 |
|---|---|---|
| `native_gen.lomt` | 142 行 | 泛型函数实例化 + 泛型 struct/enum 的类型替换（**建议第一个做**，金标最短） |
| `native_res.lomt` | 112 行 | §3 的类型名改写（`Result_u32_u32`）+ `?` 的早退降级 |
| `native_trait.lomt` | 35 行 | §4 的 impl 改名与 `self` → `__self`（**行数最少**） |
| `all_loment.lomt` | 131 行 | 多模块 + 泛型（依赖上面两项） |
| `demo.lomt` | 141 行 | 泛型 + 能力域（能力域已在 2026-09-10 落地） |
| `native_raii.lomt` | — | **不是目标**：连 Python 版都报错（Drop 只在 Rust 路径支持） |

## 7. 复现命令

```
python tools/mono_trace.py loment/examples/native_gen.lomt     # 单个文件的实例/顺序/命名
python tools/mono_trace.py --all                              # 全语料摘要
python tools/mono_trace.py --check                            # 断言命名规则不漂 (进门禁)
python tools/loment_p8_test.py                                # 逐字节对照 (M82 的判据)
```
