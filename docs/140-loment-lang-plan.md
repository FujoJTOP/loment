# 140 · 语言立项：Loment —— 从"接口层"到"平台母语"（提案）

> 状态: **提案 / 待用户裁决** · 2026-09-08
> 触发: 用户"为优化 FujoOS / FUAI / LinuxFUAI / FUAI-OS 开发，打造一门全新的语言（如当初 Windows 一样）"
> 一句话: **不要造"世俗新语言"（docs/110 §1 已否决）；要造的是平台自己的接口层与母语。
> 分 L0 / L1 / L2 三层：L0 现在就能做且直接服务论文，L1 / L2 排在投稿之后。**
> 注: Loment 截至目前**未投入开发**，本文是立项提案，不是实现记录。

## 0. 结论先行

1. "像 Windows 一样"的关键**不是发明语法**，而是平台所有者定义开发者接口：Win32 接口面 →
   语言与工具（VB / C# / .NET）把接口变成开发体验 → 生态正反馈。FujoOS 缺的正是这个
   **接口层**，不是一门新语言。
2. 现有痛点的根因**不是 Rust**，而是"同一个接口在 N 种语言里各写一遍"（§2 十个痛点里
   七个是重复声明）。把内核换成新语言而保留多语言胶水，N 只会变成 N+1（新语言 + Rust
   遗产 + C + Python）。
3. 可行路径 = **L0 接口层（现在）→ L1 系统语言 Loment（论文后）→ L2 平台母语**。
   L0 是 L1 的必要前提，且独立于 L1 就有价值；L1 的可行性前提是 §4 的三条破解。

## 1. "像 Windows 一样"的三种读法

| 读法 | 含义 | 本方案 |
|---|---|---|
| A. 造一门新编程语言 | 换掉 Rust，内核用自研语言写 | → L1（论文后） |
| B. 定义平台接口 | 平台定义"怎么在 FujoOS 上写东西"，语言只是外壳 | → L0 + L2（推荐主攻） |
| C. 造语言生态 | 语言 + 工具链 + 第三方开发者社区 | 超出个人学生项目范围 |

Windows 的真实模式是 B：先有稳定接口面，再让语言与工具去消费它。FujoOS 今天的接口面是
**散的**（同一事实在 JSON / Rust / C / Python 各写一份），所以 B 的第一件事是收拢接口。

## 2. 现状取证：十个痛点

| # | 痛点 | 证据（已逐条核实） | 代价 |
|---|---|---|---|
| 1 | FUAI 操作码多处声明 | `sdk/fuai-spec/spec.json` · `kernel/src/syscall.rs:261,343` · `LinuxFUAI/include/fuai.h` | 需 `tools/fuai_contract_check.py` 专职查漂移 |
| 2 | 常量**已实际漂移** | `capability.rs:84,86,107`=45 · `spec.json:24`=45 · `fuai.h:82`=46 · `capability.rs:68` 注释=46 | 双实现与注释三方不一致 |
| 3 | `.fuc` 64B 节点布局三处 | `tools/fuic.py:415`（struct.pack）· `kernel/src/fui/fuc.rs:13`（NODE_SIZE）· `docs/138` | 改一处忘两处 |
| 4 | FUJR `.run` 容器五处实现 | `kernel/src/fujr.rs` · `tools/fujopack.py` · `tools/fujorun.py` · `fujopack/src/main.rs` · `fujorun/src/main.rs` | 合计约 39 KB 平行代码 |
| 5 | 手写 ELF 头 | `kernel/src/ld.rs:78-160` 逐字节写 e_ident / phdr | 无法读、只能数 |
| 6 | 手写 x86 机器码 | `kernel/src/asm.rs:93-250`（`write(0x0F); write(0x05)`） | 同上 |
| 7 | 内核内手扫 JSON | `kernel/src/fujr.rs:182,213` 逐字符找 `"perms"` / `"resources"` | 脆弱、无结构 |
| 8 | 契约谓词双重维护 | `tools/mk_contracts.py:19-24`（dict）vs `kernel/src/boxbridge.rs:191-248`（match） | 顺序/编号靠人工对齐 |
| 9 | 测试三处表达 | C 程序打印 needle（`sdk/linux/*.c`）+ Python 元组（`tools/fujoregress.py:35`）+ JSON 副本 | needle 字符串跨文件重复 |
| 10 | 魔法 node id 跨语言 | `ui/desktop.fui:10-15` 注释定义 100/200/300/400/500 系列 · `kernel/src/fui/mod.rs:404-484,896` 依赖 | 文档即契约 |

