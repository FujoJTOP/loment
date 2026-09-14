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
| ~~`lomdoc`（文档）~~ | **已重写**：`loment/tools/lomdoc.lomt` | 与 Python 版**逐字节相同**（43 语料 + 1 边界，`tools/loment_doc_test.py`，已进 `ci.py`）；顺手修了参考实现在注入预置枚举上的行号 bug | 去 Python 第二块；同样只吃声明层，不需要 parser |
| ~~`loment_lsp`（语言服务）~~ | **已重写**：`loment/tools/lsp.lomt` | 判据 `tools/loment_lsp_test.py`（真二进制 7 帧往返 + 码/行号 + `--check`，已进 `ci.py`）；走 checker 的符号表，没等自举 parser 建树 —— `docs/154` 的 M56 仍标"部分"= 编辑器内人工点验 | 去 Python 第三块 |
| ~~`lompkg`（包管理）~~ | **已重写**：`loment/tools/lompkg.lomt` | stdout 与 Python 版**逐字节相同** + 退出码相同（拓扑序 + sha256 + 环检测 + 锁往返，`tools/loment_pkg_test.py`，已进 `ci.py`） | 去 Python 第四块。`getdents64`(217) / `newfstatat`(262) / SHA-256 都用 `syscall4/6` 内建自己发，**没动运行时** |
| ~~`lomc`（L0 生成器）~~ | **已重写**：`loment/tools/lomc.lomt` | 四个后端（rust/c/python/json）与 Python 版**逐字节相同**，12 个生成物一个字节都没动（判据 `tools/loment_lomc_test.py`，已进 `ci.py`） | 去 Python 第五块。**跨线契约面的做法**：不改 L0、不改生成物 —— 用"逐字节相同"把契约钉住，下游零改动 |
| clang / LLVM | 发射 IR → 可执行文件 | **地基语言**，本文写作时（0.1.4）明确保留 | ~~不计划去掉~~ **已改为计划去掉**（0.1.4 Alpha2）：参考实现 `tools/lomelf.py` 已能「IR → x86-64 ELF」且与 clang 产物行为逐值一致（`loment_elf_test` 4/4，主张 C19，见 `docs/167`）。**已落地**：自举侧镜像 `loment/tools/lomelf.lomt`（对四个语料与参考逐字节相同，主张 C20）—— 编译用户程序全程无 clang。**仍待做**：PE64/去 WSL、构建路径去 Python、genesis（连种子→stage1 那一步也去掉 clang） |

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

**下一格该动哪一块，由依赖决定**（2026-09-12 摸底）：

| 工具 | 现状 | 缺什么 |
|---|---|---|
| `lomfmt` | ✅ 已重写 | —（只吃词法层） |
| `lomdoc` | ✅ 已重写 | —（只吃声明层） |
| `loment_lsp`（补全/跳转） | ✅ 已重写 | —（用 checker 的符号表，没等自举 parser 建树） |
| `lompkg`（包管理） | ✅ 已重写 | —（目录遍历与 SHA-256 都在源内自备，运行时没补新调用） |
| `tools/lomc.py`（L0 生成器） | ✅ 已重写 | —（`loment/tools/lomc.lomt`；四个后端与 Python 版**逐字节相同**，下游一行不用动） |

也就是说：**去 Python 到此收口** —— 用户侧工具链（fmt / doc / lsp / pkg）与 **L0 生成器**（lomc）
全部有了 Loment 实现，表里没有"未动"的项了。再往前一步的"正经形态"是给自举 parser 加**建树**
输出（让 LSP 从符号表升级到真 AST），但它从来不是"去 Python"的前置。

## 5. 这套东西怎么进 CI

- `tools/ci.py` 的 `STATIC_CHECKS` 含 `loment_seed_test`；
- `tools/loment_release.py` 的工件清单含 `loment/bootstrap.sh`、`selfhost_driver.ll`、
  两个新工具 —— 种子与脚本本身也被 sha256 钉住；
- 种子与脚本的判据写在 `docs/158` 的"改冻结面流程"之外：它们**不是**冻结面
  （换布局、换种子格式都不改变语言语义），但**种子过期**会让自举链的红灯变成假绿，
  所以照样进门禁。

## 6. 证据（2026-09-12 起，2026-09-13 追加 lompkg / lomc）

```
python tools/loment_seed_test.py
  PASS  test_seed_matches_reference            # 种子 == 参考 (1630422B; 见下 §6 的 M81 修订: 类型表按函数清零)
  PASS  test_bootstrap_script_is_python_free   # 无解释器调用 + LF
  PASS  test_seed_bootstrap_fixed_point        # 种子自复现 + stage2/stage3 定点
SEED BOOTSTRAP OK: 只用 clang + sh (无 Python); 种子自复现 + 三阶段定点

python tools/loment_fmt_test.py
  PASS  test_loment_fmt_matches_python         # 42 个语料逐字节相同
  PASS  test_loment_fmt_edge_cases             # 空/只有注释/含转义引号/CRLF
  PASS  test_loment_fmt_is_idempotent          # 格式化两次结果相同

python tools/loment_pkg_test.py
  PASS  test_loment_lompkg_matches_python              # 链/嵌套/空包: resolve stdout 逐字节相同
  PASS  test_loment_lompkg_edge_cases                  # 环/缺依赖/缺 name/用法错误: 退出码与 stdout 一致
  PASS  test_loment_lompkg_verify_and_lock_roundtrip   # 一致/DIFF/MISS + 双向锁往返

python tools/loment_lomc_test.py
  PASS  test_loment_lomc_print_matches_python          # 12 份发射 (3 个 .lom × 4 个后端) 逐字节相同
  PASS  test_loment_lomc_emit_writes_same_bytes        # 落盘字节 + [OK] 输出行 + 退出码一致
  PASS  test_loment_lomc_check_and_errors              # --check 对账/漂移 + 语义错误 + 用法错误
  PASS  test_lomc_selfhost_compiles                     # 种子 -> stage1 -> 编译 lomc.lomt (无 Python)
```

测量：`driver.lomt` 的种子 **1 630 422 B**（比 2026-09-12 的 1 630 342 B 多 80 B —— 同一提交里修了自举 checker 的"复合类型表按函数清零"，见 docs/150 的 M81 后置修订）；`native_res.lomt` 两阶段产物 3 028 B 相同；
格式化器对最大的语料 `selfhost/codegen.lomt` 输出 161 813 B 逐字节一致。
`lomc.lomt` 的四个后端对 3 个 `.lom` 产出 **12 份逐字节相同**的生成物（rust/c/python/json，判据 `loment_lomc_test.py` 3/3）；
`lomc.lomt` 走**种子自举链**编译成功（stage1 → 473 408 B IR，判据 `test_lomc_selfhost_compiles`）；
`lompkg.lomt` 的参考 IR **280 415 B**，与**种子自举链** stage1 发射的 IR 逐字节相同
（`sh loment/bootstrap.sh loment/tools/lompkg.lomt`，无 Python），种子构建的二进制在
6 个场景上与 Python 版 stdout / 退出码一致。
