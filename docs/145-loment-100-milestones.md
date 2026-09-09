# 145 · Loment 100 里程碑计划

> 状态: **提案 / 执行中**（2026-09-08）· 定位: 超长任务总图（docs/140 三层 → docs/143 L1 → docs/144 原生后端）
> 纪律: 与 FujoOS 同款——**每个里程碑必须绑定一条可复现判据**，证据写进 docs，门禁不全绿不算完成。
> 基线（已完成，记作 M0）: L0 接口层（`tools/lomc.py` + 3 单源 + 审计 + CI 门禁）、Potato v0
> （`tools/potato.py`）、L1 v1（`tools/lomentc.py`，转译 Rust 端到端）、**原生后端 M0**（LLVM IR 标量子集）。
> M1–M100 为待办；每个里程碑独立可验收，允许调整顺序，不允许跳过判据。

## 0. 全局门禁（每个里程碑结束都要跑）

```
python tools/ci.py --static-only                 # 4/4
python tools/fuic.py --check                     # .fuc 逐字节
python tools/lom_spec_emit.py --check            # spec.json 双副本
cd kernel && cargo build --release
FUJO_MON_PORT=14568 FUJO_SER_PORT=14001 python tools/fujoregress.py --only 0
```

## P1 · 语言核心完备（M1–M12）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M1 | 字符串字面量与 UTF-8 字节视图 | `let s: str = "abc";` 转译/IR 双路径输出一致 | ✅ |
| M2 | 字符串操作（`len`/`eq`/`concat`/切片视图） | 三个操作的双路径逐值一致 | ✅ 部分（`str_len`/`str_eq`/`str_byte`；`concat` 阻塞于 M15 堆分配） |
| M3 | 只读切片 `&[T]` | 函数参数传切片，IR 用 `{ptr,len}` | ✅ |
| M4 | 可变切片 `&mut [T]` | 原地写入经双路径一致 | 待做 |
| M5 | 借用检查 v0（最小规则：不别名可变借用） | 3 个正例通过 + 3 个负例报错 | 待做 |
| M6 | 泛型函数（单态化） | `fn max<T>(a: T, b: T) -> T` 双路径一致 | 待做 |
| M7 | 泛型 struct/enum | `Pair<u32>` 双路径一致 | 待做 |
| M8 | 接口/trait（静态派发） | 两个实现通过同一接口调用 | 待做 |
| M9 | 错误处理 `Result` + `?` | 嵌套调用短路语义正确 | 待做 |
| M10 | 内建 `Option`/`Result` 与 match 糖 | `if let` 形式解析并转译 | 待做 |
| M11 | 模块/包系统（多目录、路径解析） | 三目录工程编译通过 | 待做 |
| M12 | 可见性 `pub` 与 API 边界 | 私有符号跨模块访问报错 | 待做 |

### P1 证据（2026-09-08，M1/M2）

```
loment/examples/native_str.lomt
  Rust 路径 (rustc):  5 1 0 1 0 90 99
  IR 路径  (clang):   5 1 0 1 0 90 99   → diff 逐值一致
```

`str` = `{ ptr, i64 }`（UTF-8 字节视图）；字面量 → 模块级 `private constant [N x i8]`；
`str_len` → `extractvalue 1 + trunc`；`str_eq` → 长度比较 + `memcmp`（长度不等直接 false，
避免越界读）；`str_byte` → GEP + `zext i8`。Rust 路径分别降级为 `.len()` / `==` / `.as_bytes()[i]`。

### P1 证据（2026-09-08，M3）

```
loment/examples/native_slice.lomt
  Rust 路径:  10 7      # sum(&a) / first_of(&a)
  IR 路径  :  10 7      → 一致
```

`[T]` = `{ ptr, i64 }`；`&array` 生成切片值（元素指针 + 元素个数）；`slice_len` →
`extractvalue 1 + trunc`；切片下标 → `extractvalue 0 + GEP`。Rust 路径：`&[T]` / `(&a)` /
`.len()`。切片参数在原生路径已放行（`str` 同理），聚合 struct 参数仍拒绝。

## P2 · 内存与运行时语义（M13–M22）

| # | 里程碑 | 判据 |
|---|---|---|
| M13 | 所有权最小规则（移动/借用/复制） | 移动后使用报错；Copy 类型放行 |
| M14 | no-alloc 子集审计（无堆分配） | 编译器报告分配点，no-alloc 模块为 0 |
| M15 | 可选堆分配器接口 | 显式 `alloc` 后运行通过 |
| M16 | 确定性析构（RAII） | Drop 顺序测试 |
| M17 | 无 use-after-free 静态检查（子集） | 5 个负例被拒 |
| M18 | 溢出/除零语义定规 + 检查模式 | 两种模式各一条用例，语义写进规范 |
| M19 | panic/abort 策略（no_std 友好） | 裸机目标下 panic 走 abort |
| M20 | 内联汇编与端口 I/O 原语 | 读写端口 demo 在 QEMU 中生效 |
| M21 | volatile/原子操作原语 | 原子自增在多核下无丢失 |
| M22 | 位域与打包结构 | 与 `.lom` 布局单源一致 |

