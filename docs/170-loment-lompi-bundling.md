# 170 · lompi 随 Loment 一起发行

> 版本 `0.1.4-alpha2.3` · lompi `0.1.0`（完全稳定）· 实现 `loment/tools/lomcli.lomt` 之外
> · 判据 `tools/loment_lompi_test.py` · 同步 `tools/lompi_sync.py`
> · 边界见 `docs/169 §2`（`loment` 的命令面里**不出现** lompi）

## 0. 一句话

**lompi 从 0.1.4 Alpha2.3 起随 Loment 一起安装**，但它**不是 Loment 官方工具** ——
它不编 Loment、不读 Loment 源码树，是**另一个命令**。装完 `loment`，`lompi` 就在 PATH 上；
`loment help` / `loment commands` 里没有它，`loment <任何东西>` 也不转发给它。

它是**用 Loment 写的**，由同一条自举链编出来，所以"随包发行"这件事本身是可复现的。

## 1. 正本在外面，仓里只放快照（两组配对）

lompi **在外面开发**，本仓 `lompi/` 是**随包发布的快照**。有两组配对，
`tools/lompi_sync.py` 管它们：

| 组 | 正本 | 仓内副本 |
|---|---|---|
| 源码 + 夹具 | `D:\Dev\Lolment-ku\lompi`（`--from-dir` / `LOMENT_LOMPI_DEV` 可换） | `lompi/` |
| agent 指南 | `~/.claude/skills/lompi/SKILL.md`（lompi 线在那里写它） | `.claude/skills/lompi/SKILL.md` |

```
python tools/lompi_sync.py                # 门禁模式: 逐字节比 (正本不在本机则**明说跳过**)
python tools/lompi_sync.py --from-dev     # 正本 -> 仓 (把外面的改动收进来)
python tools/lompi_sync.py --to-dev       # 仓 -> 正本 (在仓里改了要同步回去)
python tools/lompi_sync.py --status       # 只打印两边的摘要与各自哈希
```

**为什么要有这个工具**：CLAUDE.md 里那句「正本只有一份 —— 别处都只是指针或同字节拷贝，
复制出第二份必然漂」。这里**必须**有两份（正本在开发区），所以就得有个东西把"漂了"变成
**看得见**的红。它不是摆设 —— 第一次跑就当场抓到 `lpi_pkg.lomt` 两边不同；写这条的时候
又抓到指南那份漂了。

**正本不在本机时门禁模式跳过并打一行明说**（CI / 别人的机器上没有那条路径），但**仓内那份
自身的完整性是无条件查的**（每个模块文件在不在、夹具目录在不在）。

## 2. 打包与安装

- `loment_dist.py` 的 `TOOLS` 从二元组变成**三元组**：`(工具名, 入口, 编译时的 CWD)`。
  CWD 那一格是给**路径形式的 `use "..."`** 用的 —— 自举镜按 **CWD** 解析相对路径
  （参考实现按入口文件所在目录），仓库其它工具全用名字形式（`use bytes`），所以它们一律
  写 `"."`。**lompi 是唯一一个用路径形式 import 的**，它那格是 `"lompi"`。
- 包里落 `bin/lompi`（Windows 是 `bin/lompi.exe`）、`share/lompi/skill/SKILL.md`。
- **agent 指南与 loment 那份同一套装法**（用户 2026-09-15 要求）：
  装进 `~/.claude/skills/lompi/`、往 Codex 的 `~/.codex/AGENTS.md` 写一段**独立标记**的
  指针、卸载时摘掉。**标记用 `lompi:` 前缀而不复用 `loment:`** —— 两份指南是两件事，
  卸载一份不该动另一份。`--no-skill` / `-NoSkill` 同时管两份。
- 装完的冒烟：`loment version` 之外再加一条 `lompi`（无参打用法、退 2）—— 它是独立命令，
  所以单独冒烟。
- **安装脚本一律纯 ASCII**（PowerShell 5.1 按 ANSI 读无 BOM 脚本，非 ASCII 会连带把后面
  的行解析坏）。写这一段时踩过一次：注释里写了中文，`install.sh` 立刻不再是纯 ASCII ——
  判据里有一条专门守它。

## 3. 边界（用户定的，别越）

| 是 | 不是 |
|---|---|
| 随 Loment 一起装，在 PATH 上 | Loment 官方工具 |
| 用 Loment 写的、同一条自举链编的 | `loment` 的子命令 |
| 有自己的指南、自己的版本号、自己的自检 | `loment help` 里的一行 |

**不要**因为"它随包一起装"就把它挂进 `loment` 的命令表：**装在一起 ≠ 是同一件工具的部件。**
判据 `test_lompi_is_not_a_loment_subcommand` 钉着这条（往启动器或 `lomcli.lomt` 的目录里
塞 `lompi` 会立刻红）。

## 4. 判据（13 条，`tools/loment_lompi_test.py`）

判的是"它作为**发行件的一部分**是好的"，不判 lompi 自己的功能对不对（那是它的自检驱动的事）。

| 组 | 判的 |
|---|---|
| 编得出来 | 参考实现能**检查**它（这条曾经是红的，见 §5）；发行包同一条路（stage1 + lomelf）能编出来 |
| 跑得对 | **`lpi_test.lomt` 逐模块自检全绿**（7 个模块，退出码即结论）；`index` 列出夹具里的 5 个包，且**同名不同版本给不同内容哈希**；`check` 有**分辨力**（合法库退 0 说 OK，现造的坏库退 1）；`show` 报完整 64 位内容哈希；`version` 与源码真源一致且钉在 0.1.0 |
| 边界 | `lompi` 不出现在 `lomcli.lomt` 的命令目录 / help 总览，也不出现在两个启动器里 |
| 装法 | 包里有指南；两个安装器都按 loment 那套处理它，**且带自己的标记**；卸载摘得掉；五个脚本纯 ASCII |
| 一致性 | 仓内快照与外面正本逐字节一致（正本不在则明说跳过） |

**证伪过**：往启动器塞 `lompi` → 红；安装脚本混一个中文字符 → 红；把 lompi 的标记并成
loment 的 → 红；版本号漂到 0.2.0 → 红。

## 5. 诚实清单

- **参考实现与自举镜为 lompi 产出的 IR 仍不一致**（2026-09-15 实测 **114 行差异**，全是
  **宽度**类：参考把 `(off as u64) * 4294967296` 折成 i64，镜折成 i32）。属于任务 #18 记着
  的镜的混宽毛病，**既有、非本轮引入**；在这一处取值无害（操作数是字面量 0），所以不影响
  产物。**后果要说清**：同一份源码两侧产不出同一份 IR，所以 lompi 进不了 p8 的逐字节语料
  —— 本文件判的是"发行件是好的"，**不是**"双实现一致"。
- **`lpi_test.lomt` 不进发行包**（它不是 `bin/lompi` 的组成部分），但**跟着一起收进仓**，
  留在这里当判据用 —— 它比冒烟一条命令强得多。
- **lompi 自己的功能判据在它那边**（正本里的自检驱动）。本文件只守"随包"这一层。
- 打包用的是 **stage1**（自举镜），不是参考实现 —— 因为发行包就是这么编的，判据要判
  "发出去的那个东西"。
