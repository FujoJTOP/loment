# 159 · Loment 无 Python 自举（种子）

> 判据：`python tools/loment_seed_test.py` 3/3 全绿，其中 `test_seed_bootstrap_fixed_point`
> 跑的是 `sh loment/bootstrap.sh` —— 全过程**只有 clang（地基语言）+ POSIX sh**，
> 没有任何解释器参与。门禁已进 `tools/ci.py` 的静态检查表。

## 1. 为什么需要这一步

自举链在此之前只能从**Python 版编译器**起步：M83 的 stage1 由 `tools/lomentc.py` 现编，
也就是说"想重建 Loment 编译器"这件事本身依赖 Python 解释器。这条依赖比工具链（fmt/doc/LSP）
更根本 —— 它不在使用侧，而在**重建编译器**的那一步上。

把起点固化成工件之后，依赖面变成：

| 环节 | 之前 | 现在 |
|---|---|---|
| 得到 stage1（能编译 Loment 的可执行文件） | `python tools/lomentc.py` 现编 | `clang loment/build/selfhost_driver.ll` |
| 编译任意 Loment 程序 | Python 参考实现 | stage1（自举驱动）+ clang |
| 验证与参考实现一致（判据） | Python | Python（**测试侧**，不是产品路径） |
| 表单/文档/LSP/包管理/发布 | Python | Python（下一批） |

## 2. 种子是什么

`loment/build/selfhost_driver.ll`（1.63 MB）= 参考实现为 `loment/selfhost/driver.lomt`
发射的完整单元 IR（含 `use` 闭包：lexer / checker / codegen / ir_*）。

自举驱动本身就是**整个编译器**：它从 `/proc/self/cmdline` 取入口路径，自己递归装载
`use`，缺 `Option`/`Result` 时注入预置枚举，然后**先检查再发射**。所以"有 stage1"
就等于"有一个不用任何解释器的 Loment 编译器"。

种子的复现性由闸门钉住：`test_seed_matches_reference` 每次都用参考实现重新发射一遍
并逐字符比对 —— **自举产物变了而种子没跟上，门禁就红**。这是"种子不许过期"的棘轮。

## 3. 启动脚本做了什么（`loment/bootstrap.sh`）

四条判据，全部只用 `clang` + `cmp`：

1. `clang(种子)` → `stage1`；
2. `stage1` 编译 `loment/selfhost/driver.lomt` → **必须与种子逐字节相同**
   （即：种子自己就是一个定点，编译器能原样重建自己）；
3. `clang(stage2.ll)` → `stage2`，`stage2` 编译同一入口 → 与 `stage2.ll` 相同
   （三阶段定点，与 M84 的判据同一件事，只是起点换成了种子）；
4. `stage1` 与 `stage2` 对**非自身**入口（`native_res.lomt`）的产物相同
   —— 定点不能是"只会编译自己"的巧合。

跑法：

```sh
sh loment/bootstrap.sh                # 四条证明（几十秒）
sh loment/bootstrap.sh ENTRY.lomt     # 追加：用 stage1 把该入口的 IR 打到 stdout
```

**平台适配**：Linux/macOS/WSL 自带 clang 时直接用本机 clang；本机（WSL 里没有 clang、
Windows 侧有 LLVM）走 `/mnt/c/Program Files/LLVM/bin/clang.exe` 互操作 —— 这种模式下
输入/输出要用 `wslpath -w` 转成 Windows 认的路径，而 ELF 要拷进 `/tmp` 才能执行
（DrvFs 上不能直接跑）。两种模式都在脚本里显式分支，没有隐式降级。

**静态判据**：`loment_seed --script-ok` 扫启动脚本，命令位置出现
`python/python3/perl/ruby/node/cargo/rustc/gcc` 任何一个即失败，并要求 LF 换行
（WSL 的 `sh` 会把 CRLF 里的 `\r` 当命令字符，见 `.gitattributes` 的 `*.sh eol=lf`）。
这条是**防止退化**的闸门：以后有人在脚本里加一句 Python 便利检查，门禁立刻红。

## 4. 现在还剩哪些"第三方语言"

诚实的清单（不是"零依赖"）：