## P3 · 原生后端（M23–M34）

| # | 里程碑 | 判据 | 状态 |
|---|---|---|---|
| M23 | 聚合类型 IR：struct（GEP） | `Blk` 在原生路径跑出同一结果 | ✅ |
| M24 | 定长数组 IR | `sum_array`/`fill_incr` 双路径一致 | ✅ |
| M25 | 枚举 IR（tagged union） | 无载荷/带载荷枚举双路径一致 | ✅ |
| M26 | `match` 降级（switch + phi） | 穷尽性用例双路径一致 | ✅ |
| M27 | 短路 `&&`/`||`（phi 修正） | 副作用调用只执行一次 | ✅ |
| M28 | 字符串/切片 IR | M1–M4 用例在原生路径通过 | 阻塞于 P1 |
| M29 | 泛型单态化 IR | M6/M7 用例在原生路径通过 | 阻塞于 P1 |
| M30 | 裸机目标 `x86_64-unknown-none` | 产出 `.o` 无 libc 依赖 | ✅ 部分（`-c` 出 1672 B 对象；链接流程未接） |
| M31 | 无 libc 运行时（memcpy/memset 内联） | 链接后无未定义符号 | 待做 |
| M32 | 自定义入口 + 链接脚本（与 FujoOS 对齐） | 产物能被 `kernel.ld` 布局吃下 | 待做 |
| M33 | 中断/异常函数属性（naked/interrupt） | QEMU 中触发中断并返回 | 待做 |
| M34 | 后端一致性差分回归 | IR 路径 vs Rust 路径全用例逐值一致 | ✅（native 7 函数逐值一致） |

### P3 证据（2026-09-08）

```
python tools/lomentc.py loment/examples/native_agg.lomt --emit-llvm loment/build/native_agg.ll
clang -O1 -o native_agg_exe.exe native_agg_driver.c native_agg.ll && ./native_agg_exe.exe
8 110 12 9 11 10          # blk_end / array_sum / Circle(2) / Square(3) / 短路两例
clang --target=x86_64-unknown-none -ffreestanding -c native.ll -o native_bare.o   # 1672 B
```

IR 形态：struct → `{ i32, i32 }` + `getelementptr`；数组 → `[4 x i32]` + GEP；枚举 →
`{ i32, i64 }`（tag + 载荷槽）；`match` → `switch` + `extractvalue`；`&&`/`||` → 分支 + `phi`。
聚合参数/返回值暂不支持（ABI 未定，明确报错而非静默错编）。

## P4 · 能力与安全（M35–M44）

| # | 里程碑 | 判据 |
|---|---|---|
| M35 | `capability` 的运行时表示 | 生成物含域描述常量 |
| M36 | 能力域静态检查 | 越界访问编译期拒绝 |
| M37 | 撤销语义代码生成（revocable） | 撤销后调用返回拒绝 |
| M38 | 审计钩子（每次能力使用） | 审计条目数与调用次数一致 |
| M39 | 与 `kernel/src/capability.rs` 域模型对齐 | 双向 diff 0 差异 |
| M40 | A1–A4 断言的 Loment 表达 | `inv_run` 自检通过 |
| M41 | `excluded` 出界声明强制 | 越界能力声明编译期报错 |
| M42 | 信任自适应域宽接口 | 域宽随质量台账变化可观测 |
| M43 | 能力域模糊测试 | 无越权放行 |
| M44 | 能力安全形式化说明 | docs 定稿并进论文素材 |

## P5 · Potato 与表示层（M45–M54）

| # | 里程碑 | 判据 |
|---|---|---|
| M45 | 形式对象 v1（泛型/切片/字符串） | 新类型全部导出且校验通过 |
| M46 | 编译器强制导出（不可关闭） | 缺形式对象则编译失败 |
| M47 | 独立校验器完备性 | 校验器不 import 编译器代码 |
| M48 | 结构识别转换率测量（波 C 主表） | 三语言 × 两模型对照表 |
| M49 | Python/C/Rust → Potato 转写工具 | 转写率与错误率可复现 |
| M50 | 形式对象 → 内核断言绑定 | A1–A4 由形式对象驱动 |
| M51 | 形式对象版本化与回放 | 旧版本可回放校验 |
| M52 | Potato 规范文本（论文三素材） | 规范 + 测量协议成稿 |
| M53 | 跨实现一致性（FujoOS/LinuxFUAI） | 同一形式对象双实现同判定 |
| M54 | 测量协议与统计 | 置信区间与样本量定义 |

## P6 · 工具链（M55–M66）

