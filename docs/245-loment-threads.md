# 245 · 真线程 `thread_spawn`，与「函数指针」那条线怎么分家

> 状态: **阶段 1 已落地**（2026-10-14）· 判据: `tools/loment_thread_test.py` +
> `loment_elf_test::test_selfhost_mirror_matches_reference_on_thread_spawn`
> 本文按 `docs/176` §7.3 的规矩先立规范与落点 —— **没有落点的那条，就是这一改动的真实代价**。

## 0. 起因

`os` 库（`docs/242`）落地后要补**真线程**。一动手就撞到两件**实测出来的硬事实**（不是推断），
它们一起决定了这件事该怎么做。

## 1. 硬事实一：`clone(CLONE_THREAD)` 能起真线程，但语言层判父子是**结构性**竞态

`clone` 起线程时只换 `rsp`（`child_stack`），**`rbp` 是继承的**。而本仓的原生后端
**所有局部与临时值都是 `rbp` 相对寻址** —— `tools/lomelf.py` 里一律
`lea(reg, RBP, off)` / `load_sized(reg, RBP, off, sz)`。于是子线程的**每一次读写都落在
父线程的帧里**：

* `clone` 返回值的落点就是那个共享槽；父写 tid、子写 0，**谁后写谁赢**；
* 于是 `if r == 0` 这句判断**本身就在读共享内存** —— 它不是"偶尔错"，是结构上不安全。

**实测**（2026-10-13，ELF 在 WSL 里跑）：第一次 `spawn` 的子线程**始终没走 `r == 0` 那条路**，
却出现了"第二个子线程" —— 那是子线程被当成父线程接着往下跑了。

**所以"起线程"这件事只有编译器能解**：子线程一落地就把 `rbp`/`rsp` 一起换成自己的栈，
然后**直接** `call @入口`。

## 2. 硬事实二：光有"函数地址"不够

`clone` 的**系统调用**签名是 `(flags, child_stack, ptid, ctid, newtls)` —— **没有入口参数**；
子线程继承 RIP，从系统调用**后面那条指令**继续跑。所以拿到一个函数地址也**没有地方放它**：

* 要么语言能"通过地址调用"（间接调用，今天没有）；
* 要么像 glibc 那样把入口压在栈顶、让子线程一进来就 `ret` 跳过去 —— 而那要求**挨着
  `syscall` 的那条指令就是 `ret`**，那是编译器发出来的代码，不是 Loment 能写的。

**用户 2026-10-13 的裁决：两条线都做，用 `choose` 开关分家。**

## 3. 两条线

### 线 A —— `thread_spawn`（默认可用，**不动任何既有保证**）· **已落地**

```rust
let tid: i64 = thread_spawn("worker", stack_top, arg);
```

* **入口是字符串字面量**，不是表达式。理由不是审美：产物里那是一条**写死的调用边**
  （`call @worker`），名字必须在编译期定下来；写成变量只会在链接期变成"未定义的标签"。
  这也让三条检查**在编译期做得了**（§4）。
* **结尾是直接调用**，所以 `lomelf` 那条 `Unsupported("v0 不支持间接调用")` 一个字没动，
  **静态调用图仍然是静态的** —— `docs/208` 那两条承重门禁（`closed` / GC 可达性）的前提
  一个字没改。
* **只在 ELF 目标可用**。Windows 没有 `clone`，PE 目标**响亮地拒绝并点名**，不静默错编一个
  "假线程"（`test_pe_refuses_thread_spawn`）。

**语义（定死在这里）**：

| | |
|---|---|
| 入口签名 | 恰好**一个**形参（整数类型） |
| 子线程结束 | 它自己结束自己（`exit` = 60，**不是** `exit_group` = 231 —— 后者会把整组杀掉） |
| 退出码 | 入口的返回值 |
| 返回值 | 新线程的 tid；失败时是负数（内核的负 errno） |
| 栈 | **调用方给**（`stack_top` 是栈顶，往下长） |

**发出来的东西**（两侧**同一串字面机器码**，`c` 是调用点）：

```asm
                            ; 进门前: 子线程实参 → r9, 栈顶 → rsi
  BF 00 0F 05 00            mov  edi, 331520   ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM
  31 D2                     xor  edx, edx      ; ptid  = NULL
  45 31 D2                  xor  r10d, r10d    ; ctid  = NULL
  45 31 C0                  xor  r8d, r8d      ; newtls = 0
  B8 38 00 00 00            mov  eax, 56       ; clone
  0F 05                     syscall
  48 85 C0                  test rax, rax
  75 13                     jne  .parent       ; 父线程: rax = tid
  48 89 E5                  mov  rbp, rsp      ; **子线程**: 换掉继承来的 rbp (硬事实一)
  41 51                     push r9            ; 实参 → 被调方的第 0 个形参槽
  E8 rel32                  call @入口          ; **直接调用**
  89 C7                     mov  edi, eax
  B8 3C 00 00 00            mov  eax, 60       ; exit
  0F 05                     syscall
```

