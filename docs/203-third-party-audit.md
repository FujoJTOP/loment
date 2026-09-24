# 203 · 第三方复核（独立 agent，2026-09-22）

> 这是一次**独立第三方复核**：复核者是一个**没有本仓创作会话上下文的独立 AI agent** —— 手上
> 只有「仓库 + `docs/160` 审计包」两样东西，**自己**跑判据、自己判断接受还是驳回。
> 结论、证据、以及**与审计包自身判定不一致**的地方都在下面；`docs/160` §1 那句数字与
> 它点名的工具对不上（见 §5），那本身就是本次最贵的一条产出。

> 复核对象：`origin/main` @ `f09c9a5`（2026-09-22 检出的干净工作树，`git status` 干净）。
> 判据入口：`python tools/loment_audit.py --json`。
> 复核者：一个独立运行的 AI agent（模型 `claude-sonnet-4-5[1m]`），2026-09-22，
> Windows 11 + Git Bash，从 `origin/main` 新建 worktree 执行。**不是**人类、**不是**另一个
> 组织、**不是**持证审计方，也**不是**作者或其 agent。本文的判断是我自己下的。

## 0. 这次"第三方"是哪一种（读者据此定位）

- **是什么**：一个独立运行、无创作会话上下文的 AI agent，按 `docs/160` §3 的步骤
  独立跑判据并逐条判接受/驳回/未验证。
- **不是什么**：不是人类审计、不是外部组织背书、不构成任何形式的认证。
- **复核的是哪一层**：**审计包里的主张能不能被它点名的判据支持** —— **不是**语言语义
  正确性本身。审计包 §2.1 已声明"冻结面内的判据都是自证的"；这一点我复核后同意，且它正是
  这次复核的天花板：两个由同一作者写的实现"两边一致"，不等于"语义正确"。

## 1. 环境与判据（读者要能复跑）

| 项 | 本机实测 |
|---|---|
| 提交 | `f09c9a5`（`origin/main`，干净工作树） |
| Python | 3.11.15 |
| clang | 22.1.8（`C:\Program Files\LLVM\bin\clang.exe`，**不在 PATH 上**，靠工具的 fallback 找到） |
| WSL | Ubuntu（Running） |
| 其它 | `openssl` 3.2.4 · `node` 在 · `powershell` 在 · `signtool`（Windows SDK 10.0.26100）在 |

**一条命令**（约 20+ 分钟，本机比审计包 §1 说的"约 4 分钟"慢得多，因为 C19/C20/C24/C21 要
现编 clang 与自举链）：

```bash
python tools/loment_audit.py --json
```

审计包对**第三方复核机器**的要求（§4）在本机基本满足：没有 SKIP 掉的判据（`loment_audit`
报告里逐条看得到）。**唯一的环境红是 C14**，原因见 §3.1（**不是**审计包 §1 说的那种
"环境红"）。

## 2. 逐条判定（汇总）

主张条数由**自省**点出，不看文档写的数：

```
python -c "import sys;sys.path.insert(0,'tools');import loment_audit as a;print(len(a.CLAIMS));print([c[0] for c in a.CLAIMS])"
# -> 24
# -> ['C1','C2',...,'C24']
```

**汇总：ACCEPT 22 · REJECT 1（C14）· UNVERIFIED 1（C16）。**
逐条的命令、输出与保留意见见 **§6**；展开的论证在 §3–§5。

## 3. 对抗性尝试（"门禁有没有牙"）

我按 `docs/160` §3.3 的清单逐条试，另外自己加了几条。**命令与输出都是实测**。

### 3.1 试图让判据在没有那条性质时也通过

- **C14（文件类型注册）— 判据红了，但红的样子和文档说的不同。**
  文档 §1 说"本机 C14 是环境红, 见 §2"，而 §2（不主张清单）**一个字都没提 C14**。
  实测 `python tools/loment_filetype_test.py` → **1/4**：`test_plan_covers_both_extensions`
  与 `test_status_is_read_only` 直接**抛未捕获的异常**，不是 SKIP：
  ```
  OSError: [WinError 649] ... : 'C:\Users\hooya\AppData\Local\Programs\Microsoft VS Code\Code.exe'
  ```
  根因在 `tools/loment_filetype.py:68-70/181`：`Path(...).is_file()` 在本机那条 VS Code
  路径上**抛 OSError（WinError 649，装入点）而不是返回 False**，工具没接住。
  触发条件是本机文件系统状态（不是缺姊妹仓），所以**判据本身没有把"环境红"做成 SKIP** ——
  它是**崩**。这一点与同仓别的判据（缺 clang/WSL 时 print SKIP）的做法不一致。

