# 148 · Loment 工具链（P6，M55–M66）

> 状态: **已实现并通过门禁**（2026-09-09）· 入口: `python tools/loment.py <子命令>`
> 自检: `tools/loment_tools_test.py` 11/11 · 门禁: `ci.py --static-only` 7/7
> 一句话: **从"能编译"到"能干活"——格式化、语言服务、包管理、文档、调试信息、
> 测试/基准/覆盖、增量与缓存全部落地，且每一项都有可复现判据。**
>
> 编辑器宿主（M56）：`editors/vscode/` —— 语法高亮 + LSP 客户端（补全/跳转/诊断/格式化，
> 服务端就是本文件的 `loment_lsp.py`）+ 构建/检查/运行命令；打包 `tools/vscode_ext.py`，
> 无头验收 `tools/vscode_ext_test.py`（含完整 LSP 往返）；细节见 `docs/155` §8。

## 0.1 语法高亮（`editors/vscode/syntaxes/`）

两套 TextMate 语法：`loment.tmLanguage.json`（`.lomt`）与 `lom.tmLanguage.json`（`.lom`）。
2026-09-11 做了一次"彻底"扫：**用 VS Code 同款引擎**（TextMate + Oniguruma）把语法跑成
scope 流逐 token 核对，补掉了这些盲区：

| 构造 | 之前 | 现在 |
|---|---|---|
| `=>`（match 臂） | 拆成 `=` + `>` 两个色 | `keyword.operator.match-arrow` 一个 |
| `?`（try） | `punctuation` | `keyword.operator.try` |
| `self` | `keyword.control` | `variable.language.self` |
| `Color::Red` | `Red` 被当成类型名 | `entity.name.type.enum` + `constant.other.enummember` |
| `fn f(a: u32)` 形参 | 无 | `variable.parameter`（签名整段进 `meta.function.signature`） |
| `let x` | 无 | `variable.other`（`if let` 的模式不误判） |
| `struct S { a: u32 }` 字段 | 无 | `variable.other.member`（整段 `meta.block.struct`） |
| `MAX_BLKS` | 被当成类型名 | `constant.other` |
| `capability blk : disk[0..4]` | 只认 `capability` | 域名 `entity.name.constant.capability` + 空间名 `support.type.capability-space` |
| 模块名 / 记录名 / 常量名 | 无 | `entity.name.namespace` / `entity.name.type` / `variable.other.constant` |

效果（同一套引擎实测的"无 scope 占比"）：`.lom` 两份语料 **0.0% / 0.1%**；`.lomt` 四份
3.4% / 7.9% / 9.6% / 20.9%（剩下的全是表达式里的裸标识符 —— TextMate 层面没有类型信息，
它们继承默认前景色是正常的）。

**两类静默失效已进门禁**（`test_vscode_grammar_lints`）—— 它们不报错，只是颜色不对：

1. **scope 名用了自造根名** ⇒ 主题不认那段，显示成默认前景色（看起来就是"没高亮"）；
2. **`match` 里吃掉引号** ⇒ 截胡字符串的**开引号**，于是整份文件剩下的部分被当成一个
   未闭合字符串染色。我加 `excluded "` 这条时就踩了：`demo.lomt` 从第 21 行起整片变字符串色。
   只有 `begin`/`end` 允许碰引号。

### 怎么亲自看高亮对不对

```
python tools/vscode_ext.py --doctor     # 装没装 / 语法是不是旧版 / 有没有人抢 .lomt
```

无头核对语法本身要用 VS Code 同款引擎：`npm i --prefix <dir> vscode-textmate vscode-oniguruma`，
加载语法后逐行 `tokenizeLine` 打印 `scopes` —— 这是唯一能"看到" VS Code 会怎么染色的办法
（`vscode_ext_test.py` 只做正则/结构层的检查，看不出配色对不对）。

