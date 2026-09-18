---
name: loment
description: 用 Loment 写程序时读它 —— Loment 是 FujoOS 项目自研的底层语言（Rust 的严格子集 + 能力域），装好 Loment 工具链后就能写、能检查、能编成原生可执行文件，不需要 Python。触发场景：写个 Loment 程序 / 写个 .lomt 文件 / **写个 Loment 库**（库 = 一个目录，依赖就是源码里的 `use`、不用另行声明；导出就是 `pub`；可选 `pkg.lomp` 清单，见 §9）/ 管理或排查依赖 / 用 loment 命令编译或运行 / 看懂 loment 报的 E1–E23 错误 / 查 Loment 的内建函数或语法（`loment builtins` / `loment syntax` / `loment cheat`）/ 把一段逻辑用项目自己的语言写 / Loment 的 struct、enum、match、capability、guard 怎么写 / **给项目配自己的源码后缀**（`loment.conf` 的 `source_ext`，见 §7.1）/ **注册自定义 `loment` 子命令**（`loment foo` -> PATH 上的 `loment-foo`，见 §7.2）/ 从别的语言迁到 Loment 时哪里不一样。**库与依赖的事先读 `lompi` 的指南**（`~/.claude/skills/lompi/SKILL.md`；装了包则 `<前缀>/share/lompi/skill/SKILL.md`）—— 装库、解析依赖、看本机有哪些库、`use <名字>` 解析到谁，全在那份里。Also use whenever the task is to author, read, or debug Loment source (.lomt / .lom / .lomp) or a Loment library with the Loment toolchain installed.
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

拿到 `Loment 0.1.4 Pre2 (0.1.4-pre2), commit <短号>` 这类输出。**以它的 commit 为准**：
不同 checkout 能力不同（本文件描述 0.1.4-pre2 这一代；更早的包会显示 `0.1.4 Alpha` / `0.1.4 Alpha2.3`）。

**命令面一共 38 条，敲 `loment help` 看全部**（分区 + 对齐 + 上色），`loment help <命令>` 看单条。
最常用的这些：

| 命令 | 作用 |
|---|---|
| `loment check FILE` | 只检查，不产出（诊断打到 stderr —— 由报错器 `lomenterr` 渲染：**位置 + 源行 + 插入符 + 错了什么 + 为什么错 + 怎么改(≥3 条) + 支持/不支持**；文件不是 Loment 时还会给出`翻译进来`的三条路。见 §9） |
| `loment ir FILE` | 打印 LLVM IR 到 stdout |
| `loment build FILE [-o OUT]` | 编成可执行文件 |
| `loment run FILE` | 编译 + 运行 |
| `loment fmt FILE` | 格式化（打印结果） |
| `loment doc FILE` | 生成 API 文档 |
| `loment lsp` | 语言服务（stdio 上的 LSP） |
| `loment skill [--print]` | 打印**这份指南**的路径 / 全文 —— 不依赖任何目录约定 |

**手边没有这份指南时，语言参考可以直接从 CLI 里问**（这正是它自足的意义）：

| 命令 | 作用 |
|---|---|
| `loment cheat` | 一页速查（最容易踩的 16 条，按「最容易踩」排序） |
| `loment syntax` | 语法速查表 |
| `loment builtins` | 内建函数表（全部，没有别的） |
| `loment types` / `keywords` / `caps` | 类型表 / 关键字 / 能力域 |
| `loment codes` / `loment explain E4` | 错误码表 E1–E23 / 单条详解 |
| `loment new NAME` | 生成一个能直接 `loment run` 的骨架 |
| `loment stat` / `fns` / `grep` / `hash` / `cat` | 读源码（行数、函数签名、搜索、sha256、带行号打印） |
| `loment ls` / `tree` / `examples` / `example tour` | 看目录与示例 |
| `loment doctor` / `where` / `env` / `tools` | 排查安装（缺哪个组件、装在哪） |

`--no-color`（或 `loment color off`）关掉 ANSI —— 管道里要干净输出时用它。
**它同时管诊断那一侧**：`loment check FILE --no-color` 会把开关转交给报错器，
关掉之后输出里一个转义字节都不剩（`loment check f.lomt | less` 就该这么用）。