- **C13（JSON 库与 Python `json` 逐字节一致）— 判据里的主用例有一半是空转的。**
  `tools/loment_json_test.py:226`：
  ```python
  want.append(f"root_kind={json.loads(p) and 1}")
  ```
  `json.loads(p) and 1` 对任何非空 JSON 都恒等于 `1` —— 所以"解析侧"这一半
  **对每个 payload 都只断言"根不是空"**，不校验任何字段值（docstring 却写着"关键字段取值"）。
  生成侧（`json.dumps` 逐字节比）是真的。**该用例的通过不构成"JSON 库与 Python 一致"的
  证据**；证据来自同文件 `test_json_decoding_and_lookup` 与 `test_json_check_matches_loment_twin`
  （那两条是真的，见 §4）。

- **C2（两个后端逐字节等价）— 有一条覆盖用例只测"子集不回归"。**
  `tools/loment_p8_test.py:1282-1285`（`test_m82_coverage_report`）唯一的硬断言是
  `known` 列表里的文件必须继续逐字节相同；语料里其它文件的差异数**只打印不断言**。
  docstring 明说这是"进度分母"，所以**不是撒谎** —— 但 C2 的"40/40 目标 IR 相同"不能由这条
  支持（由 `test_m85_selfhosted_driver_compiles_corpus` 支持，那条是真断言）。且"40/40"这个
  数**已过期**：当前语料是 32+19+29 = **80** 个单元（见 §5）。

- **"跳过算通过"是**系统性**的。** 所有 `*_test.py` 都这样：`main()` 把"没抛异常"记成通过，
  而决定跳过的那条只 `print("SKIP...")` 然后 `return` —— 于是**跳过的测试照样进 `N/N` 的分子**。
  实测两例（本机真的发生了）：
  ```
  python tools/loment_p9_test.py     # test_m92_aarch64_cross_compile 打了 SKIP（无 qemu-aarch64）
                                     # 汇总仍打印 "loment_p9_test: 2/2 通过"
  python tools/vscode_ext_test.py    # test_vscode_vsix_structure 打了 SKIP（尚未打包）
                                     # 汇总仍打印 "vscode_ext_test: 9/9 通过"
  ```
  审计包 §4 自己写了"**SKIP 不算通过**"，但这只对**读报告的人**成立；审计工具抓的"尾行"
  就是那句 `N/N 通过`，**报告里看不出跳了几条**。

### 3.2 试图让判据在没有那条性质时也通过（棘轮 / 篡改 / 解析器）

| 尝试 | 期望 | 实测 |
|---|---|---|
| 改发布清单里一条工件的 sha（把 `loment_release.OUT` 指向一份被改过的副本）再 `--check` | **必须红** | `rc=1`，`loment_release: 516/517 一致`；还原后 `rc=0`，`517/517` ✅ 有牙 |
| 改 `docs/154`（状态矩阵）再 `loment_status --check` | **必须红** | `rc=1`，`[DIFF] docs/154-loment-status.md 与 docs/145 不一致`；还原后 `rc=0` ✅ 有牙 |
| 在 `loment/bootstrap.sh` 里放一行 `python3 ...`（用探针文件 + `LAUNCH_SCRIPTS`，不碰真脚本）再 `script_ok()` | **必须红** | `[FAIL] ...:3 调用了 python3`，`rc=1`；换成只有 `clang` → `rc=0` ✅ 有牙 |
| 把一个 pinned 文件（`docs/161`）改成 CRLF（内容不动）再 `loment_eol` | **必须红** | `rc=1`，点名文件 ✅ |
| 同上，看 `git status` | 审计包 §3.3 说"**必须仍然报干净**" | **实测 `git status --porcelain` 报 ` M docs/161...`，且 `git update-index --refresh` 回 `needs update`** —— **期待没成立**（见 §5） |
| 把 `loment_rule_parity.BUDGET` **调低** | 审计包 §3.3 说"门禁**必须**红（棘轮）" | **反了**：门禁是 `eq >= BUDGET`（`loment_rule_parity.py:273`），**调低 BUDGET 只会更容易过，不会红**；而且**全仓没有任何判据钉住 BUDGET 不许下降**（`grep -rn BUDGET tools/` 只有该文件自己）。要它红得把 BUDGET **调高**过头（那才证明它有牙）。文档这一句方向写反了（见 §5） |

### 3.3 结论：门禁有牙，但"绿"的分辨率比文档暗示的低

- 发布清单、状态矩阵、启动脚本判据、行尾判据 **都对"改坏了"有反应**（§3.2 前四行）。
- 但"(N/N) 通过"这句话**同时**容纳了"真跑了"和"跳过了"（§3.1）；**审计报告里没有一处
  汇总跳过数**。对 C15/C16 这种"干净检出上大部分用例按设计跳过"的主张，这尤其要紧（见 §4）。

