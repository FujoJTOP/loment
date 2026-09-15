# Loment 参考表

查表用。叙事与上手见 [SKILL.md](SKILL.md)。

## 内建函数（全部，没有别的）

| 签名 | 说明 |
|---|---|
| `str_len(s: str) -> u32` | 字节长度 |
| `str_byte(s: str, i: u32) -> u32` | 第 i 个字节 |
| `str_eq(a: str, b: str) -> bool` | 内容比较 |
| `str_concat(a: str, b: str) -> str` | 拼接（**走 bump 堆，有 64 KiB 上限**，大串别拼） |
| `str_ptr(s: str) -> ptr` | 数据指针（喂 syscall 用） |
| `alloc(n: u32) -> ptr` | bump 堆分配 |
| `free(p: ptr) -> u32` | 占位（bump 堆不真回收） |
| `load8(p: ptr, off: u32) -> u32` | 读字节 |
| `store8(p: ptr, off: u32, v: u8) -> u32` | 写字节 |
| `ptr_add(p: ptr, n: u32) -> ptr` / `ptr_sub` | 指针偏移 |
| `slice_len(s: [T]) -> u32` | 切片长度 |
| `panic(code: u32) -> u32` | 不可返回（类型仅占位） |
| `atomic_add(p: ptr, n: u32) -> u32` | 原子加 |
| `get_bits(v: u8, hi: u32, lo: u32) -> u8` / `set_bits(v: u8, hi: u32, lo: u32, x: u8) -> u8` | 位域读写 |
| `inb(port: u16) -> u32` / `outb(port: u16, v: u8) -> u32` | 端口 I/O（**仅 Rust 路径**，IR 后端不支持） |
| `syscall4(nr: u64, a0: u64, a1: u64, a2: u64) -> i64` | 裸 syscall（rax/rdi/rsi/rdx） |
| `syscall6(nr: u64, a0..a4: u64) -> i64` | 同上，多两个参数 |

**没有标准库**：没有 `String`/`Vec`/`HashMap`、没有 I/O 封装、没有字符串格式化。
要输出数字就 `alloc` 一块 + `store8` 拼十进制（照抄 `loment/examples/all_loment.lomt` 的
`write_dec`）。要读文件就自己发 syscall。

## syscall 的可移植性（重要）

Linux ELF 目标上，syscall 号就是 Linux 的。**Windows PE 目标上只有一个 8 个号的 shim**：

| 号 | 名称 | 号 | 名称 |
|---|---|---|---|
| 0 | `read` | 60 | `exit` |
| 1 | `write` | 217 | `getdents64` |
| 3 | `close` | 257 | `openat` |
| 12 | `brk` | 262 | `newfstatat` |

**其余号返回 -1**（不是报错，是静默的失败）。要写跨平台（本机 Windows 也能跑）的程序，
就按这 8 个来。`/proc/self/cmdline` 在 PE 上由 shim 从 `GetCommandLineA` 合成，
所以 argv 的读法两边一致。

## 完整语法骨架

```
module <ident>                                       // 首行, 无分号

use "<path>.lom"                                     // L0 布局/常量 (可多次)
use "<path>.lomt"                                    // L1 模块导入 (可多次)

capability <name> : <space>[<lo>..<hi>] [revocable]
excluded "<说明>"                                    // 出界声明
const NAME: <int-type> = <int>;
struct S { <field>: <type>, ... }
enum E { A, B(<type>) }
trait T { fn m(self) -> u32; }
impl T for S { fn m(self) -> u32 { ... } }

pub fn f(<arg>: <type>, ...) -> <type> {
    let x: T = <expr>;
    x = <expr>;
    if <expr> { } else if <expr> { } else { }
    while <expr> { }
    for i in <lo>..<hi> { }
    match <expr> { E::A => { } E::B(v) => { } _ => { } }
    if let E::V(x) = <expr> { } else { }
    guard <cap>(<expr>);
    return <expr>;
    <call>;
}
```

- 类型：`u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str`、struct 名、`[T; N]`、`[T]`、`mut [T]`、
  泛型实例 `Name<A, B>`；
- 表达式：字面量（十进制 / `0x` 十六进制）/ 标识符 / 调用 / `a.b` / `a[i]` / `E::V` /
  `E::V(e)` / `S { f: e }` / `[e, ...]` / `x as T` / `a?` / 一元 `- ! & &mut` / 二元 / 括号；
