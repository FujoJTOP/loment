# 167 · Loment 原生 ELF 后端 —— 吃掉 clang 的活（第一格）

> 状态：**进行中**（2026-09-14 起）· 判据 `tools/loment_elf_test.py`（6/6，审计主张 **C19 / C20**）
> 上游：`docs/144` §3 写明"写机器码后端是自举之后的事"——自举已完成，这就是那件事。
> 前情：`docs/159` §4 曾把 clang 列为"地基语言，本次目标明确保留 / 不计划去掉"。
> **本文是对那一条方向的后置更新**：0.1.4 Alpha2 的目标就是把它去掉。

## 1. 为什么卡在 clang 上

clang 在本仓库只出现在两处，但两处都是必经之路：

| 位置 | 作用 |
|---|---|
| `loment/bootstrap.sh:71-74` | 把种子 `.ll` 变成 stage1 —— 链条的第一步 |
| `tools/loment_dist.py:582-589` | 把**每一个**发射出来的 `.ll` 变成 ELF |

运行期的启动器 `loment_dist.py:122-148` 更直白：`loment build` / `loment run` 找不到 clang 就
`exit 3`（"build/run need the foundation compiler"）。根因是仓库里**没有自己的汇编器/链接器**：
编译器只发 IR，IR → 可执行文件这一段一直外包给 LLVM。

WSL 是这条依赖的连带后果：产物是 Linux ELF，Windows 侧只能把整套工具链装进 WSL，
Windows 只留一个 `loment.cmd` 转发（`loment_dist.py:369-374`）。所以"去 clang"和"去 WSL"
是同一根绳子上的两段。

## 2. 这一步做了什么

新增 `tools/lomelf.py`（1177 行，参考实现）：**把 LLVM IR 子集直接编成 x86-64 静态 ELF**。

```bash
python tools/lomelf.py in.ll -o out
python tools/lomelf.py in.ll --check     # 只解析与降级，不落盘
```

三条设计取舍（都是为了让这件事**有界**）：

1. **吃文本 IR，不进编译器内部**。lomelf 读的是 `.ll` 文本，不是 AST。好处有三：不碰冻结面
   里的 `codegen.lomt`/`driver.lomt`；任何产出同一子集 IR 的东西都能喂进来；判据天然干净 ——
   *同一份 IR* 喂 clang 与 lomelf，比产物的行为。
2. **栈机，不做寄存器分配**。每个 SSA 值一个栈槽，表达式经 `rax`/`rcx` 求值。要的是正确，不是快。
3. **调用约定是我们自己的**。实参一律走栈（每标量 8 字节，聚合按 8 字节向上取整），标量返回值
   放 `rax`。**不是 System V** —— 见 §4。

支撑它的既有事实：Loment 发射的 IR 是**有界整数子集**（无浮点、无 `bitcast`/`select`/`cmpxchg`、
无间接调用/闭包、无 `llvm.memcpy` intrinsics——自带运行时 `__loment_memcpy/memset/memcmp/abort`
是 IR 里写的）。指令面就这些：`alloca/load/store/getelementptr/extractvalue/insertvalue`、
整数算术与位运算、`sdiv/udiv/srem/urem`（带除零陷阱块）、`icmp` 全谓词、`trunc/zext/sext`、
`ptrtoint/inttoptr`、`br/switch/phi`、直接 `call`、内联汇编 `syscall`、`atomicrmw add`、`ret/unreachable`。

## 3. 判据

```bash
python tools/loment_elf_test.py     # 6/6
```