## 4. 本机**无法验证**的（以及为什么能断定是"环境"而不是"驳回"）

判定标准（与任务给的口径一致）：判据要的**对照物**若在本机不存在、且**没有它就跑不了**，
记"未验证"；判据**跑了并给出否定结果**才是驳回。

- **无：** 本机 clang / WSL / node / openssl / powershell / signtool 都在，`docs/160` §3
  要求的 WSL 执行路径可用。所以 24 条主张里**没有**因"缺工具"而整条 SKIP 的。
  （`loment_p9_test` 的 aarch64 **执行**打了 SKIP —— 但主张 C7 只声明"发射并可反汇编"，
  执行不在主张里，所以不影响 C7 的判定。）
- **私有姊妹仓 `FujoJTOP/LinuxFUAI` 不在树里** —— 它影响的是 `lom_audit` / `lomc_test` /
  `potato_cross` / `fuai_contract_check`（`.github/gate-baseline.txt` 里那 4 条"环境"红）。
  **这四条都不在审计包的 24 条主张里**，所以它们不影响本文任何一条判定。**但读者要知道**：
  审计包 24 条 ≠ 整道门禁 —— 门禁另有已知红集（`docs/202` §1 说 10 条），审计包覆盖的
  是**能绿的那一子集**。
- **`loment/dist/` 与 `loment/build/sign/` 在干净检出上不存在** —— 这决定了 C15/C16 的
  一部分用例"按设计跳过"（见 §6 表里 C15/C16 的证据行），而不是我漏测。

## 5. 与审计包自身判定**不一致**的地方（本次最贵的产出）

### 5.1 `docs/160` §1 的主张清单，与它点名的工具**不是一回事**（缺 4 条、数错 4 次）

这是本次最重的发现，且**可一条命令证伪**：

```
python -c "import sys;sys.path.insert(0,'tools');import loment_audit as a;print(len(a.CLAIMS))"
# -> 24
```

而 `docs/160`：
- §1 标题写"主张清单（**23 条**…）"，**正文表格只有 20 行**（C1–C20）；
- §1 末尾与 §3 的期望都写"**23/23**"。

`docs/202`（2026-09-22，同一天）§1 第 2 项与 `docs/145` M100 行写的都是"**18/18**"。
**四个数**：18（文档）/20（表）/23（标题）/24（工具）。

`git log` 把来历挖出来了（**不是笔误，是长期不同步**）：

```
for c in $(git log --format=%h -- docs/160-loment-audit-kit.md); do ... done
# 608728b header=21 rows=20   <- C21(genesis) 加进工具, 标题+1, 表格没加行
# c27c2c8 header=22 rows=20   <- C22(lomstatus)
# f440c78 header=23 rows=20   <- C23(lomrel)
# 156fc64 header=23 rows=20   <- 文档最后一次改动(2026-09-15), 仍 20 行
# db90a16 (C24, PE) 连标题都没+1 -> 卡在 23
```

也就是说：**C21（genesis）/ C22（lomstatus）/ C23（lomrel）/ C24（PE）这四条 —— 恰好是
"去 clang / 去 WSL / 构建去 Python"这半条叙事 —— 从 2026-09-14 起就没进过 `docs/160` 的
主张表**。第三方照 `docs/160` 复核，会**整段漏掉它们**。

`docs/160` §0 声称 `loment_tools_test::test_audit_claims_match_ci` "专门盯这件事"。实测
（`tools/loment_tools_test.py:54-71`）它盯的是**四条别的**：①审计引用的**工具名**都在
`ci.STATIC_CHECKS` 里；②主张编号不重复；③**`len(CLAIMS) >= 10`（一个下限，不是等号）**；
④`argv` 是 list。**它不把条数对任何常量、更不对 `docs/160`** —— 所以 20/23/24 的漂移它
**天然看不见**。§0 那句自我描述因此**说宽了**。

### 5.2 每条主张的"通过标准"数字，绝大多数**过期**

审计包给的"通过标准"与工具**实际会打印的分母**（`len(TESTS)`）对照（本机实测 `grep -cE '^def test_'`）：