- 运算符优先级（低→高，同 Rust）：`||` · `&&` · `== !=` · `< <= > >=` · `|` · `^` · `&` ·
  `<< >>` · `+ -` · `* / %`；
- 注释 `//` `/* */`；`///` 是文档注释。

## 错误码（`loment diag` / `lsp --check`）

码按**修法**分，不按消息措辞分。

| 码 | 要你做的事 |
|---|---|
| E001 | 类型对不上（含 return / 载荷 / 内建实参） |
| E002 | 有东西没声明（未声明变量 / 未定义函数 / 未知类型，含"先用后定义"） |
| E003 | 实参个数不对 |
| E004 | 能力域问题（未声明 / 越界 / 重复） |
| E005 | 用了 `excluded` 声明出界的空间 |
| E006 | 值已被移动 |
| E007 | 借用冲突（可变借用与借用并存 / 可变借用两次） |
| E008 | `match` / 枚举（不穷尽、模式重复、不是枚举、载荷绑定不对） |
| E009 | 命名与基类型冲突 / 空 struct / 空 enum |
| E010 | `?` 用在了不是 `Result`/`Option` 的地方 |
| E011 | 切片可变性（只读切片不能写、形参要 `mut`） |
| E012 | 悬垂引用 |
| E013 | 重复定义 / 重名 |
| E014 | 赋值目标不是左值 |
| E015 | 字段与下标（无此字段、缺字段、重复初始化、对非结构体取字段…） |
| E016 | 数组字面量 / 长度与声明不符 |
| E017 | `as` 转换非法 |

## L0（`.lom`）—— 别随手改

`.lom` 是**接口层契约**，不是实现：一处声明、多处生成（`spec.json`、Potato 形式对象、
内核侧的常量）。语法是 Rust 风味的 `const` / `enum` / `record` / `param`，
顶点是 `lom/fuai.lom` 与 `lom/fuc.lom`。

**改它的规矩**（CLAUDE.md）：单独一次提交、commit 里写明谁依赖它 / 变了什么 / 下游要不要动、
并知会 compat 线；不要为了编译器方便去改它 —— 优先在内核侧做适配层。
新增或删除原语等于契约升版，会触发下游针对枚举计数的断言（**那是该看见的信号**，
不要靠放宽断言消掉）。

写程序基本用不到 `.lom`；它出场是在你要定义内核接口常量的时候。

## 自举链路（想知道"这语言自己怎么写自己"）

`loment/selfhost/` 是忠实实现，按依赖序：

| 文件 | 干什么 |
|---|---|
| `lexer.lomt` | 源码 → token 记录（20 字节/条） |
| `parser.lomt` | token → 规范 AST dump |
| `checker.lomt` | 符号表 + 类型/调用/移动/借用/能力检查 |
| `codegen.lomt` | AST → LLVM IR |
| `driver.lomt` | 装载 → `use` 解析 → 检查 → 发射（就是发行包里的 `loment-driver`） |

`loment/tools/*.lomt` 是用 Loment 重写的用户侧工具（`lomfmt` / `lomdoc` / `lsp` / `lomrel` /
`lomelf`），每一个都与对应的 Python 版**逐字节等价**，判据在 `tools/loment_*_test.py`。

## 本机门禁的固有红项（别当成自己弄坏了）

`python tools/ci.py --static-only` 在本机会有几项固定失败，属环境/姐妹库缺位：

- 缺 `LinuxFUAI/` 姐妹库 → `lomc_test` 20/22、`lom_audit`、`potato_cross`、`fuai_contract_check`；
- 缺内核/ QEMU 构建产物 → `loment_p7_test` 7/8；
- 缺 VS Code 路径 → `loment_filetype_test` 1/4。

除这些以外**任何**红项都是真回归，去看，别绕。

**唯一常见的假红**是 `tools/loment_release.py` 的 C9（工件 sha256）：只要你在上次 `--emit`
之后动过 `tools/*.py`、`loment/tools/*.lomt` 或那几份 Loment 文档（哪怕只补一行注释），
清单就过期了。重跑一遍即可：

```bash
python tools/loment_release.py --emit
```

改完 Loment 相关文件后**主动重跑一次** —— 否则下一次别人跑门禁时会看到一条莫名其妙的 DIFF。