两条容易看漏、但**少了就错**的：

1. **实参走 `r9` 而不是栈**。`syscall` 只踩 `rcx`/`r11`，不碰 `r9`；而子线程在 `clone`
   那一刻**继承整个寄存器组**（只有 `rsp` 被换成 `child_stack`、`rax` 置 0）。栈顶那个
   字节属于**父线程的帧**，子线程不能读。
2. **`jne` 那个位移是按后面 19 字节算出来的**（3+2+5+2+5+2）。少一个字节就跳过 `.parent`
   落到子线程那条路上 —— 而这是两侧各写一遍的**字面**序列，所以
   `test_selfhost_mirror_matches_reference_on_thread_spawn` 逐字节钉着它。

**`clone` 的 flags 一个字都不能错**：`0x50F00` = 331520。少一个 `CLONE_*`（或多一个
`CLONE_PIDFD`）内核直接 `EINVAL` —— 而**产物照样生成成功**。第一版写的是
`0x51300`（332544，少了 `CLONE_SIGHAND`、多了 `CLONE_PIDFD`），是**跑起来**才发现的。

### 线 B —— 函数指针 + 间接调用（挂在 `choose` 开关下）· **未开始**

`fn(A, B) -> C` 类型 + `fn_addr(name) -> u64` + 间接调用 `p(a, b)`。两个必须写在明处的代价：

1. **它是当年刻意排除的。** `docs/232:179-190`（§3.3）：本语言没有函数指针，所以 `qsort`
   的第四个参数是 `kind` 而不是比较函数 —— 它把"按什么比"从**运行期的一个地址**变成
   **产物里的一个常量**，于是"这个排序按什么比"是**不读源码可判的**。
2. **它会掀掉承重门禁的前提。** `docs/208:188-220`：**静态调用图**是 `closed` 与 GC 可达性
   两条门禁的前提；间接分派/间接跳转一进来，那两条的前提要重写。

**开关的形状**：与 `choose vm` 同一条**渠道闸**（`docs/231` §2 —— 读同一个渠道常量，打包期
只换那一行）。**发行版里是编译错误并点名**，于是默认产物保持"静态调用图"那条保证，而
"这份产物有没有间接分派"落在 **Potato 产物**里 —— 仍然是**不读源码可判**的。

## 4. 落点表（`docs/176` §7.3：两个实现各找一个落点；阶段 1 已填）

| 规则 | 参考侧 `tools/lomentc.py` / `tools/lomelf.py` | 自举侧 `loment/selfhost/` 与 `loment/tools/` |
|---|---|---|
| 内建名 + 签名 | `BUILTINS`：`("str","u64","u64") -> i64` | `checker.lomt is_builtin :520` 的名字串；`chk_builtin_arity` / `chk_builtin_arg` / `chk_builtin_ret` |
| 入口三条检查 | `_walk_expr` 的内建分支（字面量 / 名字合法 / 本单元里恰好一个形参 / 已存在） | `chk_tex_atom_call` 内建分支 + **新助手 `chk_lookup_lit_name`**（按字符串字面量的**正文**查表 —— 字面量 token 的 `tok_start` 指着开引号、`tok_len` 含两侧引号） |
| 发射 | `_Ir.expr`：`call i64 asm sideeffect "thread_spawn @入口", …` | `codegen.lomt expr_atom` 的 `thread_spawn` 分支 |
| 链接器 | `Emitter._call` 分派 → `_call_spawn`（`_SPAWN_HEAD` / `_SPAWN_TAIL`）；`CALL_ASM_RE` 改成**捕获 asm 名字**并分派 | `do_call` 分派 → `do_call_spawn`（同一串字面字节） |

**入口名走"字符串字面量"而不是"裸名字"**，是刻意选的：裸名字在语言里**不是表达式**，
要支持它得动解析器，而解析器一动，**所有读 L1 源的入口**（两个 LSP、`lomfmt` 两份、
`lomdoc` 两份、编辑器语法……见 `docs/182` §1.9）都得跟着过一遍。字面量是**已经存在的
表达式形状**，对它们全部透明。代价是入口看起来像字符串 —— 用编译期的三条检查补回来。

## 5. 判据（`tools/loment_thread_test.py`，6 条）