| 主张 | 文档写的 | 实测分母 | 是否一致 |
|---|---|---|---|
| C1 | 63/63 | 69 条案例 / `BUDGET=69` | ✗ |
| C2 | 16/16 | 20 | ✗ |
| C3 | 91/91 | 121 | ✗ |
| C4 | 18/18 | 30 | ✗ |
| C5 | 3/3 | 4 | ✗ |
| C6 | 3/3 | 4 | ✗ |
| C7 | 2/2 | 2 | ✓ |
| C8 | 6/6 | 9 | ✗ |
| C11 | 3/3 | 6 | ✗ |
| C12 | 2/2 | 2 | ✓ |
| C13 | 2/2 | 4 | ✗ |
| C14 | 4/4 | 4 | ✓（但本机 1/4，见 §3.1） |
| C15 | 34 条 | `len(RESULTS)`，平台相关（Windows 上 ~70 个 `check()` 点） | ✗ |
| C17 | 3/3 | 3 | ✓ |
| C18 | 3/3 | 4 | ✗ |
| C19 | 5/5 | 9 | ✗ |
| C20 | "4 个语料" | 是 `loment_elf_test` 的第 6 个用例 | 数字对、归属含糊 |

**20 条里只有 4 条（C7/C12/C14/C17）的分母对得上。** 这不影响主张的**实质**
（判据真跑真绿才是证据），但它让审计包作为"可对账的清单"**对不上账**：一个复核者按文档
"期望 16/16"去看，会看到 20/20，然后不知道信哪个。

### 5.3 `docs/160` §3.3 的两条对抗步骤，**方向/结果写反了**

- "把 `BUDGET` **调低** → 门禁**必须**红"：门禁是 `eq >= BUDGET`，**调低只会更容易过**；
  且没有判据拦"调低 BUDGET"这件事。**要它红得调高**。
- "改成 CRLF 后 `git status` **仍然报干净**"：本机（Git 2.50.0.windows.2，
  `core.autocrlf=true`，`.gitattributes` `*.md text eol=lf`）实测 `git status --porcelain`
  **报 ` M`**，`git update-index --refresh` 回 `needs update`。**"报干净"这条没成立**
  （`git diff` 的内容确实是空的，所以"内容对 git 不可见"那一半成立）。

### 5.4 §2"不主张清单"**比仓库自己别处的开放项清单短**

`docs/160` §2（与审计工具里的 `non_claims` 字段）列了 10 条。但 `docs/158` §4 列了 **12** 条，
`docs/167` §5.3 又记了一条，合计至少**5 条已知开放项没进 §2**：

- `docs/158` §4 #8：括号里的 `ptr` 表达式再 `as` 整数，**自举 codegen 发 `zext i32`（截成
  32 位）** → 运行期用错地址；
- `docs/158` §4 #9：FFI 外部目标文件 **4 MiB 上限**（自举链接器会拒、参考实现能链）；
- `docs/158` §4 #10：`R_X86_64_PC64` 的符号扩展两实现不一致；
- `docs/158` §4 #11：**混宽整数比较**（`u32 == u8`）参考实现发**非法 IR**、自举侧截窄再比 ——
  语料从没有这种写法，所以"两边一致地错"；
- `docs/167` §5.3：**自举镜像 `lomelf.lomt` 不支持按值传聚合**，发行包里 `loment build/run`
  编不出任何按值传 struct/enum 的程序，enum-by-value 直接 **SIGSEGV** —— 而 C24 用
  `lomstatus`/`lomrel` 当语料，两个程序**恰好不按值传聚合**，所以 9/9 全绿照不到它。

§2 是审计包**给第三方的"什么不必当主张"**那一页。它比别处短，会让第三方**以为**这些洞不在
复核范围里 —— 而其中至少 #11 与 `docs/167` §5.3 那条是"门禁全绿也照不到"的**真口子**。

## 6. 逐条判定表

**判据一律由本人在本机跑**（2026-09-22，`f09c9a5` 的干净工作树）。"证据"列给的是**命令 →
打印出来的那一行**。带 ⚠ 的是**保留意见**（判据绿、但绿的原因要打折），不是否定。
`docs/160` 表里**没有** C21–C24 —— 它们是这篇表必须补上的四条（§5.1）。

