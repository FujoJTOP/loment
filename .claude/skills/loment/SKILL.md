---
name: loment
description: 用 Loment 写程序时读它 —— Loment 是 FujoOS 项目自研的底层语言（Rust 的严格子集 + 能力域），装好 Loment 工具链后就能写、能检查、能编成原生可执行文件，不需要 Python。触发场景：写个 Loment 程序 / 写个 .lomt 文件 / 用 loment 命令编译或运行 / 看懂 loment 报的 E1–E17 错误 / 查 Loment 的内建函数或语法 / 把一段逻辑用项目自己的语言写 / Loment 的 struct、enum、match、capability、guard 怎么写。Also use whenever the task is to author, read, or debug Loment source (.lomt / .lom) with the Loment toolchain installed.
---

# Loment：写程序用的语言

**Loment 是 FujoOS 自研的底层语言。** 表面语法是 **Rust 的严格子集**（`module` / `use` /
`fn` / `let` / `if` / `while` / `for` / `match` / `enum` / `struct` / `trait` / 类型名 /
运算符都跟 Rust 一样），只多加了**能力域**（`capability` + `guard`）这一层语义。所以你的
Rust 先验可以直接用。

它能直接编成 **x86-64 原生可执行文件**（Linux ELF / Windows PE），**不需要 Python 运行时**。

> **本文件是自足的**：内建函数、语法、错误码都在这里面，不要去别处找（除非你手上正好有
> Loment 的源码仓库 —— 那一节在文末，注明「仓库内才有」的路径才去找）。

## 0. 先确认你手上的工具链

```bash
loment version
```

拿到 `Loment 0.1.4 Alpha (0.1.4-alpha), commit <短号>` 这类输出。**以它的 commit 为准**：
不同 checkout 能力不同（本文件描述 0.1.4-alpha 这一代）。

命令面（安装后就在 PATH 上）：

| 命令 | 作用 |
|---|---|
| `loment check FILE` | 只检查，不产出（诊断打到 stderr） |
| `loment ir FILE` | 打印 LLVM IR 到 stdout |
| `loment build FILE [-o OUT]` | 编成可执行文件 |
| `loment run FILE` | 编译 + 运行 |
| `loment fmt FILE` | 格式化（打印结果） |
| `loment doc FILE` | 生成 API 文档 |
| `loment lsp` | 语言服务（stdio 上的 LSP） |
| `loment skill [--print]` | 打印**这份指南**的路径 / 全文 —— 不依赖任何目录约定 |

**`build` / `run` 若报 `clang not found` 退出码 3** —— 你装的是**更早的包**（那时工具链装在
WSL 里、靠 clang 链接）。升级到本代的包即可：现在链接由包内的 `loment-lomelf` 做，本机直接出
PE/ELF，不碰 clang 也不碰 WSL。

## 1. 一分钟上手

```rust
module hello

// 打印到 stdout: 走 Linux ABI 的 write(1, buf, len)
fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn _start() {
    write_str(1, "hello\n");
    syscall4(60, 0, 0, 0);        // exit(0)
}
```

```bash
loment check hello.lomt
loment run   hello.lomt          # -> hello
```

`_start` 是 freestanding 入口（没有 `main`）。写完**跑一遍再下结论**，别只说"应该能行"。

**展示 Loment 代码时用 ```` ```rust ```` 作围栏** —— 各家高亮引擎都不认识 loment，而 Rust 是
它的严格超集，着色基本一致。文件本身永远是 `.lomt`（写程序）或 `.lom`（接口契约），
**不要**改扩展名、不要按 Rust 的完整语法去写（见 §6 的限制）。

## 2. 语言要点

| 构造 | 写法 |
|---|---|
| 模块 | 首行 `module <name>`（无分号） |
| 导入 | `use "path/to/other.lomt"`（相对当前文件或工作目录） |
| 函数 | `fn f(a: u32, b: str) -> u32 { ... }`（无返回写 `fn f()`） |
| 导出 | 跨模块可见加 `pub`：`pub fn` / `pub struct` / `pub const` |
| 变量 | `let x: u32 = e;`，赋值 `x = e;` —— **类型标注必写**，语言没有类型推断 |
| 控制流 | `if` / `else if` / `else`、`while`、`for i in lo..hi`、`return` |
| 结构体 | `struct S { a: u32, b: u32 }`；字面量 `S { a: 1, b: 2 }`；取字段 `s.a` |
| 枚举 | `enum E { A, B(u32) }`；构造 `E::B(3)`；`match` 要穷尽（或带 `_`） |
| `Option`/`Result` | 预置泛型枚举，配 `?` 传播、`if let E::V(x) = e { }` |
| 数组 | 类型 `[u8; 16]`；字面量 `[1, 2, 3]`；下标 `xs[0]`（可写 `xs[0] = 1`） |
| 切片 | 参数类型 `[T]` / `mut [T]`，`slice_len(s)` 取长度 |
| 泛型 | `fn max_of<T>(a: T, b: T) -> T`（编译期单态化） |
| trait | `trait M { fn m(self) -> u32; }` + `impl M for S { ... }`，静态派发 `obj.m()` |
| 类型 | `u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str`、struct 名、`[T; N]`、`[T]`、`mut [T]` |
| 转换 | `x as u64` —— 整型/指针互转都要显式写 `as` |
| 字符串 | `"..."`，转义 `\n \t \" \\`；操作见 §3 的 `str_*` |
| 注释 | `//`、`/* */`；`///` 是文档注释（`loment doc` 会抽出来） |
| 运算符 | 优先级同 Rust：`\|\| && == != < <= > >= \| ^ & << >> + - * / %`，一元 `- !` |

