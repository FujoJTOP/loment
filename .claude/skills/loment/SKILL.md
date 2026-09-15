---
name: loment
description: 用 Loment 写程序时读它 —— Loment 是 FujoOS 项目自研的底层语言（Rust 的严格子集 + 能力域），装好 Loment 工具链后就能写、能检查、能编成原生可执行文件，不需要 Python。触发场景：写个 Loment 程序 / 写个 .lomt 文件 / 用 loment 命令编译或运行 / 看懂 loment 报的 E1–E19 错误 / 查 Loment 的内建函数或语法 / 把一段逻辑用项目自己的语言写 / Loment 的 struct、enum、match、capability、guard 怎么写 / 从别的语言迁到 Loment 时哪里不一样。Also use whenever the task is to author, read, or debug Loment source (.lomt / .lom) with the Loment toolchain installed.
---

# Loment：写程序用的语言

**Loment 是 FujoOS 自研的底层语言。** 表面语法是 **Rust 的严格子集**（`module` / `use` /
`fn` / `let` / `if` / `while` / `for` / `match` / `enum` / `struct` / `trait` / 类型名 /
运算符都跟 Rust 一样），只多加了**能力域**（`capability` + `guard`）这一层语义。
它能直接编成 **x86-64 原生可执行文件**（Linux ELF / Windows PE），**不需要 Python 运行时**。

> **本文件是自足的**：语法、内建函数、错误码、**以及最容易踩的那些坑**都在这里。
> 不要先去别处找（除非你手上正好有源码仓库 —— 那一节在文末）。

**先读 §1 那个程序，再回来看表。** 它一个文件把整门语言串了一遍，而且是**能编译能跑**的；
`.claude/skills/loment/SKILL.md` 里贴的和仓库里的 `loment/examples/tour.lomt` 是同一份
（包里也随带一份），所以照抄一定对得上。

## 0. 先确认你手上的工具链

```bash
loment version
```

拿到 `Loment 0.1.4 Alpha2 (0.1.4-alpha2), commit <短号>` 这类输出。**以它的 commit 为准**：
不同 checkout 能力不同（本文件描述 0.1.4-alpha2 这一代；更早的包会显示 `0.1.4 Alpha`）。

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

**包内 CLI 实测的三个坑**（Windows 上尤其容易误判成败）：

- `-o` 收的是**不带扩展名**的名字：`loment build hi.lomt -o hi` 出 `hi.exe`；写成
  `-o hi.exe` 只会得到 `hi.exe.exe`。
- Windows 上从 Git Bash / MSYS 里要调 **`loment.cmd`**；`bin/loment` 是 POSIX 脚本，
  在 Windows 上走 `build`/`run` 会去链 ELF 再执行，报 `Exec format error`。
- `loment: hi.exe` 这行是**成功回显**，不是错误；失败时会另打 `loment: failed` 并非零退出。

## 1. 一个程序过完全门语言

**先跑通这个最小骨架**（下面这份可以直接 `loment run`）：

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

`loment run hello.lomt` → 打出 `hello from Loment`。它已经定了三件事：**入口叫 `_start`**
（不是 `main`，且不接参数）、**`let` 必须写类型**、**没有 printf**（输出走 `syscall4`，
字符串用 `str_ptr` / `str_len` 转成裸指针和长度）。

再往下是完整的那份（它叫 `tour`，逐段的解说在程序后面）：