| 判据 | 钉什么 |
|---|---|
| `test_threads_accumulate_on_shared_memory` | N 个线程在**共享**计数器上各原子加 `ROUNDS` 次，和 == `N × ROUNDS`。一条同时钉住 flags 对不对、`CLONE_VM` 在不在、子线程是不是真的从入口跑 |
| `test_each_thread_is_a_distinct_thread` | 每个工作线程写下自己的 `gettid()` —— 必须**互不相同**且**都不等于主线程**。这是"真线程"的**证伪面**：换成顺序调用，上面的和**照样对**，tid 会全相同 |
| `test_entry_must_be_a_string_literal` | 入口写成变量 → 编译期报 |
| `test_unknown_entry_is_a_compile_error` | 入口不在本单元 → 编译期**点名**，不留到链接期 |
| `test_entry_must_take_exactly_one_parameter` | 入口两个形参 → 编译期报。这条不是洁癖：子线程只压一个槽，第二个形参读到栈上的垃圾，是**静默错编** |
| `test_pe_refuses_thread_spawn` | PE 目标拒绝并点名 |

外加 `loment_elf_test::test_selfhost_mirror_matches_reference_on_thread_spawn`：同一份 IR
过**参考链接器**与**自举镜像**，产出的 ELF **逐字节相同**（那条字面序列两侧各写一遍，
漂一个字节别的判据一条都看不见）。

## 6. 这一笔顺带查出来的缺陷（**已修**）

**自举镜像链接器编不了任何用 `atomic_add` 的程序。** `loment/tools/lomelf.lomt` 的
`do_atomic` 只跳过 `atomicrmw `（10 字节），而 IR 里那后面还有**一个操作名**：
`atomicrmw add ptr %v, i32 1 seq_cst`。于是它把 `add` 当成了类型，`skip_sp` 落到 `ptr` 上，
拿值为 `ptr` 去查指针表，报出来的是 `lomelf: 指针值查不到: ptr` —— 离现场很远。
参考实现的正则 `atomicrmw\s+add\s+` 里**一直有那个 `add`**，所以这是镜像单方面的漏抄。

它潜伏至今的原因是**没有任何语料用过 `atomic_add`**：`loment_elf_test` 的镜像语料
（`CORPUS`）里一份都没有，所以那条"镜像与参考逐字节相同"的判据从没走到这条路。
线程探针的原子累加是**第一次**把它走通（2026-10-14）。

## 7. 刻意**没**做的（记账，不是遗漏）

**`thread_spawn` 没有进 `potato.BOUNDARY_BUILTINS`。** 它确实是一次"越过语言保证的每一步"
（发的是 `clone`），但那份清单的**分类是两族**：`syscall*` 一族、裸指针变换一族
（`ptr_add`/`ptr_sub`/`str_ptr`），加一族自声明的 `extern`。`thread_spawn` 是**第三类**，
硬塞进任一族都会让那个计数说假话，而加一族是 **Potato 产物 schema 的改动**
（`potato.lomt` / `lompotato.lomt chk_*` / `lomcli` 三处 + 两条计数判据），该单独一笔做。
在那之前，**用 `thread_spawn` 的程序，Potato 的边界计数是少报的**。

`BOUNDARY_BUILTINS` ⊆ `lomentc.BUILTINS` 那条判据不受影响（它只要求子集关系）。

## 8. 阶段 2 / 3（未开始）

* **阶段 2 · `os/thread` 库**：`lompi/store/os/0.1.0/thread.lomt` —— `thread_spawn` 包装、
  `gettid`、`mmap` 拿栈、futex 互斥/条件变量、`join`。判据进 `loment_os_test.py`
  （同一个 WSL/ELF 环境）。
* **阶段 3 · 函数指针 + 间接调用（线 B，开关下）**：按 `docs/158` §5 四步；两个链接器都拒绝
  间接调用（`tools/lomelf.py:1083` / `loment/tools/lomelf.lomt`），要四份实现一起改；
  外加 `docs/208` 那两条门禁前提改成"带开关的模式"。消费方清单见 `docs/182` §1.9
  （两个 LSP 各带自己的类型表、`lomfmt` 两份、`lomdoc` 两份、`lomcli` 五张表、
  `trans_core.py:519` 七门翻译器、`potato` 的词法计数器、编辑器两份、`lompotato chk_types`）。

## 9. 约束（这一笔守的）

1. `docs/158` §5：两个实现在**同一次提交**；判据 `loment_rule_parity` 与
   `loment_tools_test::test_m81_builtin_tables_match` 两边都过。
2. `docs/176` §4 A：**语言面改动两条提交**（手写 / `--emit` 产物）。种子
   `loment/build/selfhost_driver.ll` 重算，不手工合并。
3. 门禁口径是**不新增红**（`.github/gate-baseline.txt`）。
4. **不碰 `lom/*.lom`**（L0 跨线契约）；两条线都在 L1。
