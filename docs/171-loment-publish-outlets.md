# 171 · Loment / lompi 的发布口（两个私有库）

> 2026-09-15 用户定 · 工具 `tools/loment_publish.py` · 判据（门禁模式）`--check`
> · 上游：`docs/162`（发行包）、`docs/170`（lompi 随包）

## 0. 一句话

**开发在主仓（FujoOS 单仓），发布在各自的私有库。**

| 库 | 地址 | 装什么 |
|---|---|---|
| loment | `github.com/FujoJTOP/loment`（private） | 语言本体 + 工具链 + 文档 + 编辑器支持 |
| lompi | `github.com/FujoJTOP/lompi`（private） | 包管理器（摊平到根） |

**单仓里的内容原样不动** —— 两个库是**额外的发布口**，不是搬家。判据与跨线引用照旧看单仓。

## 1. 为什么是"发布口"而不是"搬家"

单仓里 961 个已跟踪文件里，Loment 只占 314、lompi 33；其余是内核 / sdk / ui / 资产。
而这些路径**互相引用得很密**：`CLAUDE.md` 指向 `lom/*.lom`，`docs/*` 里到处是
`loment/tools/lomcli.lomt`、`tools/loment*.py`。真搬走就要一次改干净所有引用，
而其中一部分归**别的线**（跨线协议：不碰对方的路径）。

所以分工是：

- **单仓 = 正本与开发地** —— 所有线在这里干活，判据在这里跑；
- **两个库 = 发布口** —— 外面的人 `git clone` 它们就能拿到 Loment / lompi，
  不用把整个 FujoOS 拖下来。

## 2. 怎么发

```
python tools/loment_publish.py --status        # 切出来会是什么样（文件数 + 树哈希）
python tools/loment_publish.py --check         # 门禁: 路径清单自洽
python tools/loment_publish.py --push loment   # 或 --push lompi / --push all
```

- **切分用 `git filter-repo`，在克隆副本里跑** —— 它默认**就地重写仓库**，绝不能对工作仓
  下手。工具每次都 `git clone --no-local` 到临时目录再过滤，跑完删掉。
- **历史保留**：切出来的库带自己那段历史（loment 侧 143 个提交，lompi 侧 1 个 ——
  `lompi/` 本来就是一次性收进单仓的）。
- **推成 `main`**：空库的**第一个 ref** 会成为默认分支。**别**用 `gh repo create --source
  --push` —— 它会把你**当前分支名**（`Fujoos-FujoLang-DEV`）当默认分支，一个新库顶着
  单仓的开发分支名很怪。第一次跑就撞上了，现在改成显式 `push HEAD:main` 再 `PATCH
  default_branch`。
- **幂等**：库已存在就只推，不重建。

## 3. 路径清单为什么这样切

**Loment 那侧保留原结构**（`loment/`、`lom/`、`tools/`、`docs/`、`editors/`）：
那边**到处是按路径引用的**，路径一改，全部文档与注释失效。

| 切什么 | 为什么 |
|---|---|
| `loment/` | 语言本体：前端、自举镜、示例、库、种子 |
| `lom/` | L0 接口契约（跨线契约，`docs/141`） |
| `editors/` | vim / VS Code 的语法与 LSP 支持 |
| `docs/manual/`、`docs/{14?,15*,16*,17*}-loment-*.md`、`docs/141-l0-lom-spec.md` | 语言文档 |
| `tools/lom*` | 工具链（Python 参考实现 + 全部判据 + 发布/打包工具） |
| `.claude/skills/loment/` | 给 AI agent 的指南 |
| `LICENSE` | 没有它不算一个能发出去的仓库 |

**lompi 那侧摊平到根**（`--path-rename lompi/:`）：它是个自足的小项目，`lompi.lomt` 直接
在根上才像正经仓库；而且它的模块互相用 `use "lpi_cli.lomt"` 这种**相对 CWD** 的导入，
摊平后"在仓库根下 `loment build lompi.lomt -o lompi`"正好成立。

**有意不在里面**的：`tools/lompi_sync.py` 与 `tools/loment_lompi_test.py` 留在单仓 ——
它们管的是"**单仓里那份 lompi 快照**"（同步与随包判据），属于 Loment 侧的机制，
不是 lompi 自己的东西。

## 4. 这一层的边界（诚实清单）

- **不在 CI 里跑 `--push`**：推送是对外动作，得人按。`--check` 进 CI，它只读。
- **没有自动同步**：单仓提交之后，两个库**不会自动跟上** —— 要显式跑 `--push`。
  判据 `--check` 只保证**清单自洽**（路径都存在、条目数没崩），
  **不保证两个库是最新的**（那要联网比对，不适合进门禁）。
- **发布口是只读的**：**别在 `FujoJTOP/loment` / `lompi` 里直接提交** —— 下次
  `--push` 会 `--force` 把它盖掉。两个库的 README 第一段就写着这句话。
- **`--force` 是刻意的**：发布口的内容应当**完全**由单仓决定；留着分叉只会让人困惑。
  代价是"在那边提交过的东西会没"。
- **切的是 `HEAD`**：只发已提交的内容，工作区里没提交的改动不会进去。