| # | 里程碑 | 判据 |
|---|---|---|
| M55 | 格式化器 `lomfmt` | 幂等：格式化两次结果相同 |
| M56 | LSP（补全/跳转/诊断） | 三个能力在编辑器实测 |
| M57 | 包管理器 `lompkg` | 依赖解析 + 校验和 |
| M58 | 文档生成器 `lomdoc` | 从 `.lomt` 生成 API 文档 |
| M59 | 调试信息（DWARF） | 调试器能按源码行断点 |
| M60 | IR 查看器 / 反汇编 | 一条命令看 IR 与机器码 |
| M61 | 内建测试框架 | `loment test` 跑通 |
| M62 | 基准框架 | 与 Rust 路径对比表 |
| M63 | IR 级覆盖率 | 覆盖率报告可复现 |
| M64 | 错误信息质量（含修复建议） | 10 类错误各有建议 |
| M65 | 增量编译 | 改动单文件重编译时间下降 |
| M66 | 构建缓存 | 冷/热构建时间对照 |

## P7 · 内核集成（M67–M78）

| # | 里程碑 | 判据 |
|---|---|---|
| M67 | 第一个 Loment 用户态程序跑在 FujoOS | 输出与宿主一致 |
| M68 | 第一个 Loment 驱动（AHCI 子集） | 读扇区成功 |
| M69 | FUI 运行时用 Loment 重写一个控件 | 桌面渲染无回归 |
| M70 | 中断处理用 Loment | 键盘中断路径生效 |
| M71 | 内核模块 ABI（Loment ↔ Rust 互操作） | 双向调用通过 |
| M72 | Loment 版 syscall 层 | 兼容矩阵不回归 |
| M73 | 内存管理子系统（一个分配器） | 压力测试无泄漏 |
| M74 | 调度器钩子 | 上下文切换计数正确 |
| M75 | Loment 版调试器（"不再读二进制"） | 按符号名断点 |
| M76 | Loment 版 bootprobe | 取代 bootstrap 读二进制 |
| M77 | 内核自检用 Loment 写 | 自检项全绿 |
| M78 | 首个全 Loment 的 demo 程序 | 进回归矩阵 |

## P8 · 自举（M79–M88）

| # | 里程碑 | 判据 |
|---|---|---|
| M79 | Loment 版 lexer | 与 Python 版 token 流一致 |
| M80 | Loment 版 parser | AST 与 Python 版结构一致 |
| M81 | Loment 版类型检查 | 负例集判定一致 |
| M82 | Loment 版 IR 生成 | `.ll` 与 Python 版逐字节一致 |
| M83 | 自编译：编译器编译自身 | 产出可运行二进制 |
| M84 | 三阶段自举定点校验 | 第 2/3 阶段产物逐字节相同 |
| M85 | 自举编译器跑全部测试 | `lomentc_test` 在自举版上通过 |
| M86 | 自举性能优化 | 编译自身时间进入预算 |
| M87 | 引导脚本与发布包 | 干净环境一键引导 |
| M88 | 自举版本发布 | 打 tag + 校验和 |

## P9 · 生态与平台（M89–M96）

| # | 里程碑 | 判据 |
|---|---|---|
| M89 | SDK：Loment 版示例集 | 10 个示例全通过 |
| M90 | 第三方库加载（L0 单源共享） | 外部库接入不复制常量 |
| M91 | 版本与兼容策略 | 语义化版本规则成文 |
| M92 | 跨平台目标（aarch64） | 交叉编译产物可运行 |
| M93 | 语言手册站点 | 手册与编译器同版本 |
| M94 | 教程与迁移指南 | Rust → Loment 迁移案例 |
| M95 | 发布流程与社区规范 | 发布检查单 |
| M96 | 语言稳定性承诺（1.0 冻结） | 冻结后 API 不再破坏 |

## P10 · 论文与验证（M97–M100）

| # | 里程碑 | 判据 |
|---|---|---|
| M97 | 语言设计与实现的论文素材 | 设计决策有据可查 |
| M98 | 与 Rust/C/Zig 的形式化对比 | 对比矩阵成文 |
| M99 | 端到端可复现实验包 | 第三方机器可复现 |
| M100 | 1.0 发布与审计 | 全门禁绿 + 外部审计 |

## 依赖与风险

- **硬依赖链**：P1/P2（语言语义）→ P3（后端）→ P4（能力）→ P7（内核集成）→ P8（自举）。
  P5/P6 可与 P1–P3 并行，但 P5 的 M45 必须早于 P3 的 M28（形式对象要覆盖新类型）。
- **自举前置**：M79 之前语言必须补上字符串（M1–M2）、切片（M3–M4）、模块（M11）、
  文件与动态内存（M15）——否则编译器写不出来。
- **最大风险**：后端与工具链吞掉论文时间。→ 每里程碑结束必跑 §0 门禁；论文优先级不变。
- **止损**：M30（裸机目标）若无法进入 FujoOS 链接流程，P7 降级为"宿主可执行 + 宿主驱动"，
  M79 之后的"替换 Rust 路径"重新评估。
- **环境风险**：回归门禁受端口占用/负载影响，见 [[fujoos-qemu-port-collision]]——先查环境再判回归。