| 剩余 | 位置 | 影响 | 去路 |
|---|---|---|---|
| Python（**测试侧**） | `tools/*_test.py`、`loment_rule_parity`、`ci.py` | 不影响"用户构建/使用 Loment"，只影响开发期判据 | 判据本身也是可移植的：把一致性套件写成 Loment 程序（下一批），或保持 Python 作为"第三方审计工具"——两条路都合理 |
| ~~`lomfmt`（格式化）~~ | **已重写**：`loment/tools/lomfmt.lomt` | 与 Python 版**逐字节相同**（42 语料 + 4 边界 + 幂等，`tools/loment_fmt_test.py`，已进 `ci.py`） | 工具链去 Python 的第一块；它只吃词法层，所以不受自举 parser 子集限制 |
| Python（其它工具链） | `lomdoc`/`loment_lsp`/`lompkg` | 用户要文档、补全、依赖管理时还要 Python | 逐个用 Loment 重写；`lomdoc`/`lsp` 需要**完整 parser**（目前是子集），所以先补 parser 或先做 `lompkg` 这类不吃 AST 的 |
| Python（L0 生成器） | `tools/lomc.py`（13 个生成物被内核线消费） | 跨线接口面，单方面改会破坏内核线约定 | 需与内核线协同排期（docs/141 的冻结阈值） |
| clang / LLVM | 发射 IR → 可执行文件 | **地基语言**，本次目标明确保留 | 不计划去掉 |

换句话说：**从"想重建/使用 Loment"出发的路径已经不含解释器**；剩下的 Python 都在
开发期判据与尚未重写的工具链上，且每一项都有可测量的迁移判据可写。

### 4b. 第一块工具链已经重写（`lomfmt`）

`loment/tools/lomfmt.lomt` 是用户侧工具链里第一个 Loment 实现，判据 = **与 `tools/lomfmt.py`
逐字节相同**（42 个语料 + 4 个边界 + 幂等，`python tools/loment_fmt_test.py` 3/3 通过，
已进 `ci.py`）。它只吃词法层，所以不需要完整的自举 parser。

语义是**逐条镜像** Python 版，包括那些"怪癖"：

- 集合成员判断用 **val**：字符串字面量的 val 是转义解码后的内容，所以源码里的 `"("`
  会被当成真的左括号（语料里到处是 `tok_is(src,t,i,"(")`，不镜像就逐字节不一致）；
- `_render` 对字符串**解转义再重转义**（`\q` → `q`、`\"` → `\"`、`\n` 写成两字符 `\n`）；
- 多字符运算符按**相邻 token** 合并（不看源码里是否连着，`merge_ops` 的原文语义）；
- Python 版**丢注释**这一行为被照搬（两者都丢），不单方面改。

这一格同时把"写 Loment 工具"的模板钉下来了：同一份源码喂两个实现、比 stdout 字节。
`lomdoc`/`loment_lsp` 需要完整 parser（自举 parser 目前是子集），`lompkg` 不吃 AST ——
下一个该动哪一格由这条依赖决定。

## 5. 这套东西怎么进 CI

- `tools/ci.py` 的 `STATIC_CHECKS` 含 `loment_seed_test`；
- `tools/loment_release.py` 的工件清单含 `loment/bootstrap.sh`、`selfhost_driver.ll`、
  两个新工具 —— 种子与脚本本身也被 sha256 钉住；
- 种子与脚本的判据写在 `docs/158` 的"改冻结面流程"之外：它们**不是**冻结面
  （换布局、换种子格式都不改变语言语义），但**种子过期**会让自举链的红灯变成假绿，
  所以照样进门禁。

## 6. 证据（2026-09-12，本机）

```
python tools/loment_seed_test.py
  PASS  test_seed_matches_reference            # 种子 == 参考 (1630342B)
  PASS  test_bootstrap_script_is_python_free   # 无解释器调用 + LF
  PASS  test_seed_bootstrap_fixed_point        # 种子自复现 + stage2/stage3 定点
SEED BOOTSTRAP OK: 只用 clang + sh (无 Python); 种子自复现 + 三阶段定点

python tools/loment_fmt_test.py
  PASS  test_loment_fmt_matches_python         # 42 个语料逐字节相同
  PASS  test_loment_fmt_edge_cases             # 空/只有注释/含转义引号/CRLF
  PASS  test_loment_fmt_is_idempotent          # 格式化两次结果相同
```

测量：`driver.lomt` 的种子 1 630 342 B；`native_res.lomt` 两阶段产物 3 028 B 相同；
格式化器对最大的语料 `selfhost/codegen.lomt` 输出 161 813 B 逐字节一致。