**共同根因**：没有单一真源（single source of truth）。**关键推论**：这十个痛点里没有一个是
Rust 造成的——换语言不解决它们，只会增加语言数量。

## 3. 新语言能买 / 不能买

**能买**（针对 §2）：
- 布局与 ABI 一等公民 → 消灭 5、6、7（手写 ELF / 机器码 / 偏移）
- 单源跨后端（内核 no_std / 用户 ELF / Linux C ABI / 宿主工具）→ 消灭 4、9
- 编译期导出机器可读语义模型 → 消灭"读二进制"这类调试（Loment 的原始动机）
- 能力域语法化（capability 成为语言构造）→ 强化 FUAI 的 A1–A4 断言

**不能买**：
- LLM 语料（docs/110 §1 的否决理由：新语言 = 零语料 = Agent 写不了）
- 成熟生态、现有 59/59 回归、tcc 自举链
- 时间：内核 28,976 行 Rust + 样例 19,447 行 C 的迁移是年级别工程

## 4. 零语料问题：三条破解（L1 的可行性前提）

docs/110 §1 的论证必须正面回应，否则 L1 不成立：

1. **语法不新，语义才新** —— Loment 表面语法取 Rust 的**严格子集**（加少量显式扩展），
   LLM 的 Rust 先验直接迁移。先例：C++ 之于 C、TypeScript 之于 JS、Kotlin 之于 Java。
   "全新语言"的"新"落在**语义与工具**，不落在语法。→ 绕开零语料。
2. **表示层外置（Potato）** —— 编译器输出完整机器可读语义模型（类型 / 布局 / 能力 / 契约），
   Agent 读模型而不是读二进制或猜源码。这正是"不再读二进制"的实现机制，且与 docs/110 的
   Potato 定位天然对齐（Potato 保持表示层，不因本方案改变）。
3. **先转译，后自举** —— L1 第一版是 Loment → Rust 的转译器，不写后端。先拿到一个能跑、
   能与 Rust 原版逐位对齐、能进回归的语言，再谈 LLVM / 自研后端。
   **可行性判据 = 能否不写后端就跑起来。**

## 5. 三层架构

### L0 · 接口层（现在可做，且服务论文）

`.lom` 单一真源 → 生成器 → Rust / C / Python / JSON + 一致性检查。
首批覆盖（对应 §2 痛点 1/2/3/4/8）：

- FUAI 操作码表（`sdk/fuai-spec/spec.json`）
- FUI 词汇表（`ui/fui_spec.json`）
- `.fuc` 节点布局（64B 记录）
- FUJR 容器格式
- 契约谓词（`tools/mk_contracts.py`）
- 规则书（`sdk/rulebook/fidelity.csv` → `rulebook.h` / `fjru.bin`）

形态示意（**未定稿**，只表达"一处声明、多处生成"）：

```
record FucNode layout(packed, size=64) {
  kind:  u16 @0
  flags: u16 @2
  x:     i16 @4
  y:     i16 @6
  ...
}

enum Opcode: u16 {
  cfg_get = 0x8106 {
    sig: "a0=key", fujo: "fujo_cfg_get", linux: "fuai_cfg_get",
    semantics: "config read (defaults 1->50 2->0 7->45 8->35)"
  }
}
```

生成物：Rust 常量与解码器、C 头、Python 打包器、JSON 规范、漂移检查器。