```rust
module tour

const LIMIT: u32 = 3;

capability slots : disk[0..4] revocable

struct Entry {
    cents: u32,
    tax: u32,
}

enum Kind {
    Small,
    Big(u32),
}

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let buf: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(buf, i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}

fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}

fn total(e: Entry) -> u32 {
    return e.cents + e.tax;
}

fn score(k: Kind) -> u32 {
    match k {
        Kind::Small => { return 1; }
        Kind::Big(w) => { return w * 2; }
    }
}

fn sum_slice(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}

fn guarded(slot: u32) -> u32 {
    guard slots(slot);
    return slot;
}

fn _start() {
    let e: Entry = Entry { cents: 40, tax: 2 };
    let t: u32 = total(e);
    let s: u32 = score(Kind::Big(5));
    let a: u32 = 7;
    let b: u32 = 9;
    let m: u32 = max_of(a, b);
    let xs: [u32; 3] = [1, 2, 3];
    let sum: u32 = sum_slice(&xs);
    let g: u32 = guarded(2);
    let acc: u32 = 0;
    for i in 0..LIMIT {
        acc = acc + i;
    }
    write_dec(1, t);
    write_str(1, " ");
    write_dec(1, s);
    write_str(1, " ");
    write_dec(1, m);
    write_str(1, " ");
    write_dec(1, sum);
    write_str(1, " ");
    write_dec(1, g);
    write_str(1, " ");
    write_dec(1, acc);
    write_str(1, "\n");
    if t == 42 && s == 10 && m == 9 && sum == 6 && g == 2 && acc == 3 {
        write_str(1, "TOUR RESULT: PASS\n");
        syscall4(60, 0, 0, 0);
    }
    write_str(1, "TOUR RESULT: FAIL\n");
    syscall4(60, 1, 0, 0);
}
```

跑它：`loment run tour.lomt` → 打出 `42 10 9 6 2 3` 与 `TOUR RESULT: PASS`。

> **只在装了发行包、没有仓库的机器上**：`tour` 用到了**按值传 struct / enum**，而包内那个
> 自举链接器还不支持按值聚合（§6.9），所以 `loment run tour.lomt` 会**失败**（非零退出、
> 不产出文件）。`loment check` / `loment ir` 不受影响。**上面那个 hello 骨架只用了标量、
> 字符串和 syscall，在包里可以直接跑** —— 先用它确认环境，再往下看。

**逐段解说**（按出现顺序）：

| 片段 | 说的事 |
|---|---|
| `const LIMIT: u32 = 3;` | 模块级常量，函数体里可直接引用 |
| `capability slots : disk[0..4] revocable` | 能力域声明（见 §5） |
| `struct Entry { … }` | 结构体；字段要带类型 |
| `enum Kind { Small, Big(u32) }` | 枚举，变体可带**单载荷** |
| `fn write_str/fd: u64, s: str` | 函数；**每个形参都要写类型**，返回写 `-> T`，无返回就不写 |
| `syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64)` | 裸 syscall：`(号, 参数…)`；**没有 printf** |
| `fn write_dec(…)` | 十进制输出的标准写法：`alloc` + `store8` 拼 + 反转 |
| `let buf: ptr = alloc(12)` | **`let` 必须写类型**；`ptr` 是裸指针 |
| `48 as u8` | 转换一律显式 `as`（整型/指针互转都要） |
| `fn max_of<T>(a: T, b: T) -> T` | 泛型函数（编译期单态化）；**调用时实参要能定出 T，见 §6.4** |
| `e.cents` | 取字段 |
| `match k { Kind::Small => { return 1; } … }` | **臂体是块**，不是表达式（§6.1） |
| `Kind::Big(w)` | 带载荷变体的模式，`w` 是绑定名 |
| `fn sum_slice(xs: [u32])` | 切片参数；`&xs` 传数组当切片；`slice_len(xs)` 取长 |
| `guard slots(slot);` | 能力域守卫（§5） |
| `fn _start()` | **freestanding 入口**，没有 `main`；参数是空的 |
| `let xs: [u32; 3] = [1, 2, 3]` | 定长数组；下标 `xs[0]` 可读可写 |
| `for i in 0..LIMIT { … }` | 区间循环（上界**不含**） |
| `while i < n { … }` | 循环 |
| `if a > b && c == 3 { … }` | 条件必须是 bool；`&&`/`\|\|` 短路 |
| `syscall4(60, 0, 0, 0)` | `exit(0)` —— 程序用 syscall 退出 |

## 2. 语法速查