| 用例 | 判据 | 结果 |
|---|---|---|
| `test_lomelf_matches_clang_behavior` | 4 个可终止的 `_start` 程序，原生产物与 clang 产物的 **stdout 字节 + 退出码**一致 | 4/4 一致 |
| `test_lomelf_reports_unsupported_instead_of_miscompiling` | 聚合返回值 / 间接调用 / 没有 `_start` 三类输入**必须报错**，不许静默编出错的 ELF | 3/3 报错 |
| `test_lomelf_cli_check_and_usage` | `--check` 不落盘且 rc=0；无参数 rc=2 | ✅ |
| `test_lomelf_compiles_selfhost_ir_without_clang` | **主线**：种子 →(clang 一次)→ stage1 → 发 IR → lomelf 编成 ELF → 跑出 `M67 RESULT: PASS loment-user` | ✅ |
| `test_lomelf_selfhost_matches_reference` | **自举镜像**：`loment/tools/lomelf.lomt`（走种子自举链）对四个语料产出的 ELF 与参考**逐字节相同** | 4/4 |
| `test_lomelf_rebuilds_the_compiler_without_clang` | **重建不需要 clang**：`lomelf(种子)` → 一个能用的编译器 → 它自编译 `driver.lomt` 的产物**== 种子**（1 630 436 B） | ✅ |

语料（只放**会终止**的 `_start` 程序）：

```
user_hello   M67 RESULT: PASS loment-user                        rc=0
bootprobe    M76 sysname=Linux / M76 RESULT: PASS bootprobe      rc=0
selfcheck    M77 RESULT: FAIL getrandom                          rc=1   ← 两边一致 (沙箱无 getrandom)
all_loment   M78 sum=42 double=84 max=84 fib=55                  rc=0   ← 含泛型/多模块/字符串
```

`loment/examples/native_entry.lomt` **不进**这个判据：它是 `while true` 的裸机入口（M32 验证集），
按设计不终止。它属于"能不能编出来"，不属于"跑出来一样"。

主线那条用例是这一步真正的意思：**编译一个 Loment 程序不再需要 clang**。clang 只在"从种子重建
stage1"那一步出现（见 §5）。

## 4. 与 clang 路径的差异（逐条记账，别当成"等价"）

| # | 差异 | 影响 |
|---|---|---|
| 1 | **调用约定是自己定的**（实参走栈，非 System V） | 原生产物 v0 **不给 C 调**；仓库里的 C 夹具（`native_driver.c` 那类）仍走 clang 路径 |
| 2 | ~~聚合返回值不支持~~ **已支持**（隐藏结果指针：调用方在实参之外多压一个缓冲地址，被调方写进去、再用 `rax` 回传） | 2026-09-14 补；这是「能编译编译器自己」的前置 |
| 3 | 间接调用 / 函数指针 / 闭包 不支持 | 同上（语言本身目前也没有） |
| 4 | 浮点不支持 | 语言本身没有（发射面 0 处 `fadd`/`fcmp`） |
| 5 | 变参、结构体动态下标 GEP 不支持 | 同上 |
| 6 | 不做寄存器分配 —— 产物比 `clang -O1` 大且慢 | v0 要的是正确性；性能是后面的事 |
| 7 | 不写 DWARF | `--debug` 的元数据（`!DILocalVariable` 等）被忽略；原生产物没有行表 |

**不覆盖**的东西统一是"报错退出"，不是"编出个错的"——第二条用例专门钉这个。

## 5. 还没做的（这一步只吃了一格）

1. ~~自举侧的镜像尚未落地~~ **已落地**。`loment/tools/lomelf.lomt` 走种子自举链编成二进制后，
   对四个语料程序（user_hello / bootprobe / selfcheck / all_loment）产出的 ELF 与参考实现
   **逐字节相同**（8240 / 8296 / 12392 B…），且产物跑出来的输出与 clang 路一致
   （判据 `loment_elf_test` 的 `test_lomelf_selfhost_matches_reference`，审计主张 **C20**）。
   也就是说：**编译一个 Loment 程序从发 IR 到出 ELF，全程不需要 clang** ——
   clang 只剩"种子 → stage1"这一步（见第 4 条）。
2. **PE64 / Windows 原生（去 WSL）**。复用本文的代码生成，换 object 写出与调用约定；
   `loment_dist.py` 的 Windows 装法从"拷进 WSL + `wsl -e` 转发"改成装原生 `loment.exe`。
3. **构建/发布路径去 Python**（`lom_spec_emit` / `loment_status` / `loment_release` /
   `loment_src` / `loment_dist` / `loment_seed` / `loment_manual` / `lom_audit` / `loment` CLI）。
