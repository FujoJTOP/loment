# 160 · Loment 0.1.3.4 Alpha 审计包（M100）

> 这份文件的读者是**第三方复核者**（以及几个月后忘掉细节的我自己）。
> 一条命令跑完全部判据：`python tools/loment_audit.py --json`
> —— 它会打印/落盘 `loment/build/audit-report.json`（含提交、tag、clang 版本、
> 14 条主张的逐条结果，以及**不主张清单**）。

## 0. 审计包的设计原则

审计工具**只调用已有门禁**，不新写判据。原因：审计工具自己长成第二套判据，是这类
项目最典型的坑 —— 两边一起漂，红绿都失去意义。`tools/loment_tools_test.py` 里有一条
一致性门禁（`test_audit_claims_match_ci`）专门盯这件事：审计工具列的每个工具都必须
出现在 `tools/ci.py` 的静态门禁表里。

## 1. 主张清单（14 条，各自有可执行判据）

| # | 主张 | 判据（`python tools/…`） | 通过标准 |
|---|---|---|---|
| C1 | 自举 checker 与参考实现**规则等价** | `loment_rule_parity.py` | 63/63 等价；假阳性/口径漂移/探针失效 **必须为 0**；棘轮预算只升不降 |
| C2 | 两个后端**逐字节等价** + 三阶段自举定点 + 语料零诊断 + 负例被拒 | `loment_p8_test.py` | 16/16 条；40/40 目标 IR 相同；stage1=stage2=stage3；63 条负例全部非零退出且无信号 |
| C3 | 参考实现自身形状 | `lomentc_test.py` | 91/91 |
| C4 | 工具侧（诊断分类完整、内建表一致、增量缓存、**审计清单一致**） | `loment_tools_test.py` | 15/15 |
| C5 | **无 Python 自举**：种子自复现、启动脚本无解释器、定点 | `loment_seed_test.py` | 3/3（含 `sh loment/bootstrap.sh` 只用 clang 跑通） |
| C6 | Loment 版格式化器与 Python 版**逐字节相同** | `loment_fmt_test.py` | 3/3（42 语料 + 4 边界 + 幂等） |
| C7 | 示例集与交叉编译目标 | `loment_p9_test.py` | 2/2（25 示例编译；aarch64 目标发射并可反汇编） |
| C8 | 编辑器宿主与 LSP 往返（无头） | `vscode_ext_test.py` | 6/6 |
| C9 | 发布工件 **sha256 可复现** | `loment_release.py --check` | 工件数 N/N 一致（件数由它打印） |
| C10 | 账本不是手改的 | `loment_status.py --check` | 状态矩阵与 `docs/145` 一致 |
| C11 | **LSP 去 Python**（Loment 版语言服务：补全/跳转/诊断/`--check`） | `loment_lsp_test.py` | 3/3（真二进制 7 帧往返 + 码/行号 + `--check`） |
| C12 | 工具链等价（格式化器/文档生成器与 Python 版逐字节相同） | `loment_doc_test.py` | 2/2（43 语料 + 边界） |
| C13 | JSON 库与 Python `json` 逐字节一致 | `loment_json_test.py` | 2/2 |
| C14 | Windows 文件类型注册 + 启动脚本无解释器 | `loment_filetype_test.py` | 4/4 |

一键跑（约 4 分钟，含 clang 编译与 WSL 执行）：

```bash
python tools/loment_audit.py --json     # 14/14 通过 + loment/build/audit-report.json
python tools/loment_audit.py --list     # 只列主张与命令
```

## 2. 明确**不**主张（复核者请按"未验证"处理）

1. **冻结面内的判据都是自证的**：两个实现由同一作者编写，"两边一致"不等于"语义正确"。
   本审计包**不构成**外部确认 —— 外部审计要补的正是这一层。
2. 三处**刻意保守偏离**：类型算不出来时（下标/`&`、`match` 主体、`?`），自举 checker
   可能**少报**诊断（方向保守，不假阳性）。见 `docs/158 §4`。
3. **同名 `let` 双 alloca**：同一函数里重复声明同名变量时两个后端语义不同
   （参考复用槽位，自举发两条）。这是唯一一处**已知的语义不一致**。
4. **自举驱动整条链没有 parser**：解析期错误（如 `const C: bool = true;`）只有参考实现
   能报出来。parser 本身已与参考逐字符一致（42/42 语料），但没有接进 driver 的链路。
