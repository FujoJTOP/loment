# 217 · `loment lompicheck`：发布口的 lompi 跟上开发口了没

> 2026-10-09 · 引擎 `loment/tools/lompicheck.lomt`（Loment 写的）· 注册器 `tools/lompicheck.py --install`
> · 上游：`docs/171`（两个发布口）、`docs/169`（CLI 与启动器）、`docs/189` §47（指针比较那条限制）

## 0. 一句话

**`FujoJTOP/lompi` 是 `loment` 开发口里 `lompi/` 的一份发布副本**（`docs/171`：开发在主仓、
发布口只读、由 `tools/loment_publish.py --push lompi` 整体覆盖推上去）。于是"发布口是不是
最新"是个**可以算**的问题 —— 两个仓库里每个文件都有内容，逐文件比一遍就行。

```bash
python tools/lompicheck.py --install ~/.local/bin   # 装一次（生成两个注册器）
loment lompicheck                                   # 之后随时
```

```
lompicheck: the lompi publish outlet against the loment source
  source  <你的 loment 检出>
  outlet  ~/.lompi/cache/lompi
[OK] the outlet is in step with the source (170 file(s) identical)
```

**始终以开发口为准**，而且**每次都查发布口** —— 不查就不知道它掉了队。掉了队**不是使用者
能修的**（发布口只由那条 `--push` 写），所以这条命令会明确说"该去提 issue 提醒开发者"，
并给出链接：`https://github.com/FujoJTOP/lompi/issues/new`。

退出码：`0` 一致 / `1` 不一致 / `2` 用法错或读不了。

## 1. 它是两半拼的，为什么必须两半

| 这一半 | 在哪 | 干什么 | 为什么不能是另一半 |
|---|---|---|---|
| **引擎** | `loment/tools/lompicheck.lomt`（编成 PE/ELF） | 只读**本地两棵树**，逐文件比内容 | 它**不能联网**：PE 运行时只实现 8 个 syscall，里面没有 socket（`loment/tools/win_shim_data.lomt` 的导入表就是全部家当）。换 Linux ELF 也不行 —— `socket` 有，但到 `api.github.com` 要走 TLS，工具链里没有 TLS |
| **薄壳** | `--install` 生成的两个注册器 | 用 `git` 把 `FujoJTOP/lompi` 的一份缓存 clone 更新好，再把两个根交给引擎 | 它是 shell，能跑 git；引擎是 Loment，不能 fork |

这与 lompi 自己"PE 上没有 socket，于是 `lompi fetch` 只把 `git clone` 打印出来"是**同一个
边界**：**是运行时缺，不是设计上不想要**。

Loment 在 Git Bash 里更愿意用 `loment.cmd`（POSIX 那份在 Windows 上会去链 ELF），薄壳替你把
这件事办了 —— 所以 `loment lompicheck` 在两个 shell 里都能敲。

## 2. 比的到底是什么

发布口那份是**摊平**过的（`loment_publish.REPOS["lompi"]` 的 `paths` + `renames`），于是：

| 开发口 | 发布口 |
|---|---|
| `lompi/<x>` | `<x>`（摊平到根） |
| `.claude/skills/lompi/<x>` | `.claude/skills/lompi/<x>`（原地） |

发布口**根上**还有三样东西开发口里没有对应物，一律当例外：

- `README.md` —— `loment_publish` 自己写的（`spec["readme"]`）；
- `.git` —— 那份缓存 clone 自己的元数据；
- `.claude` —— 属于**另一对**（上表第二行整棵落在那儿），不是"多出来的"。

上面这几条引擎里是**写死的**（引擎是 Loment，引不了 Python 的发布清单），所以有一条判据
从 `loment_publish.REPOS["lompi"]` **推出应有的样子再核引擎源码** —— 清单加了第三棵子树而
引擎没跟上，那条红。

比对用的是**内容**（分块比字节，多大文件都只吃 2 × 8 KiB）。**不**比版本号：lompi 自己报的
`0.1.0` 是个标签，两边永远是它，盯不出任何东西。

## 3. 实测的两条坑（都会静默地把好结果说成坏结果）

### 3.1 `core.autocrlf` —— 一个完全正常的发布口会被报成 170 处差异

**发布口仓库里没有 `.gitattributes`**（开发口那份是给开发口用的，`--push` 只往 lompi 那个库
发 `lompi/` 与 `.claude/skills/lompi/`）。于是 Windows 上按默认设置 clone 出来的工作树**全是
CRLF**，而开发口那边因为 `.gitattributes` 的 `*.lomt text eol=lf` 是 **LF** —— 逐文件比
字节，170 个全不同。实测过：污染后 46/46 行是 CRLF。

薄壳因此**显式**用 `-c core.autocrlf=false -c core.eol=lf` 拉，并且每次跑之前都会
`git config` 再 `reset --hard` 一遍 —— 老的、按默认设置拉下来的缓存会被自动修好（实测：
把一份 CRLF 缓存摆在那儿再跑一次，出来是 `[OK]`，工作树回到 LF）。

### 3.2 参考实现的 codegen 不能比两个指针（`docs/189` §47）

引擎里判空写成 `(p as u64) == 0` 而不是 `p == (0 as ptr)`：`tools/lomentc.py` 会为两个
`ptr` 比相等发出 `icmp == ptr`（**非法 IR**，自举那份发的是正确的 `icmp eq`）。这条限制仓库里
早就记着，`loment/selfhost/potato.lomt` 也是照它绕的 —— 本文件只是跟着绕。

**为什么这条对本文特别要紧**：`tools/lompicheck_test.py` 是用**仓库里的参考编译器**把引擎
编出来测的，所以引擎每碰一次指针比较，那条判据就整条挂。它第一次跑就是这么发现的。

## 4. 边界（诚实清单）

- **只认 `main`**：两边都比默认分支。feature 分支上的 `lompi/` 不在比对范围里。
- **缓存 clone 是浅的**（`--depth 1`）：只用来读文件，不给你历史。
- **取不到网就退化成"读旧缓存"**，并且**会在输出第二行说明**这份可能是旧的；一份缓存都
  没有、又取不到网才退出 2。
- **要能编 Loment**：引擎第一次跑会 `loment build` 一次（之后直接用产物）。没有工具链就
  装不了这条命令 —— 它是给"有 loment 检出的人"用的。
- **包里没有它**：安装包（`loment_dist`）里没有 Python，而 `--install` 那一步要 Python。
  发行包里因此没有这条命令；`loment/tools/lompicheck.lomt` 随源码发，谁有检出谁能编。
- **它不修任何东西**：发布口只由 `--push` 写，这条命令只**说**。

## 5. 判据

`tools/lompicheck_test.py`（登记在 `ci.py` 的 `STATIC_CHECKS`，也在发布清单 `GLOBS` 里）：

1. 引擎里那两对根必须**正好**是发布清单里那两棵子树（外加根上那三条例外）；
2. `--install` 生成的注册器：纯 ASCII、`.cmd` 是 CRLF、POSIX 那份是合法 sh、两边都关掉了
   `autocrlf`、都点名了引擎的源文件与产物路径；
3. 用**仓库里的参考编译器**把引擎编出来，拿本仓**真的** `lompi/` 当源侧造一个发布口目录，
   跑出四种结论：一致（并核文件数）/ 改一个 / 删一个 / 多一个（文件与整棵目录各一次）；
4. argv 的三种形状：参数不够报用法；第 4 个参数是 `stale` 时那句"可能是旧的"要说出来。

夹具是**真源码树**拷贝出来的，不是手写的小玩意 —— 手写的不会告诉你"170 个文件里从哪一个
开始不对"。