| # | 它主张什么 | 我跑的判据 | 判定 | 证据（命令 → 输出） |
|---|---|---|---|---|
| C1 | 自举 checker 与参考实现**规则等价**（棘轮；假阳性/漂移必须 0） | `python tools/loment_rule_parity.py` | **ACCEPT** | `规则覆盖率: 69/69 等价 (预算 69)  (缺口 0 / 假阳性 0 / 口径漂移 0 / 探针失效 0)` → `[OK] 达到预算 69/69 且无假阳性/漂移`，rc=0。⚠ 文档写 63/63（§5.2）；"等价"的口径是**69 条最小负例上的错误码集相等**，不是语义等价 |
| C2 | 两后端逐字节等价 + 三阶段定点 + 语料零诊断 + 负例被拒 | `python tools/loment_p8_test.py` | **ACCEPT** | **单独跑（机器空闲）**：`loment_p8_test: 20/20 通过`，含 `自举驱动按路径编译语料: 90/90 逐字节一致`、`定点: stage1 == stage2 == stage3`、`checker 放行单元: 79 无诊断`、`驱动闸门: 负例 14/14 被拒`。⚠ **三路并行跑时同一条是 `19/20`**：`test_m86_selfhost_perf_budget` 报 `自举自编译 67.7s 超过护栏 60s`，单独跑是 `45.64s` —— 这条是**墙钟预算，受负载影响**（§3.1）。文档写 16/16 与"40/40"（语料现为 90） |
| C3 | 参考实现自身的一批判据 | `python tools/lomentc_test.py` | **ACCEPT** | `lomentc_test: 121/121 通过`，rc=0。⚠ 其中有若干"确定性/子串"类弱断言与"自证再自证"的 Potato 校验（§3.1 同类）。文档写 91/91 |
| C4 | 工具侧（诊断分类/内建表/增量缓存/**审计清单一致**/DWARF） | `python tools/loment_tools_test.py` | **ACCEPT** | `loment_tools_test: 30/30 通过`，rc=0。⚠ 其中 `test_audit_claims_match_ci` 只管"工具名 ⊆ `ci.STATIC_CHECKS` + 编号唯一 + **≥10 条**"，**不管条数对不对**（§5.1）。文档写 18/18 |
| C5 | 无 Python 自举（种子自复现 + 启动脚本无解释器 + 定点） | `python tools/loment_seed_test.py` | **ACCEPT** | `loment_seed_test: 4/4 通过`，含 `SEED BOOTSTRAP OK: 无 Python, 无解释器, 无 clang (genesis 起头)`。另加对抗（§3.2）：往启动脚本塞一行 `python3` → `script_ok()` rc=1。文档写 3/3 |
| C6 | Loment 版格式化器与 Python 版**逐字节相同** | `python tools/loment_fmt_test.py` | **ACCEPT** | `loment_fmt_test: 4/4 通过`，`54 个语料逐字节相同`。文档写 3/3 与"42 语料" |
| C7 | 示例集编译 + aarch64 目标发射/反汇编 | `python tools/loment_p9_test.py` | **ACCEPT** | `loment_p9_test: 2/2 通过`，`32 个示例全部通过`；aarch64 的**执行**打 SKIP（无 qemu-aarch64）——主张只声明"发射并可反汇编"，执行不在主张里，故不影响 |
| C8 | VS Code 宿主与 LSP 往返（无头） | `python tools/vscode_ext_test.py` | **ACCEPT** | `vscode_ext_test: 9/9 通过`；其中 1 条 SKIP（未打包 .vsix）**被算进分子**（§3.1）。文档写 6/6 |
| C9 | 发布工件 **sha256 可复现** | `python tools/loment_release.py --check` | **ACCEPT** | `loment_release: 517/517 一致`，rc=0；把清单副本改一处 → `516/517`、rc=1（§3.2）。⚠ **审计者自己的报告文件若落在 `docs/*.md` 会让这条红**：`docs/203-…` 进 `build()` 但不进已提交清单 → `[DIFF] docs/203-third-party-audit.md`、`517/518`、rc=1。本表是把报告文件移开 `docs/` 后测的 |
| C10 | 状态矩阵与 `docs/145` 一致（账本不是手改的） | `python tools/loment_status.py --check` | **ACCEPT** | `[OK] 状态矩阵与 docs/145 一致`，rc=0；改 `docs/154` → `[DIFF]`、rc=1（§3.2） |
| C11 | LSP 去 Python（补全/跳转/诊断/`--check`） | `python tools/loment_lsp_test.py` | **ACCEPT** | `loment_lsp_test: 6/6 通过`。⚠ 6 条里 **2 条测的是 Python 版 LSP**（`test_python_lsp_*`）；`test_lsp_diagnostics_match_selfhosted_checker` 的期望是**写死**的、没有自举对照面。真跑 Loment 版的是另外 4 条（含 7 帧往返与 `--check`）。文档写 3/3 |
| C12 | 工具链等价（**格式化器/文档生成器**与 Python 版逐字节相同） | `python tools/loment_doc_test.py` | **ACCEPT** | `loment_doc_test: 2/2 通过`，`80 个语料逐字节相同`。⚠ 该文件**只测文档生成器**（"格式化器"那半在 C6 的 `loment_fmt_test`）——主张里并列的两个东西不在这同一条判据里 |
| C13 | JSON 库与 Python `json` **逐字节一致** | `python tools/loment_json_test.py` | **ACCEPT（有保留）** | `loment_json_test: 4/4 通过`，`6 个 payload + 生成侧: 与 CPython json 逐字节相同`。⚠ **同名的头一条 `test_json_library_matches_python` 的"解析侧"是空转的**（`json.loads(p) and 1` 恒为 1，见 §3.1）；这条主张的证据实际来自 `test_json_decoding_and_lookup` 与 `test_json_check_matches_loment_twin`。文档写 2/2 |
| C14 | Windows 文件类型注册 + 启动脚本无解释器 | `python tools/loment_filetype_test.py` | **REJECT** | `loment_filetype_test: 1/4 通过`，3 条**抛未捕获的** `OSError: [WinError 649] … 'C:\\Users\\hooya\\…\\Microsoft VS Code\\Code.exe'`（`tools/loment_filetype.py:68-70/181` 对 VS Code 路径 `is_file()` 抛错、没接住）。触发是本机文件系统状态（**不是**缺对照物），但后果是**崩、不是 SKIP** —— 与同仓其它判据"缺东西就 print SKIP"的做法不一致。文档写 4/4，且 §1 说"环境红，见 §2"而 §2 一字未提 C14（§3.1） |
| C15 | 发行包（命令安装 + **自解压安装包**），装出来的产物与参考逐字节相同 | `python tools/loment_dist_test.py` | **ACCEPT** | `loment_dist_test: 79/79 通过`，rc=0；含"装完后 `loment run` 在本机编出 PE 并跑出 `PASS loment-user`（无 WSL、无 clang）"与跨实现字节相等。⚠ **"自解压安装包"那半在干净检出上跳过**：判据自己用 `--emit --no-exe --out <build>/it-out`（不建 `loment/dist`），而 setup.exe 的检查去 `loment/dist` 找、找不到就 SKIP。文档写"34 条判据"（实测 79 个 check 点） |
| C16 | 发行包签名：**Authenticode** + **SHA256SUMS 分离签名** | `python tools/loment_sign_test.py` | **UNVERIFIED** | `loment_sign_test: 6/6 通过` **但两条 SKIP 恰好就是主张的两半**：`SKIP 签 PE (loment/dist 里没有 setup.exe; 先跑 loment_dist --emit)` 与 `SKIP 分离签名 (没有 loment/dist/SHA256SUMS)`。真正跑的只有凭据纪律 + `--print-cmd`。**本机没有可验的签名工件**；要真验得先 `loment_dist --emit`（带 exe）。**不抬高为 ACCEPT**。文档写"15 条判据" |
| C17 | 包管理器去 Python（Loment 版 `lompkg` 与 Python 版逐字节） | `python tools/loment_pkg_test.py` | **ACCEPT** | `loment_pkg_test: 3/3 通过`。文档 3/3 ✓ |
| C18 | L0 生成器去 Python（`lomc` 四后端逐字节 + `--check` 对账/漂移） | `python tools/loment_lomc_test.py` | **ACCEPT** | `loment_lomc_test: 4/4 通过`。文档写 3/3 |
| C19 | 原生 ELF 后端（**不经 clang**），与 clang 链产物行为逐值一致 | `python tools/loment_elf_test.py` | **ACCEPT** | `loment_elf_test: 9/9 通过`；`test_lomelf_matches_clang_behavior`（4 程序 stdout+退出码一致）、`test_lomelf_reports_unsupported_instead_of_miscompiling`（3 类报错）、`lomelf(种子) -> 编译器 -> 自编译产物 == 种子`。⚠ 主张里"种子→stage1→产物**全程无 clang**"**说宽了**：多数用例的 stage1 仍由 clang 编，只有 `test_lomelf_rebuilds_the_compiler_without_clang` 那条完全无 clang。文档写 5/5 |
| C20 | **自举侧镜像** `lomelf.lomt` 产出的 ELF 与参考逐字节相同 | `python tools/loment_elf_test.py`（同文件第 6 条用例） | **ACCEPT** | 同上 9/9；`test_lomelf_selfhost_matches_reference`（4 语料逐字节）+ `镜像 -> 编译器 -> 自编译产物 == 种子 … 自举侧全程无 clang 无解释器`。文档写"4 个语料"（数字对，但归属含糊） |
| C21 | **genesis** 起头，在**没有 clang** 的环境里跑通 `bootstrap.sh` 四条证明 | `python tools/loment_genesis_test.py` | **ACCEPT** | `loment_genesis_test: 4/4 通过`；`PATH 里没有 clang, bootstrap 仍四条全过 (genesis 起头)`，且 `全局表 2359/4096 · 标签表 14442/16384 · 回填表 18538/32768` 三条容量余量都印了出来。**`docs/160` 表里没有这一条**（§5.1） |
| C22 | 构建路径去 Python 第一格：Loment 版状态矩阵生成器与 Python 版逐字节相同 | `python tools/loment_status_test.py` | **ACCEPT** | `loment_status_test: 4/4 通过`（无参/`--check` 三路输出 + `--emit` 落盘字节 + 漂移检出 + 种子自举链编译 93494B）。**`docs/160` 表里没有这一条** |
| C23 | 构建路径去 Python 第二格：Loment 版发布清单生成器与 Python 版逐字节相同 | `python tools/loment_rel_test.py` | **ACCEPT** | **把报告文件移开 `docs/` 后**：`loment_rel_test: 5/5 通过`，rc=0。⚠ 报告留在 `docs/` 内时 `test_lomrel_emit_writes_same_bytes` 报 `两边写出的清单与仓库里的不同 (不该)`（与 C9 同因，§3.2）。**`docs/160` 表里没有这一条** |
| C24 | 原生 **PE** 后端：本机原生跑、不碰 clang/WSL；`lomstatus`/`lomrel` 编成 PE 输出与 Python 版逐字节相同 | `python tools/loment_pe_test.py` | **ACCEPT** | `loment_pe_test: 9/9 通过`；含 `test_pe_runs_the_loment_toolchain_natively`、`test_pe_builds_and_runs_without_clang_or_wsl`、`自举镜像产出的 PE 与参考逐字节相同`。**`docs/160` 表里没有这一条**；且 `docs/167` §5.3 记的"镜像**不支持按值传聚合**、enum-by-value 直接 SIGSEGV"这个真口子**不在 §2 不主张清单里**（§5.4） |