## 3. 内建函数（全部，没有别的）

**没有标准库**：没有 `String` / `Vec` / `HashMap`、没有 I/O 封装、没有字符串格式化。

| 签名 | 说明 |
|---|---|
| `str_len(s: str) -> u32` | 字节长度 |
| `str_byte(s: str, i: u32) -> u32` | 第 i 个字节 |
| `str_eq(a: str, b: str) -> bool` | 内容比较 |
| `str_concat(a: str, b: str) -> str` | 拼接（**走编译期 bump 堆，堆只有 64 KiB**，大串别拼） |
| `str_ptr(s: str) -> ptr` | 数据指针（喂 syscall 用） |
| `alloc(n: u32) -> ptr` | bump 堆分配（同样 64 KiB 上限） |
| `free(p: ptr) -> u32` | 占位（bump 堆不真回收） |
| `load8(p: ptr, off: u32) -> u32` | 读字节 |
| `store8(p: ptr, off: u32, v: u8) -> u32` | 写字节 |
| `ptr_add(p: ptr, n: u32) -> ptr` / `ptr_sub` | 指针偏移 |
| `slice_len(s: [T]) -> u32` | 切片长度 |
| `panic(code: u32) -> u32` | 不可返回（类型仅占位） |
| `atomic_add(p: ptr, n: u32) -> u32` | 原子加 |
| `get_bits(v: u8, hi: u32, lo: u32) -> u8` / `set_bits(v: u8, hi: u32, lo: u32, x: u8) -> u8` | 位域读写 |
| `inb(port: u16) -> u32` / `outb(port: u16, v: u8) -> u32` | 端口 I/O（**Rust 转译路径才支持**，原生路径不支持） |
| `syscall4(nr: u64, a0: u64, a1: u64, a2: u64) -> i64` | 裸 syscall（rax/rdi/rsi/rdx） |
| `syscall6(nr: u64, a0..a4: u64) -> i64` | 同上，多两个参数 |

**要输出数字**：`alloc` 一块再 `store8` 拼十进制（自己写循环取模），没有 `printf`。

**syscall 号**：Linux ELF 目标上就是 Linux 的号（`write`=1、`exit`=60、`openat`=257…）。
**Windows PE 目标只实现了 8 个**：`read`(0) / `write`(1) / `close`(3) / `brk`(12) / `exit`(60) /
`getdents64`(217) / `openat`(257) / `newfstatat`(262)，**其余号返回 -1（静默失败）** ——
要跨平台跑就按这 8 个来。`/proc/self/cmdline` 在 PE 上由运行库合成，argv 读法两边一致。

## 4. 错误码怎么读

编译器打的形如：

```
E2 @13 line 4: 1
```

读法：`E2` = 统一口径的 **E002**；`@13` 是**第 13 个 token**（不是列号）；`line 4` 是行号；
冒号后是该 token 的原文。**码按"你该做什么"分，不按措辞分**：

| 码 | 要你做的事 |
|---|---|
| E1 | 类型对不上（含 return / 载荷 / 内建实参） |
| E2 | **有东西没声明**（未声明变量 / 未定义函数 / 未知类型，含"先用后定义"） |
| E3 | **实参个数不对** |
| E4 | 能力域问题（未声明 / 越界 / 重复） |
| E5 | 用了 `excluded` 声明出界的空间 |
| E6 | 值已被移动 |
| E7 | 借用冲突（可变借用与借用并存 / 可变借用两次） |
| E8 | `match` / 枚举（不穷尽、模式重复、不是枚举、载荷绑定不对） |
| E9 | 命名与基类型冲突 / 空 struct / 空 enum |
| E10 | `?` 用在了不是 `Result`/`Option` 的地方 |
| E11 | 切片可变性（只读切片不能写、形参要 `mut`） |
| E12 | 悬垂引用 |
| E13 | 重复定义 / 重名 |
| E14 | 赋值目标不是左值 |
| E15 | 字段与下标（无此字段、缺字段、重复初始化、对非结构体取字段…） |
| E16 | 数组字面量 / 长度与声明不符 |
| E17 | `as` 转换非法 |

