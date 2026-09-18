# 189 · 完全自举：0.1.4 正式版之前移除全部非 Loment 代码

> 用户 2026-09-18 定的：**「0.1.4 正式版之前务必移除所有非 Loment 代码，包括全部
> rust/py/c/java 的代码，确保完全自举」**。
>
> 上游：`docs/159`（无 Python 自举 —— 那份把去 Python 推到了"用户侧工具链 + L0 生成器
> + 构建路径 + clang"全部有 Loment 实现）。本文档做的是**剩下的那一块**。

## 0. 这一句关掉了哪条路

`docs/159` §4 那张表最后一格写的是：

> Python（**测试侧**）… **不影响"用户构建/使用 Loment"，只影响开发期判据** …
> 把一致性套件写成 Loment 程序（下一批），**或保持 Python 作为"第三方审计工具"——
> 两条路都合理**

**用户这一句把"保持 Python"那条关掉了。** 两条路只剩一条：判据要变成 Loment 程序。

**这一句也让"移除"有了一个明确的对象**：不是"少依赖"，是**仓里不许有**。

## 1. 实测清单（数出来的，不是估的）

`git ls-files` 逐扩展名统计，**175 个非 Loment 代码文件**：

| 语言 | 在哪 | 个数 | 例 |
|---|---|---|---|
| **Python** | `tools/` | **86** | `tools/_safepath.py` |
| **Rust** | `loment/build/` | **25** | `loment/build/cap_asserts.rs` |
| **C** | `loment/build/` | **15** | `loment/build/native_agg_driver.c` |
| **LLVM IR** | `loment/build/` | **13** | `loment/build/native.ll` |
| Rust | `kernel/` | 8 | `kernel/src/capability.rs`（vendored） |
| Python | `loment/pytrans/` | 5 | 表层语法语料 |
| C | `sdk/` | 4 | `sdk/linux/m120_distill.c`（vendored + 语料） |
| Vim script | `editors/vim/` | 3 | 语法高亮 |
| C 头 / Python / Rust | `lom/build/` | 3 / 3 / 3 | `fuai.h` / `fuai.py` / `fuai.rs` |
| JavaScript | `editors/vscode/src/` | 2 | 扩展本体 |
| PowerShell / shell | `scripts/`、`loment/`、`tools/` | 2 / 2 | 启动脚本 |
| 链接脚本 | `loment/build/` | 1 | `loment.ld` |

**Loment 侧**：281 个 `.lomt` + 6 个 `.lomp` + 3 个 `.lom`。

## 2. 按"移除它会破坏什么"分五类 —— 每一类的去路都不同

### A. 判据（Python 86，`tools/` 的大头）—— **必须搬进 Loment**

`ci.py` / `loment_*_test.py` / `lomc_test.py` / `potato_test.py` …

**这是最大的一块，也是唯一一块"没有替代品就动不了"的。** 判据是给别的改动兜底的；
先拆它等于把安全带解了再开车。

**模板已经有了**（`docs/159` §4b）：同一份源码喂**两个实现**、比 stdout **逐字节相同**
—— `lomfmt` / `lomdoc` / `lompkg` / `lomc` / `lomrel` / `lomstatus` 全是这么落的。
判据的迁移照这个来：**Loment 版判据 + 与 Python 版逐字节相同**，直到 Python 版可以拆。

### B. 参考实现（`tools/lomentc.py` / `lomc.py` / `lomelf.py` …）—— **排在 A 之后**

它是"两个实现逐字节一致"（`docs/158` §5）里的**对照面**。

⇒ **移除它 = 那条开发期纪律没有对照物了。** 那**是对的**（自举完成时本来就只有一个
实现），但**时机**必须排在 A 之后：判据先搬进 Loment，再拆参考实现。
**顺序反了的话，回车键前一刻没有任何东西能告诉你搬对了没有。**

### C. `loment/build/` 里那些 Rust / C / LLVM IR（53 个）—— **要用户点头**

**它们不是手写的，是生成物。** 谁生成的决定了去路：

* `.rs` / `.c` / `.py` —— **`lomc` 的后端**（`tools/lomc.py:546/571/613` 与它的 Loment
  孪生 `loment/tools/lomc.lomt`）。**那正是 `lomc` 存在的理由**：L0 契约要交付成
  Rust / C / Python，给 FujoOS 那一侧用（`lom/fuai.lom` → `fuai.h` / `fuai.rs` /
  `spec.json`）。
  **⇒ "移除全部 rust/py/c"与"L0 契约要交付成 rust/c"是同一件事的两面。**
  这条**不能自己定**：要么下游改吃 `.lom`，要么这一条指令对 `lom/build/` 不适用。