> ### 任务碰到"库 / 依赖"，**先去读 lompi 的指南**
>
> lompi 是 **Loment 库的包管理器**（管 store、锁文件、`deps/`）。下面这些事，答案全在它那份
> 指南里，**这份指南不讲**：
>
> - 装/取一个库、解析依赖、生成锁文件；
> - "这台机器上有哪些 Loment 库"、"这个 store 里是什么"、"某个库的源码在哪"；
> - `use <名字>` 到底解析到谁、哪个版本会被选中、两个版本能不能共存；
> - 项目里的 `deps/` 是什么形状、编译器从哪里找库。
>
> **先读它再动手**，别照别的语言的包管理器猜 —— 模型不一样（库 = 一个目录，身份是**内容
> 哈希**，依赖就是源码里的 `use`，不用另行声明）。
>
> 路径：`~/.claude/skills/lompi/SKILL.md`；只有安装包时在 `<前缀>/share/lompi/skill/SKILL.md`
> （同一份）。**这份指南和它是一对**：讲语言语法看这里，讲库与依赖看它。
>
> 它是**独立命令，不是 `loment` 的子命令**：`loment help` 里没有它，也别试 `loment lompi`，
> 直接敲 `lompi`。

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
| 项目模式 | `choose std` / `choose no_std`（**整个程序只写一次，只能写在入口那一份**，库不许写；不写就是 `std`）—— 见 §2.1 |
| 开关设定 | `addin <名字>`（**只写在入口那一份**）—— 拉一个"开关设定"单元进来，见 §2.2 |
| 自定义语法 | `comefor let "词" to { … }` … `byuse "词" done` —— **在编译期定义一个新语法**。`to` 后面那段是**一段 Loment 程序**（`fn main` 是入口，**返回值 = 吃掉的 token 数**），它用 `ct_n` / `ct_tok` / `ct_out` / `ct_syn` 读 token、吐 token。定义与终止都**只能在顶层**、不能嵌套；展开发生在**解析之前**的 token 层，所以报错/跳转仍指回你写的那一行。见 `docs/184` |
| 导入 | **两种写法，后面都不带分号**：`use 名字`（**按层找，先命中先用**：① 项目根 `<项目>/deps/<名字>/<名字><后缀>` ② 工具链自带 `<工具目录>/../share/lompi/store/<名字>/<版本>/<名字><后缀>` ③ 内置四根 `loment/lib` → `examples` → `selfhost` → `tools`，**只有第 ③ 层要求名字唯一**）；`use "path/to/other.lomt"`（相对当前文件或工作目录，**自己目录里的伴生文件要用这个**）。`<后缀>` 默认 `.lomt`，项目可以换成自己的（§7）。单文件最多 **300 条** use。写成 `use x;` 会被解析期拒绝（§6.13） |
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

### 2.1 项目模式 `choose`

```rust
module myapp

choose no_std          // ← 整个程序只写这一次；不写就是 std
```

- **它是"整个程序"的属性，不是某个文件的**：所以只许出现一次，而且**只能写在入口那一份**
  （被 `use` 进来的库写了就是 E22）。两个编译器都在管这件事。
- 取值只有 `std` 与 `no_std` 两个（拼错是 E22）。
- **`no_std` 才是 Loment 的本来面目**（它最初就是为了写 FujoOS）。`std` 是默认值，
  意味着你可以用宿主能力。**什么时候必须写**: 写系统、写要在裸机上跑的东西时 ——
  显式写出来是给读者和工具看的，语言不强制。
- 它也会进 Potato 形式对象（`mode` 字段），所以"这份程序是哪个模式"在**不读源码**的
  那一侧也看得见。

### 2.2 开关 `set choose` / `choose` / `addin`

**开关**是"打开才编进去的代码"。关着的那段**连词法 token 都不进解析器** —— 所以
"关掉 = 不依赖"是字面成立的：体内引用的东西**不需要存在**。

```rust
module myapp

// 定义：名字 + "打开时才编进去的代码"
set choose verbose {
    pub fn banner() -> u32 { return 0x5EED; }
}

choose verbose          // 取值：打开（`choose close verbose` 是关掉）
                        // **不写就是关着**

fn _start() {
    syscall4(60, banner() as u64, 0, 0);   // 只有 verbose 开着时这一句才编得过
}
```

几条规则（三条都是 **E22**，两个编译器都在管）：