**高亮没出来时的排查顺序**（按概率）：① 装完/更新完**没重载窗口**（语法在窗口启动时加载）；
② 该文件的**语言模式**还是 Plain Text —— 在扩展装上之前打开过的文件会记住旧的关联，
`Ctrl+K M` 改成 Loment 即可；③ 跑一次 `--doctor` 看上面三项。
（想直接看某个位置被染成什么：`Developer: Inspect Editor Tokens and Scopes`。）

## 0. 统一入口

```
python tools/loment.py fmt   FILE...        # M55
python tools/loment.py doc   FILE           # M58
python tools/loment.py diag  FILE           # M64
python tools/loment.py ir    FILE [--objdump]   # M60
python tools/loment.py test  FILE           # M61
python tools/loment.py bench FILE [--n N]   # M62
python tools/loment.py cov   FILE [--call F]# M63
python tools/loment.py build DIR            # M65/M66
python tools/loment.py pkg   resolve|verify # M57
python tools/loment.py lsp                  # M56
```

## 1. M55 格式化器 `lomfmt`

- 复用 `lomc.lex` 词法器 → 合并多字符运算符 → 按缩进/空格规则重排；不改变 token 语义。
- 判据：**幂等**（格式化两次结果逐字节相同）+ **语义保持**（格式化前后 Potato 形式对象相同）。
- 覆盖全部 16 个示例（`loment_tools_test.py::test_m55_*`）。

## 2. M56 语言服务 `loment_lsp`

最小 LSP（JSON-RPC over stdio）：`initialize` / `didOpen` / `didChange` / `definition` /
`completion` / `shutdown`。`handle()` 是纯函数，可无编辑器自测（判据三项：诊断、跳转、补全）。

> 诚实边界：**未在真实编辑器里实测**（无 VS Code 扩展宿主），自测驱动的是同一 `handle()`。

## 3. M57 包管理 `lompkg`

- 包 = 目录 + `pkg.json`（name/version/deps）；依赖解析为**拓扑序**并检测环；
- 校验和 = 包内 `*.lomt`（按路径排序）逐文件 sha256 合并；`resolve --write` 写 `pkg.lock`，
  `verify` 重算比对（改动一个字节即报 DIFF）。

## 4. M58 文档生成 `lomdoc`

从 `.lomt` 生成 Markdown：能力域表、常量表、struct 字段、enum 变体（含载荷）、trait/impl、
函数签名；文档注释取声明前的连续 `///` 行。

## 5. M59 调试信息（DWARF）

`lomentc --debug`（配合 `--emit-llvm`）为 IR 附加：

- `!llvm.dbg.cu` / `!DIFile` / `!DISubroutineType`；
- 每函数一个 `!DISubprogram`，**并挂在 `define` 行上**（关键：只给指令加 `!dbg` 而
  `define` 不挂 scope，LLVM 会整块丢弃行表）；
- 每条语句一个 `!DILocation`，`w()` 统一追加 `, !dbg !N`。

验证：`clang -g -c` 后 `llvm-objdump -d -l` 输出 `; toolchain.lomt:7` 这样的源行标注
（本机 LLVM 22 无 `llvm-dwarfdump`，用 `objdump -l` 等价验证）。

> 诚实边界：行表到**语句**粒度；无变量位置表（`llvm.dbg.declare`/`dbg.value`），
> 因此调试器能按行断点、不能打印局部变量。

## 6. M60 IR 查看器

`loment ir FILE` 打印 IR；`--objdump` 追加 `clang -c` + `llvm-objdump -d` 的机器码。

## 7. M61 测试框架

约定 `fn test_*() -> bool`：`loment test FILE` 生成 Rust harness（`include!` + 计数），
`rustc -O` 编译运行，输出 `PASS/FAIL` 与 `RESULT: n/m PASS`，失败退出码 1。

## 8. M62 基准框架

约定 `fn bench_*() -> u32`（零参）：同一模块分别走 Rust 路径与 IR 路径，循环 N 次取纳秒级耗时，
输出对照表。Rust 侧用 `std::hint::black_box` 防常量折叠；IR 侧用 `timespec_get`。

