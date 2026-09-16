# 174 · Loment 0.1.4 Pre2 —— FFI：调用别的语言写的库

> 版本 `0.1.4-pre2` · 显示名 `Loment 0.1.4 Pre2` · 对外 tag `v0.1.4-pre2`
> 上一版 `0.1.4-pre1`（tag `v0.1.4-pre1`）
> 唯一真源：`tools/loment_release.py` 的 `RELEASE` / `RELEASE_NAME`（自举镜像 `loment/tools/lomrel.lomt`）
> 设计：`docs/173-loment-ffi.md`（四阶段全图 + 边界 + **实现状态**）
> 上游：`docs/143` §3.1（语法）· `docs/158` §2/§5（冻结面）

## 0. 一句话

**Loment 程序现在真的能调到别的语言写的库。** 两条腿，按"对方是什么"分：

- **C ABI 那一族**（C / C++ / Rust / Zig / Go(c-archive) / Swift / C#(NativeAOT) / Fortran…）
  —— `extern fn` 声明 + `loment build --link lib.o`。它们导出的是同一种符号与同一套传参约定，
  所以**加一个语言就是加一条构建配方 + 一条判据**。
- **运行期那一族**（Python / Java / JS / Ruby / Lua…）—— 它们不是导出 C ABI 的库，是解释器。
  走新加的进程桥 `loment/lib/proc.lomt`：起一个解释器进程，把代码交给它，把 stdout 读回来。
  对方照旧 `import` 自己的库。

**纯 Loment 程序不受影响**：它仍然是 freestanding 的（无运行时、无 libc、不需要 Python）。
**只有声明了 `extern` 的程序**才带外部依赖 —— 这是用户 2026-09-16 明确要的两条路并存。

## 1. 这一版做了什么

| 变更 | 判据 |
|---|---|
| `extern fn name(a: i32, b: ptr) -> i32;` 进语言（只有签名、末尾分号） | `lomentc_test` 112/112 · `loment_p8_test` 两个实现**逐字节一致** |
| 调用点按**平台 C ABI** 传参（Loment 之间仍走自家约定） | `loment_ffi_test` 里 C / C++ / Rust 三条端到端 |
| `lomelf` 读外部 ELF64 目标文件（含 `.text.*` 分节）、多节拼接、符号解析 | 同上 |
| 诊断码 **E021**（签名形态不支持：`str` / 聚合 / 变参） | `lomentc_test` 的负例 |
| `loment build/run --link FILE.o`（两个启动器） | `loment_cli_test` 32/32 |
| `loment/lib/proc.lomt`：进程桥（`proc_sh` / `proc_python`） | `loment_ffi_test` 的 Python / JS 两条 |

### 已经能被调到的语言（这台机器上真跑通的）

| 语言 | 走哪条腿 | 判据 |
|---|---|---|
| **C** | `--link` | 退出码 52 |
| **C++** | `--link`（`extern "C"` 包装） | 退出码 42 |
| **Rust** | `--link`（`#[no_mangle] extern "C"`） | 退出码 42 |
| **Python** | 进程桥（`python3` + `import json`） | 退出码 9 |
| **JavaScript** | 进程桥（`node` + `JSON.stringify`） | 退出码 7 |
| Java / Zig / Go / Swift / C# / Fortran | 配方在判据与 `docs/173` §2 | 本机没装工具链 → **SKIP** |

**"7 个语言"拆开说**：机制上两条腿各自覆盖一族（7+ 是保守说法）；本机实测覆盖 5；
其余因为**没有工具链**而 SKIP —— 缺的是工具链，不是通路。

## 2. 兼容性（相对 `0.1.4-pre1`）

- **既有程序零改动**：`extern` 是新语法，不用它的程序一个字节都不变（语料 54/54 仍逐字节一致）。
- **纯 Loment 程序仍不需要 libc / 运行时**。带 `extern` 的程序才需要，而且**它自己负责**
  把外部目标文件准备好（`--link`）。
- **新增诊断码 E021**（E001–E021）。码只增不改。
- **自举种子变了**：语言面与链接器都改了 —— 从种子起头重建工具链的人要换新种子。
- **发行包/源码包文件名随版本变**：`loment-0.1.4-pre2-*`（见 `docs/162`）。

## 3. 判据

```bash
python tools/loment_ffi_test.py     # 11/11: C / C++ / Rust 端到端 + Python / JS 桥 + 四条边界
python tools/lomentc_test.py        # 112/112: extern 正负例
python tools/loment_p8_test.py      # 16/16: extern 两个实现逐字节一致 + 语料 54/54
python tools/loment_elf_test.py     # 7/7 · loment_pe_test 9/9 · loment_cli_test 32/32
python tools/ci.py --static-only
```

**证伪（真做过，且第一次没红）**：把 C ABI 前两个寄存器对调 —— 判据**必须红**。第一版判据
用的 C 夹具只有 `a+b` / `a*b`，两者**可交换**，于是对调寄存器结果一模一样、判据照样绿。
改用非交换的 `a-b` 与 `a*100+b*10+c` 之后对调立刻打红（rc=22≠52、rc=213≠123）。
**教训**：测传参次序的判据，夹具里必须有非交换运算，否则测的是"能跑"而不是"传参对"。

## 4. 不主张（这一版的关键缺口）

1. **产品路径目前还做不了 FFI。** 打包出去的 `loment` 用的是**自举**链接器
   (`loment-lomelf`)，而它**还没镜像**这一套 —— 它对 `--link` **硬拒**并指向 `docs/173`。
   也就是说：FFI 在参考实现/开发侧可用，装出来的工具链还不行。**这是下一版的第一件事。**
2. **只有静态链接、只有 ELF。** 归档（`.a`/`.lib`）与动态库（`.so`/`.dll`）都没做；
   PE 侧 FFI 未开始（`--link` 配 `--target pe` 明确拒绝）。
3. **不链 libc。** 引用了未定义符号（`printf`/`malloc`）的目标文件会被**硬拒**——
   第 1 阶段没有能力解开它们。所以能调的是**自包含的**库。
4. **签名只收标量与 `ptr`。** `str` 与按值聚合都在外面（E021）；变参、回调（函数指针）也不支持。
5. **进程桥只在 Linux/ELF 可用**（Windows 垫片没有 `fork`/`pipe`），且**传的是字节流**
   —— 不能把结构体递过去。
6. 其余见 `docs/160 §2` 的不主张清单与 `docs/173` §3。
