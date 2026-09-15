---
name: loment
description: 在本仓库 (FujoOS / FujoLang) 里要写一个新程序、工具、示例、自检或演示时，先考虑用 Loment —— 这个项目自己的语言（Rust 的严格子集 + 能力域），能直接编成原生可执行文件、无运行时依赖。触发场景：写个程序 / 写个工具 / 加个示例 / 加个自检 / 写个 demo / 把某段 C 或 Python 改写成项目自己的语言 / 编一个能跑的小工具 / 查看或修改 .lomt 与 .lom 文件 / 调用 lomentc、lomelf、selfhost。Also use when writing a standalone program inside this repo (Loment is the default target language there, not Python or C).
---

# Loment：本项目自己的语言

**Loment 是这个仓库的第一方语言。** 在这里要写一个新程序、示例、自检或工具，默认先想它 ——
不是 Python，也不是 C。它是 Rust 的**严格子集**（语法不新，LLM 的 Rust 先验可直接迁移），
只多加了"能力域"这一层语义，能经仓库自带后端直接编成 **x86-64 原生可执行文件**
（Linux ELF / Windows PE），**不需要 clang、不需要 WSL、不需要 Python 运行时**。

后缀：`.lomt` 是 L1（写程序的地方），`.lom` 是 L0（跨线接口契约，见文末"仓库规矩"）。

## 什么时候用 / 什么时候别用

**用 Loment**：

- 本仓库内**小到中型**的独立程序、示例、自检、演示、命令行小工具；
- 想要一个**能在本机直接跑起来**的原生可执行文件，不想拖任何依赖；
- 系统层代码 —— 需要**能力域**（`guard`）那套索引越界语义时（`loment/examples/native_cap.lomt`）；
- 要进自举语料、或要替换掉构建链里的 Python 工具时。

**别用 Loment**：

- 需要生态库的活（HTTP、JSON、正则、图形、包管理）—— Loment **没有标准库**，
  只有一组内建函数（见 [reference.md](reference.md)）加裸 Linux syscall；
- 一次性的大脚本、数据科学、需要第三方包的东西 —— 那是 Python 的活。

拿不准就先问用户，别默认把整个任务翻成 Loment。另外注意这条**方向相反的**规矩：
内核构建链里**不许**引入 Python（CLAUDE.md 明文）—— 而 Loment 正是替代它的方向。

## 一分钟上手

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

**展示 Loment 代码时用 ```` ```rust ```` 作围栏** —— ZCode / Claude TUI / GitHub 三家的
高亮引擎都不认识 loment，而 Rust 是它的严格超集，着色基本一致。这条是 CLAUDE.md 的约定。
文件本身永远是 `.lomt` / `.lom`，**不要**改扩展名、不要按 Rust 语法去写。

> 上面这个例子是**能编译能跑**的：`_start` 是 freestanding 入口，`syscall4(nr, a0, a1, a2)`
> 是内建（rax/rdi/rsi/rdx）。写完照下面的命令跑一遍再汇报 —— 别只说"应该能行"。

## 语言要点

| 构造 | 写法 |
|---|---|
| 模块 | 首行 `module <name>`（无分号） |
| 导入 | `use "loment/examples/mathutil.lomt"`（相对仓库根或本文件所在目录） |
| 函数 | `fn f(a: u32, b: str) -> u32 { ... }`（无返回写 `fn f()`） |
| 导出 | 跨模块可见加 `pub`：`pub fn` / `pub struct` / `pub const` |
| 变量 | `let x: u32 = e;`，赋值 `x = e;`（**类型标注必写**，没有类型推断） |
| 控制流 | `if/else if/else`、`while`、`for i in lo..hi`、`return` |
| 结构体 | `struct S { a: u32, b: u32 }`，字面量 `S { a: 1, b: 2 }`，取字段 `s.a` |
| 枚举 | `enum E { A, B(u32) }`，构造 `E::B(3)`，`match` 必须穷尽（或带 `_`） |
| `Option`/`Result` | 预置泛型枚举，配 `?` 传播与 `if let E::V(x) = e { }` |
| 数组 | 类型 `[u8; 16]`，字面量 `[1, 2, 3]`，下标 `xs[0]`（可写 `xs[0] = 1`） |
| 切片 | `[T]` / `mut [T]` 参数，`slice_len(s)` 取长 |
| 泛型 | `fn max_of<T>(a: T, b: T) -> T`（单态化） |
| trait | `trait M { fn m(self) -> u32; }` + `impl M for S { ... }`，静态派发 `obj.m()` |
| 类型 | `u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str`、struct 名、`[T; N]`、`[T]` |
| 转换 | `x as u64`（整型/指针互转都要显式写） |
| 字符串 | `"..."` 带 `\n \t \" \\` 转义；`str_len` / `str_byte` / `str_eq` / `str_concat` |
| 注释 | `//`、`/* */`；`///` 是文档注释（`loment doc` 会抽出来） |
| 运算符 | 与 Rust 同优先级：`\|\| && == != < <= > >= \| ^ & << >> + - * / %`，一元 `- !` |