- 一个文件里开关可以有很多（**上限 500**，超了报错，绝不静默丢），但**同名只许写一次**；
- 取值前要先用 `set choose <名字> { … }` **定义**过，否则报"未定义的开关"；
- 开关声明**不许嵌在另一个开关体里**（那会让"开没开"变成鸡生蛋）；
- **库不许写 `choose`** —— 库要表达需要就声明能力需求，由项目决定。

**跨文件设定用 `addin`**（`addin chooseset` 拉一个开关设定单元，只在入口那份里写）。
`chooseset.lomt` 长这样（**这一块自己能编**，可以整段拷走）：

```rust
module chooseset            // chooseset.lomt，与入口同目录

addin chooseset             // 开头写它自己（约定）

set choose verbose {
    pub fn banner() -> u32 { return 0x5EED; }
}
```

入口那一份则是（**这一块单独编不过** —— 它的定义在旁边的 `chooseset.lomt` 里，
两半合起来才是完整的例子；可运行的那一对在仓库的 `loment/examples/addin/`）：

<!-- no-compile -->
```rust
module myapp                // 入口

addin chooseset             // 拉进来；它的 `choose` **在整个程序生效**
choose verbose
```

- `addin` 拉进来的单元**只许写 choose 相关代码**（`module` / `addin` / 三种 `choose`）；
  写别的（`fn`/`struct`/`use`）会被拒 —— 要装代码用 `use`（那是库），装开关才用 `addin`。
- **它本身参与编译**，所以体里给入口用的函数要 `pub`。
- **只有入口能写 `addin`**；库或 addin 单元里写了会被拒（写了也不会生效）。
- 开关取值会进 Potato（`switches` 字段），所以"哪些开关开着"在**不读源码**的那一侧也看得见。

### 2.3 自定义语法 `comefor` / `byuse`

**在编译期定义一个新语法。** 定义处那段程序由内核**跑**，它读 token、吐 token。
下面这一份是**完整的、能编过**的例子 —— `def ANSWER = 42;` 被展开成 `const ANSWER: u32 = 42;`：

```rust
module cfdef                    // 仓库里是 loment/comefor/def_dialect.lomt

comefor let "def" to {
    /// 把字符串字面量的字节写进**宏体自己的宿主内存**，再合成一个 token。
    fn emit_s(s: str, k: u64, line: u64, col: u64) -> u64 {
        let n: u32 = str_len(s) as u32;
        let p: ptr = alloc(32);
        let i: u32 = 0;
        while i < n {
            store8(p, i, str_byte(s, i) as u8);
            i = i + 1;
        }
        return ct_syn(k, p, n as u64, line, col);
    }

    /// 吃 `名字 = 数 ;` 四个，吐 `const 名字 : u32 = 数 ;` 七个。
    fn main() -> u64 {
        let buf: ptr = alloc(32);
        ct_tok(0, buf);                         // 名字 -> buf
        let ln: u64 = load8(buf, 12) as u64;    // 行列从源 token 上**读下来**
        let cl: u64 = load8(buf, 16) as u64;
        emit_s("const", 0, ln, cl);
        ct_out(buf);                            // 名字：照抄源 token
        emit_s(":", 3, ln, cl);                 // 3 = punct
        emit_s("u32", 0, ln, cl);
        ct_tok(1, buf); ct_out(buf);            // `=`
        ct_tok(2, buf); ct_out(buf);            // `42`
        ct_tok(3, buf); ct_out(buf);            // `;`
        return 4;                               // 吃了 4 个
    }
}

def ANSWER = 42;                // 从这里到 `byuse`，`def` 是语法

fn main() -> u64 { return ANSWER as u64; }

byuse "def" done
```

**宏体拿到什么**（`docs/184` §3.2）—— 只有这四个，别的内建**在这个语境里不可用**：

| 签名 | 说明 |
|---|---|
| `ct_n() -> u64` | 游标起还有多少 token |
| `ct_tok(i: u64, buf: ptr) -> u64` | 源 token 的字段写进 `buf`；越界返回 0 |
| `ct_out(buf: ptr) -> u64` | `buf` 追加到输出流（文本**按跨度回源里查**） |
| `ct_syn(k: u64, txt: ptr, ln: u64, line: u64, col: u64) -> u64` | 合成一个 token（文本取自**宏体自己的堆**） |

记录 **20 字节**：`+0 kind`（0=ident 1=number 2=string 3=punct 4=eof）`+4 off` `+8 len`
`+12 line` `+16 col`；用 `load8`/`store8` 读写。`off`/`len` 圈的是源里的**原始片段**
（`"hello"` 的 len 是 7，不是 5）。