**L0 判据（可验收）**：`tools/fuai_contract_check.py` 可退役（漂移在生成期不可能发生）；
§2-2 类不一致在生成期报错；全量回归仍 59/59。

### L1 · 系统语言（Loment 本体，论文期后）

设计目标（按优先级）：

1. 布局 / ABI 一等公民：显式 `layout`，编译期 ABI 校验，禁止隐式布局
2. 确定性：无 GC，显式所有权，no_std 优先
3. 能力域语法化：`capability` 作为语言构造，A1–A4 可静态检查
4. 语义模型导出：每个编译单元产出可审计的 Potato 形式对象
5. 跨后端：transpile-to-Rust 起步 → LLVM / 自研
6. 内核内可用：不依赖宿主，与现有 `asm.rs` / `ld.rs` / `fujocc.rs` 路线衔接

### L2 · 平台母语（"Windows 时刻"）

Loment 成为 SDK 母语：FUI 文档、驱动、用户程序、测试统一；内核自带工具链。
此时"平台定义开发者接口"才真正闭环。

## 6. 与既有决策的对齐（不推翻任何一条）

| 既有决策 | 本方案态度 |
|---|---|
| docs/110 Potato = 表示层，**非**新语言 | **保持**。L1 的语义模型导出即 Potato 载体；不把 Potato 改成语言 |
| docs/110 §1"世俗新语言无意义" | **接受**。L1 用"语法子集"策略正面绕过该否决 |
| docs/98 A6：Loment = 超长期议题，"论文期内不推进" | **尊重**。L1 / L2 排在投稿后；L0 例外——它服务论文的可复现性 |
| docs/57 W16 自举"毕业考试"（tcc 已达成） | 不重复。L1 不以自举为目标，以"消痛点 + 语义模型"为目标 |
| 论文 / 投稿最高优先级（2027-01） | **硬门禁**：L0 之外的任何语言工作不得挤占论文时间 |

## 7. 分期与止损

| 阶段 | 内容 | 判据 | 时点 |
|---|---|---|---|
| P0 | L0 schema + 生成器，覆盖 3 个真源 | checker 退役；回归 59/59 | 可立即（1–2 周） |
| P1 | L0 覆盖全部 6 类真源 | §2 痛点 1 / 2 / 3 / 4 / 8 消失 | 投稿前空档 |
| P2 | L1 语法草案 + transpile-to-Rust 原型 | 一个真实内核模块与 Rust 原版逐位对齐 | 论文后 |
| P3 | L1 后端 + 语义模型导出 | 一个驱动 / 用户程序全 Loment | 学期级 |
| P4 | L2 平台化 | SDK 母语 | 远期 |

**止损线**：P2 若三个月内无法与 Rust 原版逐位对齐 → 回退"L0 + 代码生成"路线，L1 归档。

## 8. 待裁决的三个决策点

1. **定位**：Loment 是"替换 Rust 的内核语言"（A 读法）还是"平台 SDK 母语"（B 读法）？
   两者工作量差一个量级；B 可以包含 A。
2. **语法策略**：严格 Rust 子集（推荐，规避零语料）还是自创语法（docs/110 已否决）？
3. **起点**：先做 L0（推荐，服务论文）还是直接进 L1？

## 9. Potato 与 Loment 的分工（追加 · 回答"俩语言如何分工"）

### 9.1 先摆正范畴：不是一个层次上的两个语言

严格说 Potato **不是语言**——这是 docs/110 §1 的定位修订：它不提供语法，语法属于源语言
（Python / C / Rust），它提供的是**形式表示**。Loment 才是语言（语法 + 类型系统 + 编译器）。
两者不是"两种语言竞争"，而是**编译器与接口**的关系。

docs/110 §3 的 .NET 对照表里，Potato 的"语言"一栏是**空的（无）**——Loment 正好补上这一行：