5. **aarch64 只验证到发射**：没有 qemu-user、没有真机执行（`docs/158 §4`）。
6. 自举性能 12.8s（参考 1.2s）；**无 DWARF 变量信息**（只有语句级行表）。
7. **工具链仍有 Python 成分**：`lomdoc`/`loment_lsp`/`lompkg` 与 L0 生成器 `lomc.py`
   尚未 Loment 化（进度与依赖排序见 `docs/159 §4b`）。
8. Mimosa 扫描器多次未能给出完整结论（`scanner_enobufs`）—— 因此**不宣称项目安全**。

## 3. 第三方复核步骤（30–60 分钟）

```bash
git clone -b Fujoos-FujoLang-DEV <repo> && cd FujoOS
git checkout <审计报告里的 commit>   # 报告 provenance.commit —— 判据要对的**就是它**
python tools/loment_eol.py --fix    # 第 0 步: 把检出行尾拉回 LF (见 docs/161)
python tools/loment_audit.py --json # 期望 14/14
```

> tag `v0.1.3.4-alpha`（annotated）是 **M96 冻结面快照**，早于当前审计状态：
> 在那个提交上 `tools/loment_eol.py` 还不存在、主张也只有 10 条。要复核**冻结面**就 checkout
> 这个 tag（那里看 docs/158 与 `--emit-llvm` 的逐字节判据）；要复核**当前主张**就用报告里的
> commit。两者别混。

> 第 0 步不是可选的礼貌：Windows 上（`core.autocrlf=true`）检出的换行取决于**怎么检出的**
> —— 先 checkout 默认分支再切分支，会留下十几个 CRLF 文件，而按原始字节读源码的判据会把
> 它们报成"逻辑红"。一条命令就能归零，`--fix` 不会吞内容改动。

1. **工件哈希**：把审计报告里的 `provenance.commit` 与本地 `git rev-parse HEAD` 对齐；
   `python tools/loment_release.py --check` 应报 N/N 一致。
2. **抽样手工核**（不依赖我的脚本）：
   - 挑一个负例 `loment/selfhost/neg/*.lomt`，用参考实现（`tools/lomentc.py`）与自举
     驱动各判一次，看**都拒**且错误码集合一致；
   - 挑一个示例，比较 `tools/lomentc.py --emit-llvm` 与自举驱动的输出是否**逐字节相同**
     （`tools/loment_ir_diff.py` 有现成的差分工具）；
   - 看 `docs/158` 的冻结面，挑一条规则，自己写一个最小反例，看两边是否同判。
3. **对抗性尝试**（这些是"门禁有没有牙"的检查，不是破坏）：
   - 把 `tools/loment_rule_parity.py` 的 `BUDGET` 调低 → 门禁**必须**红（棘轮）；
   - 改种子 `loment/build/selfhost_driver.ll` 一个字节 → `loment_seed --check` **必须**红；
   - 删/改一个发布工件 → `loment_release --check` **必须**红；
   - 在 `loment/bootstrap.sh` 里加一句 Python 便利检查 → `loment_seed --script-ok` **必须**红；
   - 把任一 pinned 文件改成 CRLF（内容不动）→ `loment_eol` **必须**红，而 `git status`
     **仍然报干净**（docs/161：这就是"两个工作树为什么不一样"的现场证据）。
4. **报告**：审计结论请连同 `audit-report.json`（含日期、环境、10 条结果）一起存证；
   有红项时报告里会直接列出主张编号与工具的输出尾行。

## 4. 复核环境

- Windows + WSL（有 clang）或 Linux/macOS 原生；Python ≥ 3.10；`clang` 需要在 PATH 上
  或装在 `C:\Program Files\LLVM\bin`（工具里有回退路径）。
- 需要执行 Linux ELF 的判据（自举定点、驱动跑语料、格式化器）在 Windows 上走 WSL；
  没有 WSL 时这些条目会打印 `SKIP` —— **SKIP 不算通过**，报告里会显示该条不红但也没有
  证据，复核者应把它记为"未验证"。

## 5. 关联材料

- `docs/158-loment-freeze.md` —— 冻结面（改什么要付什么代价）+ 7 条开放项；
- `docs/161-loment-checkout-eol.md` —— 检出行尾契约：CRLF 为什么能伪装成逻辑红；
- `docs/150-loment-selfhost.md` —— 自举链（lexer/parser/checker/codegen/定点）与 M80 收口；
- `docs/159-loment-seed-bootstrap.md` —— 无 Python 自举与工具链去 Python 的进度排序；
- `docs/145` / `docs/154` —— 100 里程碑账本（当前：完成 86 · 部分 9 · 未达 1 · 未开始 4）。