* `.ll` —— IR 种子/中间物。`genesis` 落地之后编译用户程序**不需要**它，但它是**链条起点**
  （`docs/159` §3）。属于 F 那一类。

### D. `kernel/` `sdk/`（Rust 8 + C 4）—— **要用户点头**

它们是**从 FujoOS 拿来的只读对照物**（`CLAUDE.md` 第 1 条、`loment_publish.COUNTERPARTS`）。
`lom_audit` / `fuai_contract_check` / `lomc_test` 靠它们做**逐字段对账**。

**⇒ 移除等于"L0 契约在本仓没有对照面"** —— 而 `CLAUDE.md` 明写"少了它们判据会**崩**，
不是静默红"。

这条是**跨线**的，不是语言线自己能定的。

### E. `editors/vscode/src/*.js`（2）—— **要用户点头**

VS Code 扩展**只能**是 JS/TS，没有别的选择。移除 = 放弃编辑器支持
（而 `editors/` 是 `docs/157` 花过力气的地方）。

### F. 启动脚本（`.ps1` 2 + `.sh` 2 + `.ld` 1 + `.ll` 13）—— **链条起点的鸡生蛋**

`loment/bootstrap.sh` / `scripts/lomc.ps1` 是**自举链的第一步**。在"从零重建"这个场景里
总得有一个东西先跑起来 —— `genesis` 把这一步压缩到了一小段，但**压不到零**。

`docs/159` 的"仍待做"里记着 **PE64 / 去 WSL / 构建路径去 Python** —— 属于这一格。

## 3. 排期（按依赖，不按大小）

| 步 | 做什么 | 前置 | 谁定 |
|---|---|---|---|
| **S0** | **定 D 与 C** —— 那两条是跨线/产品决定，定了才知道清单要减多少 | — | **用户** |
| **S1** | 把判据搬进 Loment（模板见 §2 A），**一次一格、各带逐字节判据** | S0（清单定了才好排） | 语言线 |
| **S2** | 拆参考实现（§2 B） | S1 全绿 | 语言线 |
| **S3** | `loment/build/` 的生成物（§2 C）：跟着 S0 的结论走 | S0 | 用户 + 语言线 |
| **S4** | 链条起点（§2 F）：PE64 / 去 WSL / 构建路径去 Python | S2 | 语言线 |
| **S5** | `editors/`（§2 E） | 用户对 E 表态 | 用户 |

**S0 不落地之前不动 S1 之后的任何一格** —— 因为 D/C 的结论会改变清单，
先搬的东西可能白搬。

## 4. 这条指令对**手上正在做的活**的约束（现在就生效）

### 4.1 六门表层语法的翻译器

`docs/188` §7.1 那份计划里，六门的翻译器是 **Python**（`tools/ctrans.py` /
`pytrans.py` / …）。按本指令，它们**也要有 Loment 孪生**，否则 0.1.4 之前又得拆。

**⇒ 从第一门起就按 `docs/159` §4b 那个模板做**：翻译器写两份、判据比**输出的 Loment
源码逐字节相同**。这比"先写 Python、以后补 Loment"便宜得多，因为**判据的形状是一样的**
（同一份输入喂两个实现、比 stdout）。

**但要如实说一句**：`lomfmt` / `lomdoc` / `lompkg` / `lomc` 那几块能那么便宜地镜像，
是因为它们**只吃词法层或声明层**。**翻译器要建树**（递归下降 + 类型 + 发射），
在自举侧是个**大得多**的活 —— 这一格的代价要认，别按前几块的经验估。

### 4.2 新加的判据

**照旧**：新判据是 Python（判据搬到 Loment 是 S1 的整件事，不要求新判据一上来就是
Loment）。但**别把 Python 判据当永久物**设计 —— S1 会来搬它。

## 5. 与其它文档的关系

* `docs/159` —— 去 Python 的上游；本文档是它"测试侧"那一格的落地。
* `docs/188` —— 表层语法。§7.1 那六门按本指令 §4.1 加约束。
* `CLAUDE.md` 第 1 条 —— D 那一类的依据（vendored 只读对照物）。