| .NET | Potato（docs/110 §3） | 加入 Loment 后 |
|---|---|---|
| C# / F# | **无** | **Loment**（人 / Agent 书写的语言） |
| IL + 元数据 | Potato 形式对象 | 不变（Loment 编译器的输出之一） |
| CLR | Potato 运行时（可选层） | 不变 |
| BCL | Potato 基类库 | 由 L0 schema 生成 |
| 规范 / 实现分离 | 规范公开 + 实现闭源 | 不变 |

### 9.2 分工主轴：写 vs 看

| 维度 | Loment | Potato |
|---|---|---|
| 面向 | 作者（人 / Agent **写**系统） | 读者（Agent / 验证器 / 内核**看**系统） |
| 提供 | 语法、类型、所有权、ABI、能力构造 | 无语法；形式对象（结构 / 能力 / 契约 / 产物 schema / 出界声明） |
| 位置 | 编译流程的**输入**端 | 编译流程的**输出**端 + 运行时接口 |
| 消费方 | 编译器 | 内核能力域、A1–A4 验证器、外部 Agent |
| 开源策略 | 全开源（与项目一致） | 规范公开、**实现闭源**（docs/110 §4，唯一战略保留地） |
| 主要风险 | 零语料（→ Rust 子集语法对冲） | 内核侵蚀（→ 规范 / 实现分离对冲） |

**一句话**：Loment 管"怎么写"，Potato 管"怎么看"。两者相遇于**语义模型**——Loment 编译器
必须为每个编译单元产出 Potato 形式对象。

### 9.3 为什么不能合并（两条硬理由）

1. **闭源决策污染**：Potato 是项目唯一刻意闭源层。若 Potato 变成语言，闭源的就成了语言
   实现——没有内核敢构建在闭源编译器上（包括本项目自己）。作为"表示层 + 校验器"，闭源
   实现可接受（公开规范的私有校验器，有先例）。
2. **零语料决策污染**：语言必须有语料才能被 Agent 书写；表示层不需要语料，它消费结构。
   合并等于把 Loment 拖回 docs/110 §1 已否决的"世俗新语言"陷阱。

### 9.4 接缝（seam）：一份映射规范

分工要可执行，接缝必须写成规范而非约定：

- **Loment → Potato**：每个编译单元强制导出（a）导出类型与布局、（b）能力需求与撤销语义、
  （c）契约谓词、（d）出界声明。这是 Loment 规范的一部分，不是可选优化。
- **Potato → 内核断言语义**：形式对象到 A1–A4 断言的绑定属于 Potato 规范
  （docs/59 接口公理是候选语义层，见 docs/110 §6）。
- **判据**：给定一个 Loment 编译单元，一个**独立**的 Potato 校验器能仅凭形式对象判定其
  能力域与契约是否合法——无需读源码，无需读二进制。

### 9.5 最小例子（示意）

```loment
// Loment: 作者面
capability blk_write: disk[0..4] revocable { on_revoke => fallback }
export fn flush(req: FlushReq) contract(hex64) { ... }
```

```json
// Potato 形式对象: 读者面（编译器强制产出）
{"unit": "ahci.flush",
 "capabilities": [{"name": "blk_write", "domain": "disk[0..4]", "revocable": true,
                   "on_revoke": "fallback"}],
 "contracts": [{"pred": "hex64", "mode": "binary"}],
 "layout": {"name": "FlushReq", "size": 32, "fields": ["..."]},
 "excluded": []}
```

内核用它执行能力域，验证器用它跑 A1–A4，外部 Agent 用它理解系统——**三方读同一份形式对象**。

### 9.6 顺序建议（对 docs/110 波次的一个微调）

docs/110 定 Potato 入场于波 C（兼容层 + Agent 运行之后）。本方案建议**提前冻结一个
"Potato v0 接口草案"**（只含形式对象 schema + 校验器接口，不做测量、不发论文），理由：

- L1（Loment）若先落地，会自己发明一套元数据格式，那套格式将成为事实上的第二表示层——
  **正好复现 §2 的 N+1 问题**；