**规矩**：

- **定义与终止都只能在顶层**，不能嵌套；一个词不许定义两次；
- `byuse` 后面跟的是 `comefor` 里那个名字（`comefor let "def"` → `byuse "def" done`）；
- 展开在**解析之前**的 token 层 —— 所以 `comefor`/`byuse` 不是关键字，你甚至在别处
  可以把 `def` 当普通标识符用（展开只在你声明的那个区间里生效）；
- **位置是宏体的责任**：`ct_out` 吐出的 token 位置自动回到源里那一处；
  `ct_syn` 造的新词要自己填 `line`/`col`。填不对内核**看不出来**，报错会指到别处；
- 体里**暂时不支持 `use`** —— 帮手 `fn` 写在体里面（体是一支完整的程序）。

可运行的例子：`loment/comefor/def_dialect.lomt`（它手写展开后的样子是 `def_hand.lomt`，
判据说这两份编出的 IR **逐字节相同**）。

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
| E20 | **一个文件的 `use` 超过 300 条** —— 门面拆小，别把整库塞进一个文件 |
| E21 | **`extern fn` 的签名超出 FFI 第 1 阶段**（只收标量与 `ptr`）—— 见 §7.3.1 |
| E22 | **`choose` 用法不对**（写两次 / 模式名拼错 / 库写了它）—— 见 §2.1 |
| E23 | **这一版还没实现（编译器限制）** —— **不是你源码的错**，换个写法绕开；绕不开就报告。进度见 `docs/145` 的里程碑表 |

**每条码都有一张完整的说明卡**（错了什么 / 为什么错 / 至少 3 条改法 / 支持与不支持）—— `loment check` 会把它渲染出来；`loment explain E4` 给的是 ASCII 版详解。本表只是速览。

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

11. **`const` 的初值只能是整数字面量** —— 不能是表达式，也不能引用别的 `const`
    （`const B: u32 = A + 1;` 报 `期望 number（整数），得到 ...`）。所以像内存布局那种
    一串偏移量，只能一路把数字写死（`loment/tools/lompkg.lomt` 的开头就是这么写的）。

12. **没有字符串常量**：`pub const NAME: str = "x";` 是**语法错**。要一个字符串标签就写成
    `pub fn name() -> str { return "x"; }` —— `.lomp` 包清单就是这么写的（§9）。

13. **`use` 后面不带分号**：`use mathutil;` 报 `顶层只允许 use/capability/fn，得到 ';'`；
    两种写法（`use 名字` / `use "路径.lomt"`）都不带。

14. **非 void 的函数掉出末尾 → 运行期 SIGILL**。写 `fn f() -> u32 { ... }` 时最后一条**必须**
    是 `return <expr>;` —— 忘了写，**check 照样放行**，跑起来直接 `Illegal instruction`
    （2026-09-15 写 `loment/tools/lomcli.lomt` 时实测，一个 40 行的目录遍历函数就崩在这）。
    这也是为什么早期返回要写成 `return 0;` 而不是裸 `return;`（§6.3）。
    附带：返回类型只用来放行早退的，可以声明成 `-> u32` 然后 `return 0;`，调用方忽略返回值。

16. **输出非 ASCII 会在 Windows 控制台上乱码**。PE 把字节**直接写进控制台**，控制台按
    当前代码页解 —— 中文 Windows 是 **936(GBK)**，于是 UTF-8 的中文被解成 `婧愮爜`。
    **垫片没有 `WriteConsoleW`**，程序这边无从补救。要打印给用户看的东西就**只用 ASCII**；
    ASCII 是唯一在任何代码页下都解码一致的集合。
    （`loment` 自己的帮助与速查页就是被这条从中文改成英文的，见 `docs/169 §3a`。）

15. **`let mut x: T = ...` 里的 `mut` 不是关键字**，是普通标识符（`loment/selfhost/checker.lomt`
    里真有个变量就叫 `mut`）。写 `let mut len: u32 = ...` 等于"声明一个叫 `mut` 的变量、
    后面再跟一个 `len`"，报的是 `期望 :，得到 'm'` 这种**指向别处**的语法错。
    本地变量直接 `let x: T = ...;`，重新赋值不需要任何修饰。

## 7. 两个开口：自己的后缀、自己的 `loment` 命令