**计数**：ACCEPT **22** · REJECT **1**（C14）· UNVERIFIED **1**（C16）。

## 7. 结论

### 7.1 判定计数

24 条主张（`len(loment_audit.CLAIMS)` = 24）：**接受 22 · 驳回 1（C14）· 未验证 1（C16）**。
接受的那 22 条**本机真的跑过**且绿；驳回的 1 条是判据自己**崩**；未验证的 1 条是判据**跳过
了主张的两半**、汇总却报"通过"。

### 7.2 最该先修的一条

**`docs/160` §1 的主张清单与它点名的工具不是一回事**（§5.1）：工具里 24 条，文档标题写 23、
表格只有 20 行，`docs/202`/`docs/145` 又写 18。**C21（genesis）/ C22（lomstatus）/
C23（lomrel）/ C24（PE）从 2026-09-14 起就没进过那张表** —— 而它们恰好是"去 clang / 去 WSL /
构建去 Python"这半条叙事。第三方照 `docs/160` 复核会**整段漏掉它们**。更要命的是**没有判据
会红**：`test_audit_claims_match_ci` 只有"工具名 ⊆ 门禁 + 编号唯一 + **≥10 条**"三条，条数
错了它天然看不见。修法很轻：把表补齐到 24 行、把标题倒数改对，并给那条判据加一句
"条数与 `docs/160` 的表行数相等"。

