# 150 · Loment 自举（P8，M79–M88）

> 状态: **进行中**（2026-09-09）· 已完成: M79 · 部分: M80 · 自检: `tools/loment_p8_test.py` 2/2
> 门禁: `ci.py --static-only` 10/10

## M79 · Loment 版 lexer ✅

`loment/selfhost/lexer.lomt`：用 Loment 写的词法器，输入字节缓冲，输出 20B/token 记录
（`kind|start|len|line|col`）。规则与 `tools/lomc.py` 的 `lex()` 一致：

- 空白 / `//` 行注释 / `/* */` 块注释；
- 字符串（含 `\` 转义，span 含引号）；
- 十进制与 `0x` 十六进制数；
- 标识符（`[A-Za-z_][A-Za-z0-9_]*`）；
- 单字符 punct 集 `{}()[]:;=@,.+-*/%<>!&|^?`；
- 末尾 `eof`。

**判据（与 Python 版 token 流一致）**：`loment_p8_test.py` 用 IR 路径编译 lexer + C 驱动，
对 4 个真实文件（含中文注释、含 lexer 自身源码）逐 token 比较
`(kind, start, len, line, col)`——**全部一致**。

实现过程中修掉的两个真 bug（都属于编译器，不是 lexer）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `else if` 解析失败 | 语法只支持 `else { }` | parser 支持 `else if` 链 |
| 2 | `&&`/`||` 嵌套时 LLVM 报 `PHI node entries do not match predecessors` | 短路降级把 phi 前驱写死成 `rhs_l`，右操作数自己也可能产生新块 | `_Ir` 跟踪 `cur_label`，phi 用真实前驱；每个函数显式 `entry:` 标签 |

第 2 个 bug 是自举逼出来的：手写示例里的 `&&` 都很简单，只有把真实程序（lexer 的
多重条件）交给后端才暴露。修完后 11 条双路径示例输出与修改前逐值一致（见 docs/145 P8 证据）。

## M80 · Loment 版 parser（部分）✅

`loment/selfhost/parser.lomt`：消费 M79 的 token 记录，产出规范 AST dump。
本阶段覆盖 **module 与 fn 签名**（参数 `名:类型`、可选 `-> 返回类型`），
dump 形如 `(module m (fn f (p x u32) -> u32) (fn g) )`。

**判据（部分）**：与 Python 版 AST 结构一致 —— `loment_p8_test` 对 5 个真实文件
（mathutil / bytes / ahci / allocator / parser 自身）逐字符比较 dump，**全部一致**。
函数体（语句/表达式）解析待做，故为部分。

实现要点：Loment 无元组返回，用 `(i << 32) | o` 打包"token 游标 + 输出游标"；
token 文本比较用 `tok_is(src, t, i, s)` 逐字节比。

## 待做（M81–M88）

| # | 里程碑 | 现状 |
|---|---|---|
| M80 | Loment 版 parser（函数体） | 部分（签名已一致） |
| M81 | Loment 版类型检查 | 未开始 |
| M82 | Loment 版 IR 生成 | 未开始 |
| M83 | 自编译 | 未开始 |
| M84 | 三阶段自举定点校验 | 未开始 |
| M85 | 自举编译器跑全部测试 | 未开始 |
| M86 | 自举性能优化 | 未开始 |
| M87 | 引导脚本与发布包 | 未开始 |
| M88 | 自举版本发布 | 未开始 |

诚实说明：自举是 100 里程碑里最大的一块，M79 是它的第一步（词法层已证明"Loment 能写
自己的工具"）。后续每个阶段都需要先把对应的编译器阶段用 Loment 重写，再与 Python 版
做结构/字节级对照——这正是 M80–M82 的判据形式。