| 构造 | 写法 |
|---|---|
| 模块 | 首行 `module <name>`（无分号） |
| 导入 | **两种写法**：`use 名字`（如 `use bytes`，按 `loment/lib` → `examples` → `selfhost` → `tools` 找 `名字.lomt`）；`use "path/to/other.lomt"`（相对当前文件或工作目录，**自己目录里的伴生文件要用这个**） |
| 函数 | `fn f(a: u32, b: str) -> u32 { ... }`（无返回写 `fn f()`）；**形参最多 10 个** |
| 导出 | 跨模块可见加 `pub`：`pub fn` / `pub struct` / `pub const` |
| 常量 | `const NAME: u32 = 3` |
| 变量 | `let x: u32 = e;`，赋值 `x = e;` —— **类型标注必写**，语言没有类型推断 |
| 控制流 | `if` / `else if` / `else`、`while`、`for i in lo..hi`、`return <expr>;`（**必须有值**） |
| 结构体 | `struct S { a: u32, b: u32 }`；字面量 `S { a: 1, b: 2 }`；取字段 `s.a` |
| 枚举 | `enum E { A, B(u32) }`；构造 `E::B(3)`；无载荷 `E::A` |
| `match` | `match x { E::A => { … } E::B(v) => { … } _ => { … } }` —— **臂体是块**；要穷尽（或用 `_`） |
| `if let` | `if let E::B(v) = x { … } else { … }` |
| `Option`/`Result` | 预置泛型枚举，配 `?` 传播 |
| 数组 | 类型 `[u8; 16]`；字面量 `[1, 2, 3]`；下标 `xs[0]`（可写 `xs[0] = 1`） |
| 切片 | 参数类型 `[T]` / `mut [T]`，传 `&xs` / `&mut xs`，`slice_len(s)` 取长度 |
| 泛型 | `fn max_of<T>(a: T, b: T) -> T`（编译期单态化） |
| trait | `trait M { fn m(self) -> u32; }` + `impl M for S { … }`，静态派发 `obj.m()` |
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

**要输出数字**：照抄 §1 的 `write_dec`，没有 `printf`。

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
冒号后是该 token 的原文。**码按「你该做什么」分，不按措辞分**：

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
| E18 | **`use <名字>` 解析不出来**（找不到，或命中多处）—— 改名字/补文件，或改用路径形式 `use "...lomt"` |
| E19 | **语法错误（解析期）** —— 按消息给的 `行:列` 改那一行的写法 |

**最常见的**：`E2` 十有八九是**漏写类型标注**（`let x = 1;` 不合法，要 `let x: u32 = 1;`）
或用了未定义的函数名；`E3` 是调用实参个数对不上；`E19` 见 §6.1/§6.3。

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

## 6. 陷阱清单（这一节是实测出来的，**先看这里能省掉大半返工**）

Rust 先验能带你走完 90%（标量/字符串/切片/控制流/泛型/trait），**剩下的偏差几乎全集中在
"Loment 自己发明的那部分"**。以下每条都有最小复现。

1. **`match` 的臂体必须是块，不是表达式。**
   `Kind::A => 0,` → `E19 期望 {，得到 '0'`。要写 `Kind::A => { return 0; }`。

2. **泛型枚举的模式要用单态化之后的名字。**
   被匹配值类型是 `Result<i64, u32>` 时，`Result::Ok(v)` → `E8 与主体枚举 Result_i64_u32 不符`；
   正解是 **`Result_i64_u32::Ok(v)`**。普通（非泛型）枚举照常写 `Kind::Big(w)`。

3. **没有无值的 `return;`。**
   `return;` → `E19 期望表达式，得到 ';'`。要么 `return <expr>;`，要么用 if/else 收尾。

4. **泛型函数调用：实参必须能把 `T` 定出来。**
   `pick(4, 9)`（整型字面量）→ `E2 调用未定义的函数 pick`（**这条消息会误导**，它不是
   跨模块问题）；先 `let a: u32 = 4;` 再 `pick(a, b)` 就对了。`all_loment.lomt` 的
   `max_of(s, d)` 正是后者。

5. **结构体字面量不能直接当实参**：`f(S { a: 1 })` 报 `native 后端不支持该表达式: StructLit`。
   先 `let x: S = S { a: 1 };` 再 `f(x)` 就行。