### 7.3 这份复核**授权**与**不授权**什么

- **授权**说：*这份审计包点名的 24 条主张里，22 条在一个独立 agent 于干净工作树上跑绿；
  C14 的判据在本机崩、C16 的两半在本机跳过。* 以及：*审计包的文档与工具**不同步***（条数
  20/23/24/18；16/20 条主张的"通过标准"分母过期；§3.3 两条对抗步骤写反；§2 比 `docs/158` §4
  与 `docs/167` §5.3 短）。
- **不授权**说：
  - **不是"语言语义正确"** —— 冻结面内的判据仍然是**自证**的（审计包 §2.1 自己声明，
    我复核后同意）。两个同一作者的实现"两边一致"**不等于**"对"。
  - **不是"全门禁绿"** —— 审计包的 24 条**≠**整道门禁：`.github/gate-baseline.txt` 上另有
    10 条已知红（其中 4 条要读私有姊妹仓 `LinuxFUAI`，本机不存在）。`docs/202` §1 也这么记。
  - **不是"项目安全"** —— 审计包 §2.10 自己写了 Mimosa 未给结论。
  - **不是 M100 的"外部审计"那一格** —— 我是一个**独立 AI agent**，不是外部人类/组织。
    `docs/202` §2.2 把这一定义问题**明确留给用户拍板**；本文不替它下结论，只提供一份
    "独立 agent 的干净复核"作为那一格的候选证据。

### 7.4 我判为**表述不准**的地方（作者侧）