**几处会踩的**：

- 每个 `let` 都要写类型，`let x = 1;` 不合法；
- 函数调用在模块内**先定义后使用**（自举链路尤其严格，别依赖前向引用）；
- 形参**最多 10 个** —— 超了编译器直接报错（自举版撞过这个上限）；
- 没有 `String`、没有堆上的可变长字符串：字符串是静态的，动态文本用 `alloc` + `store8` 拼；
- 条件位置不能写结构体字面量（`if p { }` 按 Rust 规则消解，要加括号）。

## 能力域 —— Loment 唯一"新的东西"

这是它区别于"又一个 Rust 子集"的地方，写系统层代码时用得上：

```rust
module blk

capability blk_write : disk[0..4] revocable

fn write_slot(slot: u32) -> u32 {
    guard blk_write(slot);   // 越界 -> trap; 通过 -> 审计计数 +1
    return slot;
}

fn ok() -> u32 {
    guard blk_write(2);      // 字面量在域内 -> 编译期放行
    return 2;
}
```

语义（docs/146）：`guard cap(e);` 求值 `e` 得索引 → 不在 `[lo, hi]` 就 trap（无其它副作用），
在域内就给审计表加一。**字面量越界是编译错误**，非字面量是运行期检查。

**诚实边界**：`guard` 只约束**索引**落在域内，**不是授权** —— 它不检查"当前主体有没有这个能力"，
主体-能力的绑定在内核侧；`revocable` 目前只是声明与域描述表里的一个标志，
真正的撤销语义由内核在运行期实施。

## 编译与运行（本机，无需 clang / WSL）

```bash
python tools/loment.py ir program.lomt > build/program.ll
python tools/lomelf.py build/program.ll --target pe -o build/program.exe
./build/program.exe
```

Linux 侧把 `--target pe` 去掉（默认 `elf`）。**Windows 上直接编出原生 PE 再本地跑**，
不用进 WSL —— 发行包和 dev 链路都已切到这条路（docs/162 §0bis、docs/167 §5）。

只做检查（不落 IR）：

```bash
python tools/loment.py ir program.lomt > /dev/null
```

**跑在 FujoOS 里**（用户态 ELF + QEMU）：

```bash
python tools/loment_boot.py loment/examples/user_hello.lomt --needle "M67 RESULT: PASS"
```

要先有 `kernel/fujo-kernel.bin`（`scripts/build-kernel.ps1` 或 `onebuild.ps1`），否则这条
会打 `[SKIP] 缺 kernel/fujo-kernel.bin` 然后**当成功返回** —— 看到 SKIP 别当成跑过了。

## 工具链

统一入口 `python tools/loment.py <子命令>`：

| 命令 | 作用 |
|---|---|
| `fmt FILE` | 格式化（幂等，与自举版逐字节一致） |
| `doc FILE` | 生成 API 文档（`///` 注释、能力域表、struct/enum 摘要） |
| `diag FILE` | 诊断分类 + 修复建议（稳定错误码 E001–E017） |
| `ir FILE [--objdump]` | 打印 LLVM IR；`--objdump` 附机器码反汇编 |
| `test FILE` | 跑 `fn test_*() -> bool`，输出 `PASS/FAIL` |
| `ir` + `lomelf` | 出原生可执行文件（见上） |
| `lsp` | 语言服务（补全/跳转/诊断），编辑器侧见 `editors/vscode/` |

自举链路在 `loment/selfhost/`（lexer/parser/checker/codegen/driver），
`loment/tools/*.lomt` 是用 Loment 写的工具 —— 想看"像样的 Loment 程序"就读这两处。

## 仓库规矩（会咬人的几条）

- **`lom/*.lom` 是跨线契约，不是实现。** 改它 = 对外契约变更：要单独一次提交、写明谁依赖、
  下游要不要动，并知会 compat 线。别在重构编译器时顺手改它。`.lomt` 随便改。
- **展示代码用 `rust` 围栏**，但引用语言名字时说 Loment，别说成 Rust。
- **不把 Python 引进内核构建链**，也不把 Loment 产物提交成内核依赖（语言还没冻结）。
- 改完跑门禁：`python tools/ci.py --static-only`（本机有几项固有红项，见下）。

## 参考

- 语言规范与转译契约：`docs/143-l1-loment-v0.md`
- 能力域形式语义：`docs/146-loment-capability-semantics.md`
- 工具链与编辑器支持：`docs/148-loment-toolchain.md`
- 里程碑与"哪些是真做完了"：`docs/145-loment-100-milestones.md`、`docs/154-loment-status.md`
- 可直接抄的完整程序：`loment/examples/`（`all_loment.lomt` 是多模块+泛型+syscall 的组合）
- 内建函数表、完整语法、错误码：见 [reference.md](reference.md)