**最常见的两个**：`E2` 十有八九是**漏写类型标注**（`let x = 1;` 不合法，要 `let x: u32 = 1;`）
或用了未定义的函数名；`E3` 是调用实参个数对不上。诊断信息**只有码和位置，没有中文句子**，
所以拿不准就看上表 + 对照 §2/§3。

## 5. 能力域（Loment 唯一"新的东西"）

```rust
module blk

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);   // 越界 -> trap；通过 -> 审计计数 +1
    return slot;
}
```

语义：`guard cap(e);` 求值 `e` 得索引 → 不在 `[lo, hi]` 就 **trap**（无其它副作用），
在域内就给审计表加一。**字面量越界是编译期错误**（`E4`），非字面量是运行期检查。

**诚实边界**：`guard` 只约束**索引**落在域内，**不是授权** —— 它不检查"当前主体有没有这个
能力"，主体-能力的绑定在内核侧。`revocable` 目前只是声明与域描述表里的一个标志。

## 6. 已知限制（照实测写，别猜）

0. **⚠️ 包里的 `loment build` / `loment run` 编不了"按值传 struct / enum"的程序**
   （2026-09-15 实测）。包内的链接器 `loment-lomelf` 还不支持聚合按值：struct 当参数
   会报一条不像给用户看的错，enum + `match` 按值会**直接崩**。`loment check` 和
   `loment ir` 不受影响（只有"出可执行文件"这一步）。**参考实现没这个问题**，
   是发行包这一条路独有的。
   → 在包环境里先用**标量 + 字符串 + 切片**写程序；真需要 struct/enum 按值，
   要么等这个修好，要么在有源码仓库时用仓库里的 `tools/lomelf.py` 链接。
1. **结构体字面量不能直接当实参**：`f(S { a: 1 })` 报 `native 后端不支持该表达式: StructLit`。
   先 `let x: S = S { a: 1 };` 再 `f(x)` 就行。**聚合参数与返回值本身是支持的**
   （但见第 0 条：包里那条链接路径还没跟上）。
2. **没有堆内存管理**：编译期 bump 堆 64 KiB，`alloc`/`str_concat` 超了直接 abort。
3. **没有标准库**（见 §3）。
4. **形参最多 10 个**。
5. **条件位置不能写结构体字面量**（`if p { }` 的歧义按 Rust 规则消解，要加括号）。
6. **`inb`/`outb` 只在 Rust 转译路径支持**，原生后端会明确报错。
7. 模块内**先定义后使用**（别依赖前向引用）。

## 7. 拿不到源码仓库时怎么办

- **抄现成程序**：包里有 `share/loment/examples/`（通常只有 `user_hello.lomt`）。更多示例
  在源码仓库的 `loment/examples/`（26 个）—— 没有仓库就用下面的办法。
- **把编译器当 oracle**：`loment check` → 改 → `loment ir` 看生成的 LLVM IR → `loment run`。
  这三步闭环足以在没有资料的情况下迭代；卡住时**先看 IR**，它比错误码信息量大得多。
- **`loment doc FILE`** 能给出手头文件的 API 摘要，可以拿来确认自己写的接口。
- **`loment skill --print`** 就是这份指南 —— 机器上没有 Claude/Codex 时，这是唯一的入口，
  而且它与文件系统约定无关（跑 CLI 就有）。
- **这份指南本身也是给别的 agent 的**：装 Loment 时它会被写进 Claude 的用户级 skill 目录，
  并往 Codex 的 `~/.codex/AGENTS.md` 写一段**带标记的指针**，同时设 `LOMENT_SKILL`
  环境变量指向包内那份。正本只有一份 —— 别把它复制到别处去改。

## 8. 下面这些路径**只在源码仓库里有**（没仓库就别去找）

仓库 = FujoOS 的 Loment 线工作树（`loment/`、`lom/`、`tools/loment*.py`、`docs/14?–16?-loment-*.md`）。
有仓库时额外能用的：

- `python tools/loment.py diag FILE` —— 把错误码翻译成**中文修复建议**（包内没有这个工具）；
- `loment/examples/` 26 个可抄的完整程序；`loment/selfhost/` 是"用 Loment 写的 Loment 编译器"；
- 文档：`docs/143`（语言规范）· `docs/146`（能力域形式语义）· `docs/148`（工具链）·
  `docs/145`（里程碑）· `docs/154`（状态矩阵）；
- 开发期的编译路径：`python tools/loment.py ir F.lomt > f.ll` +
  `python tools/lomelf.py f.ll --target pe -o f.exe`（**包内没有 Python，这条在包里不适用**）。

**仓库规矩**（有仓库才适用）：`lom/*.lom` 是**跨线接口契约**，改它要走单独提交并知会
compat 线；不要为了某次重构顺手改它。`.lomt` 随便改。