1. `docs/160` §1：条数（23）与表行数（20）与工具（24）三个数互不相同；四条主张整条缺失。
2. `docs/160` §0：说 `test_audit_claims_match_ci` "专门盯这件事" —— 它盯的是工具名与下限，
   **盯不住条数漂移**。
3. `docs/160` §1 的"通过标准"：20 条里 16 条的**分母**与工具实际打印的对不上（§5.2）。
4. `docs/160` §3.3 两条对抗步骤：**方向反了**（调低 `BUDGET` 不会红；改成 CRLF 后
   `git status` 本机**并不报干净**），§5.3。
5. `docs/160` §1 把 C14 记成"环境红，见 §2"，而 §2 **没有** C14 这一条；且 C14 的红是
   **未捕获异常**，不是别的判据那种 SKIP。
6. `docs/160` §2 的不主张清单**比 `docs/158` §4 / `docs/167` §5.3 短**：至少漏记"混宽比较发
   非法 IR""括号 `ptr` 转整数被截成 32 位""镜像不支持按值传聚合（发行包路径被它卡住）"等
   已知口子 —— 这些都是"门禁全绿也照不到"的，正是第三方最该被告知的。
7. C15/C16 的判据里，"自解压安装包"与"签名"那两半在有干净检出上**按设计跳过**，而汇总
   仍报 `N/N 通过` —— 读报告的人若不逐行看 SKIP，会把"未验证"当成"通过"。

> **对上文 §1 的一句更正**（§0–§5 按委托要求保持原样，故把更正放这里）：§1 我写了
> "没有 SKIP 掉的判据"。这是我在**跑到 C16 之前**写的 —— 实测下来**C16 的两半确实跳过了**
> （§6 的 C16 行）。所以那句应读作"**除 C16 之外**没有整条 SKIP 的判据"；C14 是崩、不是 SKIP。

---

## 8. 作者侧附记（**不是复核者的文字** —— 以上全部是复核者原文）

> 这一节的作者是仓库侧（作者的 agent），与上面那份复核**无关**。写在这里是为了让读到
> `docs/203` 的人知道"这份报告之后发生了什么"，而**不是**让报告本身去追认后续改动。
> **复核者的结论一个字都没改。**

**复核之后同一天的处置**（同一批提交）：

| 复核的意见 | 处置 |
|---|---|
| §5.1 / §7.2：表 20 行 / 标题 23 / 工具 24 / `docs/202` 与 `docs/145` 写 18 | 表补到 **24 行**（漏的正是 C21–C24），五处数字改对；**`test_audit_claims_match_ci` 从"钉 `>= 10` 下限"改成"钉*等于表的行数*"** —— 这条漂移以前**没有任何东西会红** |
| §5.2：20 行里 **16 行**的"通过标准"分母过期 | 那一列**整列改口径**：只写不变量、分母以工具尾行为准；`docs/158` §6 那份一致性套件清单同样处理 |
| §5.3 / §3.3：两条对抗步骤写反 | 按实测更正（`BUDGET` **调低不会红**、要红得调高，且没有判据拦调低；CRLF 那条 `git status` **并不**报干净） |
| §6 **C14 REJECT**（判据**崩**，不是 SKIP） | **在同一批提交里修了**：`tools/loment_filetype.py` 新增 `_plain_file()`（`OSError` → "不是文件"）。⚠ 那条 REJECT 对**它审的那个版本**仍然成立 —— 复核者没复核修完的版本 |
| §3.1 / §6 **C13 保留意见**（`json.loads(p) and 1` 恒真） | 改用同文件早有的 `_kind_of`；修完仍 4/4 绿 ⇒ 库没问题，坏的是断言 |
| §5.4：§2 不主张清单比别处短 | 补记五条（含"混宽比较两边一致地错""镜像不支持按值传聚合"） |
| §6 **C16 UNVERIFIED** | **未处置** —— 要真验签名得先 `loment_dist --emit` 出带 `setup.exe` 的包，那是"打包"那一步（`docs/202` 第 8 项）的事 |
| §6 C2 那条**受负载影响**的墙钟预算（三路并行 67.7s / 单跑 45.6s，护栏 60s） | 记进 `docs/160` §2：它**会**在门禁并发时假红 —— 门禁的"单跑复验"正是为这一类准备的 |
| §7.3：本文**不授权**"全门禁绿" / "语言语义正确" / "项目安全" | 照单全收，三条都写进了 `docs/202` §1 第 2 项的现状栏 |

**一句要留着的话**：复核者指出的天花板（冻结面内的判据全是**自证**的，"两边一致"不等于"对"）
**没有被这次处置改变** —— 补的是**账目**，不是**语义**。