这两件事让 Loment 变成一个**能长别的东西的底座**，而不是只有一种文件、一组命令的语言。

### 7.1 自定义源码后缀

**后缀不属于语言** —— 语法里只有 `.lom` 是特殊的（L0 接口契约）。别的后缀统统当 L1 源码，
写 `.lomt` 只是习惯。要让项目用自己的后缀，在**项目根**放一份 `loment.conf`（和 `lompi.conf`
同一个形状：一个词法器扫标签，不需要 `module` 头、不需要整份文件合法）：

```rust
// loment.conf
module conf

pub fn source_ext() -> str {
    return ".foo";
}
```

之后 `use geom` 会去找 `deps/geom/geom.foo`（路径形式本来就不受影响，`use "area.foo"` 照旧）。
三条边界：

- **只管名字形式**。`use "area.foo"` 这种带路径的自己带后缀，配置管不着。
- **默认后缀仍然兜底**：配成 `.foo` 之后，`loment/lib` 这些自带模块（还是 `.lomt`）照样找得到。
- **读不出来就当没配**：没这个文件 / 没 `source_ext` / 值不以 `.` 开头 / 值里带转义 —— 一律退回
  `.lomt`。配置文件会被人改坏，改坏时"退回默认"比"编出一串看不懂的错"好。

工具链旁边那份（`<工具目录>/loment.conf`）是同一个键的**全局默认**，项目自己那份优先。

### 7.2 自定义 `loment` 命令

像 `git`：`loment foo ...` 先看 `PATH` 上有没有 `loment-foo`，有就**原样转发**（`shift` 掉名字，
后面的参数一个不动、退出码原样带出来）；没有才交回官方 CLI（那就是"未知命令"）。所以：

```bash
# PATH 上放一个 loment-git，你就有了 `loment git ...`
loment git status      # -> loment-git status
```

两条边界：

- **官方命令优先**。`loment version` / `loment build` 这些永远走官方实现，PATH 上放一个同名
  的 `loment-version` 顶不掉它。
- **注册方是软件，不是用户配置**。这是一个**文件名约定**（可执行文件叫 `loment-<名字>`），
  没有注册表、没有配置文件 —— 装了就生效，卸了就没了。

## 7.3 用别的语言写的库（FFI）

**两条腿，按"对方是什么"分** —— 挑错了会白折腾：

| 对方是什么 | 怎么用 | 覆盖 |
|---|---|---|
| **C ABI 库**（C / C++ / Rust / Zig / Go(c-archive) / Swift / C#(NativeAOT) / Fortran…） | `extern fn` 声明 + 编的时候 `--link 那个.o` | 所有能导出 C 符号的语言 |
| **运行期**（Python / Java / JS / Ruby / Lua…） | `use proc` 然后 `proc_sh`／`proc_python` 起一个解释器进程，把它的输出读回来 | 所有有解释器的语言 |

### 7.3.1 C ABI 那一族（真链接）

```rust
module ffi_demo

extern fn c_add(a: i32, b: i32) -> i32;    // 只有签名, 末尾分号
extern fn c_free(p: ptr);                  // 不写 -> T 就是 void

fn _start() {
    syscall4(60, c_add(3 as i32, 4 as i32) as u64, 0, 0);
}
```

```
$ cc -c -O1 -ffreestanding -fno-pic lib.c -o lib.o      # 对方的库自己编
$ loment build app.lomt --link lib.o -o app             # 我们链接
```

**签名只收标量**（`i8..i64`/`u8..u64`/`bool`）**与 `ptr`**。`str` 是"指针 + 长度"、
**不是 C 字符串**；结构体按值传要走另一套寄存器分类规则 —— 这两样出现会**报错 E021**，
不静默错编。要传给 C 的字符串得自己在内存里拼一个 NUL 结尾的字节串。

调用点按**平台 C ABI** 传参（Linux: 前六个整数实参进 `rdi rsi rdx rcx r8 r9`；
Windows: `rcx rdx r8 r9`），而 Loment 函数之间的调用照旧走 Loment 自己的约定（实参走栈）。

**对方那份 `.o` 必须是自包含的**（这一档的硬边界，碰到了会**报错**而不是猜）：

- 它里面**不能有重定位**，**不能引用未定义符号** —— 典型就是 `printf`/`malloc`。
  也就是说**不链 libc**：编译对方那份时要按 freestanding 编（`-ffreestanding
  -fno-stack-protector`），它自己只能调用自己。