6. **条件位置不能写结构体字面量**（`if p { }` 的歧义按 Rust 规则消解），要加括号。

7. **模块内先定义后使用**（别依赖前向引用）。

8. **一个解析错会中止整个检查** —— 后面所有诊断都不出来。所以一次只改一处、改完立刻
   `loment check`；"没有别的错"不代表没有。

9. **包里的 `loment build` / `loment run` 编不了"按值传 struct / enum"的程序**：
   包内的链接器还不支持聚合按值（struct 当参数会报一条不像给用户看的错，enum + `match`
   按值会直接崩）。`loment check` / `loment ir` 不受影响 —— 只有"出可执行文件"这一步。
   → 在包环境里先用**标量 + 字符串 + 切片**写程序。

10. **`==` 用在 struct / enum 上**：checker 放行，但原生后端会拒（`M0 只支持标量`）。
    比较聚合要用显式字段比较。

## 7. 拿不到源码仓库时怎么办

- **抄现成程序**：包里 `share/loment/examples/` 有 `tour.lomt`（就是 §1 这份）和
  `user_hello.lomt`。更多示例在源码仓库的 `loment/examples/` —— 没有仓库就用下面的办法。
- **把编译器当 oracle**：`loment check` → 改 → `loment ir` 看生成的 LLVM IR → `loment run`。
  这三步闭环足以在没有资料时迭代；卡住时**先看 IR**，它比错误码信息量大得多。
- **`loment doc FILE`** 能给出手头文件的 API 摘要，可以拿来确认自己写的接口。
- **`loment skill --print`** 就是这份指南 —— 机器上没有 Claude/Codex 时，这是唯一的入口，
  而且它与文件系统约定无关（跑 CLI 就有）。
- **这份指南本身也是给别的 agent 的**：装 Loment 时它会被写进 Claude 的用户级 skill 目录，
  并往 Codex 的 `~/.codex/AGENTS.md` 写一段**带标记的指针**，同时设 `LOMENT_SKILL`
  环境变量指向包内那份。正本只有一份 —— 别把它复制到别处去改。

## 8. 下面这些路径**只在源码仓库里有**（没仓库就别去找）

仓库 = FujoOS 的 Loment 线工作树（`loment/`、`lom/`、`tools/loment*.py`、`docs/14?–16?-loment-*.md`）。
有仓库时额外能用的：

- `python tools/loment.py lib ...` —— **库系统**（`docs/168`）：`tree`（依赖树 + 每个库的实例数）、
  `id`（实例身份）、`cap`（能力需求闭包，带来源链）、`check`（冲突）、`materialize`（把嵌套与
  多版本摊成编译器能直接吃的树）。**写库没有新东西要学**：库 = 一个目录；依赖就是源码里的
  `use`（不用另行声明）；导出就是 `pub`；可选的 `pkg.lomp` 里只放两个标签 ——
  `pub fn name() -> str` 与 `pub fn version() -> str`（语言没有字符串常量，所以是函数不是 `const`）；
- `python tools/loment.py diag FILE` —— 把错误码翻译成**中文修复建议**（包内没有这个工具）；
- `loment/examples/` 全部示例（`tour.lomt` 是全语言导览）；`loment/selfhost/` 是
  "用 Loment 写的 Loment 编译器"；
- 文档：`docs/143`（语言规范）· `docs/146`（能力域形式语义）· `docs/148`（工具链）·
  `docs/158`（冻结面，含"改语言要付什么代价"）· `docs/145`（里程碑）· `docs/154`（状态矩阵）；
- 开发期的编译路径：`python tools/loment.py ir F.lomt > f.ll` +
  `python tools/lomelf.py f.ll --target pe -o f.exe`（**包内没有 Python，这条在包里不适用**）。

**仓库规矩**（有仓库才适用）：`lom/*.lom` 是**跨线接口契约**，改它要走单独提交并知会
compat 线；不要为了某次重构顺手改它。`.lomt` 随便改。