- 先冻结 v0 接口，L1 从第一天就强制导出，返工成本为零；论文三的测量与规范正文不受影响
  （只是把接口草案提前）。

推荐顺序：**L0（单源 schema）→ Potato v0 接口草案 → L1 编译器（强制导出）→
论文三（Potato 规范 + 测量）**。

## 10. 决策记录（2026-09-08，用户指示）

> 用户原话："都一起开发，Loment 改为开源，开发直到彻底完成（我人不在，需要你自己推进）"

1. **并行开发**：L0 / Potato / L1 同时推进，不再"L0 完成后再谈 L1"。
2. **取消闭源保留**：docs/110 §4"Potato 实现闭源"的决策**被本指示覆盖**——全部开源。
   - 代价：失去"防内核侵蚀"的战略保留（docs/110 §4 的原始理由）。
   - 收益：解除 docs/110 §4 自己指出的张力（闭源 vs"证据先行"论文纪律）；规范与实现
     都可复现，论文三的测量因此更强。
   - 记账方式：docs/110 §4 保留原文不动，本决策作为**后置修订**，不篡改历史。
3. **自主推进**：zcode 独立执行到"彻底完成"，不再逐步征求裁决；遇阻塞自行决策并记账。
4. **并行不取消依赖**：L0（单源）→ Potato v0 接口 → L1 编译器，三者可交替推进，
   但 L1 的元数据导出必须走 Potato v0 接口（否则复现 §2 的 N+1 问题）。
5. **不因本决策改变的约束**：论文 / 投稿优先级不变；Mimosa commit 门禁不变
   （提交仍被硬拦，工作保留在工作树）。

> 纠错记账（§2-2）：`cfg7` 的 45 / 46 并非"意外漂移"——spec.json
> `deployment_params.tau_high_default` 已把 fujo=45 / linux=46 登记为**有意的部署参数差异**。
> 真正的缺陷是 `kernel/src/capability.rs:68` 的注释仍写"τ_high 46"，与同文件代码 45 矛盾
> ——属**陈旧注释**，正是 L0 要消除的那一类。

## 11. 进度（2026-09-08 第二轮，自主推进）

| 层 | 交付 | 证据 |
|---|---|---|
| **L0** | `.lom` 编译器 `tools/lomc.py`（enum/record/const/param/meta → Rust/C/Python/JSON + `--check`） | `lomc_test` 22/22 |
| **L0** | 单源：`lom/fuc.lom`（.fuc 布局）、`lom/fuai.lom`（46 原语 + 部署参数）、`lom/fujr.lom`（.run 容器） | 见下 |
| **L0** | 接管真源：`fuic.py` 打包器、`kernel/src/fui/fuc_gen.rs`、`spec.json` 双副本、`fujopack.py` | `fuic --check` 逐字节一致；`spec.json` 语义等价；FUJR 重打包 **16428 B 逐字节一致** |
| **Potato** | 形式对象 schema + 独立校验器 `tools/potato.py`（不 import 任何 Loment 代码） | `potato validate` [OK]；5 类畸形对象被拒 |
| **L1** | 语言 + 编译器 `tools/lomentc.py`（Rust 严格子集 + `capability`；转译到 Rust；导出 Potato） | `lomentc_test` 18/18；`rustc` 编译运行输出 `55/21/8/true/false` 全对 |
| **门禁** | `ci.py --static-only` 纳入 lomc_test / lom_audit / lomentc_test / fuai_contract | **4/4**；内核 `cargo build` 通过；`fujoregress --only 0` PASS |

**首次运行即抓到的真实缺陷**：`capability.rs:68` 陈旧注释（τ_high 46 应为 45），已修。

**仍未做**（诚实记账）：`ui/fui_spec.json`、`sdk/contracts`、`sdk/rulebook` 的 `.lom` 统一
（三者本就已是单源 + 生成，收益低于前三项）；L1 的原生后端与自举；Potato 的波 C 测量。
语言本身已能**写 → 转译 → 编译 → 运行 → 导出形式对象**，端到端闭环。