- 由此**两个 `.o` 也不能互相引用**（那对另一个来说就是未定义符号）。
- 归档（`.a`）与动态库（`.so`/`.dll`）**都还没有**；PE 目标的 FFI 也还没有。
- 一次可以给**多个** `--link`，它们会被依次接在代码后面。
- 对方那个 `.o` **不能超过 4 MiB**（超了会报错，不会截断着编）。

### 7.3.2 Python / Java / JS（进程桥）

```rust
module py_demo

use proc

fn _start() {
    let buf: ptr = alloc(1024);
    let n: i64 = proc_sh("python3 -c 'import json; print(1)'", buf, 1024);
    if n > 0 {
        syscall4(1, 1, buf as u64, n as u64);
    }
    syscall4(60, 0, 0, 0);
}
```

`proc_python("...")` 是 Python 的糖。**换语言就是换命令**，Loment 这边一行不用改。

三条边界：**传的是字节流不是指针**（不能传结构体过去）；**只在 Linux/ELF 上可用**
（Windows 的垫片没有 `fork`/`pipe`，那里返回 -1）；它要求机器上有那个解释器。

**装了包（不是仓库）的时候**：`proc` 不在你的项目里，`use proc` 会说"名字导入找不到"。
包里的那份在 `<前缀>/share/loment/lib/proc.lomt`，按 Loment 的正规做法放进项目就能用：

```
mkdir -p deps/proc && cp <前缀>/share/loment/lib/proc.lomt deps/proc/
```

（`deps/<名字>/<名字>.lomt` 是名字形式的第一层搜索落点 —— 也正是 `lompi` 装库时用的形状。）

## 8. 拿不到源码仓库时怎么办

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

## 9. 下面这些路径**只在源码仓库里有**（没仓库就别去找）

仓库 = FujoOS 的 Loment 线工作树（`loment/`、`lom/`、`tools/loment*.py`、`docs/14?–16?-loment-*.md`）。
有仓库时额外能用的：

- **真要管库，用 `lompi`（§0 那条）** —— 它是**装好的**，不依赖仓库；下面这些 Python 工具是
  仓库里的另一套（`docs/168` 的旧模型，`materialize` 那条路）。两者不是一回事，别混。

- `python tools/loment.py lib ...` —— **库系统**（`docs/168`）：`tree`（依赖树 + 每个库的实例数）、
  `id`（实例身份）、`cap`（能力需求闭包，带来源链）、`check`（冲突）、`materialize`（把嵌套与
  多版本摊成编译器能直接吃的树）。**写库没有新东西要学**：库 = 一个目录；依赖就是源码里的
  `use`（不用另行声明）；导出就是 `pub`；可选的 `pkg.lomp` 里只放两个标签 ——
  `pub fn name() -> str` 与 `pub fn version() -> str`（语言没有字符串常量，所以是函数不是 `const`）；
- `python tools/loment.py diag FILE` —— 把错误码翻译成**中文修复建议**（包内没有这个工具）；
- `python tools/loment.py err 诊断.jsonl` —— **报错器本体**（`loment/tools/lomenterr.lomt`）：
  把编译器 `--diag-out PATH` 吐的结构化诊断渲染成带标题/源行/插入符/建议的样子。
  **包内也有它**，但那条路是**启动器自动起的**（`loment check/build/run` 失败时），
  不是 `loment err` 子命令 —— 所以这里同时是它的开发入口。见 `docs/182` §6；
- `loment/examples/` 全部示例（`tour.lomt` 是全语言导览）；`loment/selfhost/` 是
  "用 Loment 写的 Loment 编译器"；
- 文档：`docs/143`（语言规范）· `docs/146`（能力域形式语义）· `docs/148`（工具链）·
  `docs/158`（冻结面，含"改语言要付什么代价"）· `docs/145`（里程碑）· `docs/154`（状态矩阵）；
- 开发期的编译路径：`python tools/loment.py ir F.lomt > f.ll` +
  `python tools/lomelf.py f.ll --target pe -o f.exe`（**包内没有 Python，这条在包里不适用**）。

**仓库规矩**（有仓库才适用）：`lom/*.lom` 是**跨线接口契约**，改它要走单独提交并知会
compat 线；不要为了某次重构顺手改它。`.lomt` 随便改。