样例（`toolchain.lomt`，N=2e6，本机）：

| 函数 | Rust 路径 (ns) | IR 路径 (ns) | 比值 |
|---|---|---|---|
| `bench_fib` | 806000 | 1984100 | 2.46x |
| `bench_popcount` | 805600 | 1990100 | 2.47x |

> 读法：IR 路径约慢 2.5x（未内联 + 累加器 volatile）。这是**测量事实**，不是语言优劣结论。

## 9. M63 IR 级覆盖率

`--coverage` 在每个基本块开头对 `@__loment_cov[i]` 加一，并导出块总数 `@__loment_cov_n`；
`loment cov FILE --call F` 生成 C 驱动调用入口并打印 `COV hit/total pct`。
样例：`toolchain.lomt: COV 6/31 19.4%`（`cov_main` 只走 if 真分支）。

## 10. M64 诊断分类

`tools/loment_diag.py` 对编译器消息做模式分类，17 类错误各有稳定错误码（E001–E017）与
可执行建议；测试用 13 个反例片段断言**每条都被分类**（无 E999）且带建议。

两条与"分类表可信"直接相关的纪律：

- **码按修法分，不按消息措辞分**：`实参类型 …` / 内建实参 / `载荷类型 …` / `return 类型 …`
  都归 E001（都在说"这里类型对不上"），用户要做的是同一件事。
- **顺序即优先级**：`E002` 必须排在 `E001` 前（`载荷类型 Foo 未声明` 要做的是"先声明 Foo"），
  `字段 a 重复$` 必须锚定结尾（否则吃掉 E015 的 `重复初始化`）。
- `test_m64_all_reference_messages_are_classified` 用 ast 抽出 `lomentc.py` 里**全部**
  `errs.append` 消息模板（81 条）逐条分类 —— 分类表漏一条，那条规则在"自举 vs 参考"的码集
  对照里就两边都成 E999 被丢掉，**缺口会静默消失**。

## 10b. M85 规则等价性对照

`tools/loment_rule_parity.py`：一条参考规则配一个**最小负例**（60 条），两边各跑一遍，
按 `loment_diag` 的统一码口径比**码集**；状态分 `EQUAL`/`MISSING`/`EXTRA`/`DIFF`/`NOPY`。
门禁是**棘轮**：`eq >= BUDGET` 且 `EXTRA == DIFF == NOPY == 0` —— 补完一批才把 `BUDGET`
往上调，只调低等于隐瞒缺口。已进 `tools/ci.py` 的静态门禁。

当前实测：**60/60 等价、假阳性 0、码漂移 0** —— 自举 checker 与参考实现在这 60 条规则上
完全等价（批次 1 声明级规则 + 批次 2 语句级/表达式级类型比对 + match/`?`/借用检查）。
三处**刻意偏离**（下标与 `&` 在类型未知时不报、`match` 主体未知时不报、`?` 只在能确定
不是 `Result` 时报）都是保守方向，逐条记在 `docs/150`。

## 11. M65/M66 增量构建与缓存

`tools/loment_build.py`：

- 缓存键 = sha256(源文件 + 递归 `use` 的 `.lomt` 内容)；
- 键命中且产物在盘 → 跳过；否则重编该单元；
- 索引 `.loment-cache.json`；`--report` 打印逐文件命中/重编表。

实测（16 个示例）：冷构建 **82.6 ms** → 热构建 **9.0 ms**（9.2x）。

## 12. 未覆盖边界（诚实清单）

- LSP 未接真实编辑器，也无跨文件符号索引（只索引当前文档）；
- `lompkg` 只支持本地路径依赖（无网络仓库、无版本区间求解）；
- DWARF 无变量信息；覆盖率是**块覆盖**，不是行/分支覆盖；
- 测试框架只跑 Rust 路径（IR 路径由 `bench`/`cov` 覆盖）；
- 增量构建以文件为粒度，不做函数级增量。