### 11.1 进度（2026-09-09，P2–P5 完成）

| 阶段 | 交付 | 证据 |
|---|---|---|
| **P2** | 内存与运行时语义（移动/借用、no-alloc、RAII、panic→abort、端口 I/O、原子、位域） | `lomentc_test` 89/89 |
| **P3** | 原生后端 LLVM IR（切片/字符串/泛型/trait/Result/内存/位域/裸入口 + 独立链接脚本） | 双路径差分 10/10 逐值一致；差分抓出 `set_bits` 掩码 bug |
| **P4** | 能力域：`guard` + 编译期边界 + 运行期 trap + 审计计数 + 域表 | docs/146；`lomentc_test` 85/85（当时） |
| **P5** | Potato v1（泛型/切片/字符串/审计站点）+ 强制导出 + 双实现校验 + 波 C 测量 | docs/147；`potato_test` 7/7（33 反例）；`potato_cross` 50/50 |
| **门禁** | `ci.py --static-only` 6 项（+`potato_test` +`potato_cross`） | **6/6** |

里程碑进度 **46/100**（P5 中 M48 的 LLM 臂、M50 的内核侧接入待后续阶段）。

### 11.2 进度（2026-09-09，P6 完成）

| 里程碑 | 交付 | 证据 |
|---|---|---|
| M55–M58 | `lomfmt` / `loment_lsp` / `lompkg` / `lomdoc` | `loment_tools_test` 11/11 |
| M59 | DWARF 语句级行表（`--debug`） | `llvm-objdump -d -l` 出 `toolchain.lomt:7` |
| M60–M63 | `loment ir` / `test` / `bench` / `cov` | `RESULT: 4/4 PASS`；`COV 6/31`；对照表 |
| M64 | 诊断分类 E001–E013 + 修复建议 | 13 条反例全部命中 |
| M65/M66 | 增量构建 + 内容哈希缓存 | 冷 82.6ms → 热 9.0ms |
| **门禁** | `ci.py --static-only` 7 项（+`loment_tools_test`） | **7/7** |

里程碑进度 **58/100**（M56 无编辑器宿主、M59 无变量信息为部分；M48/M50 部分待后续）。

### 11.3 进度（2026-09-09，P7 完成）

| 里程碑 | 交付 | 证据 |
|---|---|---|
| M67/M76/M77/M78 | Loment 程序在 FujoOS 用户态运行（`_start` + Linux ABI） | 串口 4 条 `RESULT: PASS` |
| M68/M70/M71/M74 | AHCI 核心 / 中断 IR 形状 / 模块 ABI / 调度钩子约定 | mock 与形状断言（内核侧接入待做） |
| M69 | FUI Node 打包与 `lom/fuc.lom` 逐字节一致 | `NODE_FMT` 对照 |
| M72 | syscall 层由 `lom/fuai.lom` 生成 | 46/46 opcode 覆盖 |
| M73 | 固定块池 first-fit 分配器 | 复用测试 `1 1 1 1` |
| M75 | `loment dbg` 符号化（源行映射） | `fn fib: 源行 7..16` |
| **门禁** | `ci.py --static-only` 8 项（+`loment_p7_test`） | **8/8** |

里程碑进度 **70/100**（P7 中 M68/M70/M71/M74 为部分：内核侧接入与并发开发线冲突，留待后续）。

### 11.4 进度（2026-09-09，P8 起步）

| 里程碑 | 交付 | 证据 |
|---|---|---|
| M79 | Loment 版 lexer（自举第一步） | 与 Python 版 token 流逐 token 一致（docs/150） |
| 顺带 | 后端真 bug ×2：`else if` 语法、嵌套 `&&` 的 phi 前驱 | 11 条双路径示例输出不变 |
| **门禁** | `ci.py --static-only` 9 项（+`loment_p8_test`） | **9/9** |

里程碑进度 **71/100**（M80–M88 自举主体待做：parser/类型检查/IR/自编译/定点/发布）。