4. **重建不再需要 clang —— 参考侧已证**：`lomelf(种子) → 编译器 → 自编译产物 == 种子`
   （判据 `test_lomelf_rebuilds_the_compiler_without_clang`，1 630 436 B 逐字节相同）。
   也就是说今天就能**不用 clang** 造出一个能用的 Loment 编译器。
   **还差两步才算真的落地**：
   ① **做这件事的工具得是自举侧的**。镜像 `lomelf.lomt` 这一轮补齐了聚合返回值（隐藏结果指针）、
   多行 `switch` 的逻辑行拼接、以及按种子规模放大的表（全局 2048 / 标签与回填各 16384），
   **已经能把 1.63 MB 的种子编出来**（产出 955 440 B 的 ELF）；但那个二进制跑起来 **SIGILL**，
   而参考实现编同一个种子出的 980 016 B 产物是好的。两份产物的第一条差异出现在第 9328 条指令的
   一个调用目标上 —— 即**镜像漏了某个构造的约 25 924 B**，还没定位到是哪一条。
   **这是当前唯一的硬缺口**，也是"自举侧重建"这句话还没有兑现的原因。
   ② 链条总要有**第一个可执行文件**（genesis，可复现、提交进仓库、有哈希，与 Rust 发 stage0
   同一做法）。**残留的诚实点**：genesis 消不掉 —— 它可复现、有来源，但它是个二进制，不装作没有。

## 6. 落地形态

- 门禁登记：`tools/ci.py` 的 `STATIC_CHECKS` 含 `loment_elf_test`；审计主张 **C19**。
- 工件清单：`tools/lomelf.py`、`tools/loment_elf_test.py` 已进 `loment_release.py` 的 `GLOBS`
  （清单 179 个工件，`--check` 179/179）。
- **不改** L0（`lom/*.lom`）、不改 `codegen.lomt`/`driver.lomt`、不改两后端既有的逐字节等价
  （docs/158 §2）—— 新增的是**第三个后端**，不是在既有后端上动刀。

## 7. 证据

```
python tools/loment_elf_test.py
  PASS  test_lomelf_matches_clang_behavior            # 4 个程序: stdout 字节 + 退出码一致
  PASS  test_lomelf_reports_unsupported_instead_of_miscompiling   # 3 类输入报错
  PASS  test_lomelf_cli_check_and_usage               # --check 不落盘 rc=0 · 无参数 rc=2
  PASS  test_lomelf_compiles_selfhost_ir_without_clang # 种子->stage1->IR->原生产物->M67 PASS
  PASS  test_lomelf_selfhost_matches_reference        # 4 个程序: 自举镜像与参考逐字节相同

python tools/loment_audit.py --json
  [PASS] C19 原生 ELF 后端: ... loment_elf_test: 5/5 通过
  [PASS] C20 自举侧镜像: ... 4 个语料逐字节相同
```

规模：`tools/lomelf.py` **1177 行**（参考）；`loment/tools/lomelf.lomt` **约 2700 行**（自举镜像，
规模：`tools/lomelf.py` **1177 行**（参考）；`loment/tools/lomelf.lomt` **约 2700 行**（自举镜像）；
`tools/loment_elf_test.py` 约 300 行。

**自举链的容量闸门**（写镜像时撞到的，逐条记）：自举 codegen 的**形参上限是 10**
（`loment/selfhost/codegen.lomt` 的表按 10 槽定）—— 超过会让 stage1 直接 SIGILL，
现象是"编译这个文件时编译器自己崩了"，而参考实现编同一份文件完全正常。镜像里
`do_div`/`do_shift` 因此把 `oplen/signed/is_rem` 打包成一个参数。
产物示例：`user_hello` 的 IR 1581 B → 原生 ELF 8224 B（text 714 B / data 32 B / bss 0）。
自举镜像产物与参考逐字节相同：user_hello 8224 B · bootprobe 8240 B · selfcheck 8296 B · all_loment 12392 B。
