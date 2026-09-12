# 161 · 检出行尾与"两个工作树为什么不一样"

> 触发: 2026-09-12。同一个分支、同一份代码，在两个工作树里跑出不同的红，
> 一度被归因成"上游既有逻辑红"。真因是**工作区换行**，而 git 对它是"盲"的。

## 0. 一句话

同一个 commit 在两个工作树里可以有**不同字节**，而且 `git status` 两边都报干净 ——
换行不参与 git 的"改动"判定。所以没有任何 git 命令会去纠正它，差异就那么留着。

## 1. 机制：三层，缺一层都不成立

1. **仓库里的 blob 永远是 LF。** 落盘时 clean filter 按 `.gitattributes` 把 CRLF 归一成
   LF 再哈希，所以 `git cat-file blob HEAD:<文件>` 里查不到一个 CR。
2. **检出时写什么字节，由属性与配置决定。** 该扩展名声明了 `eol=lf` → 写 LF；
   只有 `text=auto`（或 `text`）而 `core.autocrlf=true` → 写 CRLF。
   本机 `core.autocrlf` 来自 `file:C:/Program Files/Git/etc/gitconfig`（Git for Windows
   安装默认），不是仓库配置。
3. **换行不进 git 的"内容"比较。** 实测（`docs/159`，8900 字节，只有换行不同）：

   | 命令 | 结果 |
   |---|---|
   | `git diff --numstat -- 文件` | **空**（两个方向的字节数都一样，git 认为内容相同） |
   | `git diff` | 只有一句 warning：`CRLF will be replaced by LF the next time Git touches it` |
   | `git status --porcelain` | 有时 ` M` —— 那是索引 stat 缓存过期，不是内容差异 |
   | 但 `git ls-files --eol` | 明明白白 `i/lf w/crlf` |
   | `git add` 之后 | 回到"干净"，而工作区**仍是 CRLF** |

   三件事合起来：**"下次 git 碰它"不会发生**，所以 CRLF 就那么留着；而 `status` 报不报
   `M` 取决于 stat 缓存，跟内容无关。

于是"最终不一样"不是 git 的 bug，而是**字节从哪来**决定的：

| 字节来源 | 结果 |
|---|---|
| git 检出（当时属性里有 `eol=lf`） | LF |
| git 检出（当时属性里只有 `text=auto`） | 视 `core.autocrlf`，本机 = CRLF |
| 工具写：Python `Path.write_text(...)`（无 `newline=`） | Windows 上 **CRLF** |
| 工具写：`newline="\n"` / `safe_write_text` / ZCode 的 Write 工具 | LF |

还有一条最容易踩的：**`eol=lf` 不回溯。** 它只作用于 `git add` 与**将来**的检出，
不重写已经在盘上的文件。一份在 pin 之前检出、或被 Windows 工具写过的文件，
会一直保持 CRLF，而 `git add` 之后 git 还会说它干净。

## 2. 本机实测（2026-09-12，可直接复跑）

| 观察 | 命令 | 结果 |
|---|---|---|
| 属性与工作区字节并列 | `git ls-files --eol` | 开发树 @`12625a8`: 793 件 `i/lf w/lf`，**50 件 `i/lf w/crlf`** |
| pin 也没被遵守 | 同上 | `docs/155…md`、`docs/156…md`、`sdk/linux/l1/build.sh`、`tools/loment_status.py`、`lom/fuai.lom` 等 attr 列就是 `text eol=lf`，工作区仍是 CRLF |
| 生成器是 CRLF 源 | `loment/build/fuai_syscalls.lomt` | hub @`7938893`（`.lomt` 无 pin）里它是 `w/crlf`，attr 列 `text=auto` —— 由 Windows 上的 Python 写法写出 |
| 新检出守 pin | 干净克隆 @`12625a8` | 61/61 `.lomt` = `w/lf` |
| 显式 checkout 也守属性 | 干净克隆 @`7938893` | 46 件里 43 件 `w/lf`（`text=auto` 下 git 并不主动转 CRLF） |

结论：**差异全部来自"这些字节是谁写的"，与 commit 无关。**

## 3. 代价：CRLF 会伪装成逻辑红

Loment 有一批判据按**原始字节**读源码 —— 自举 lexer/parser/codegen 与参考实现逐字节对照、
驱动跑语料逐字节比 IR、格式化器/文档生成器与 Python 版逐字节对照。一条 CRLF 检出在这些
判据上必然报差异，看起来就是"逻辑回归"。

2026-09-12 的归因过程正是如此：先被当成"上游既有逻辑红"，加 pin、改回 LF 之后同一条判据
在同一个 commit 上转绿。**判断一个红是逻辑还是包装，先看 `git ls-files --eol`。**

## 4. 门禁与修法

```
python tools/loment_eol.py         # 判据: attr 声明 eol=lf 的文件, 工作区必须 LF
python tools/loment_eol.py --fix   # 就地归一
```

数据来源是 `git ls-files --eol --cached --others --exclude-standard`：**带上 `--others`**
是刻意的 —— 刚写出来、还没进索引的文件也要看，否则门禁对"新文件"是瞎的，等它进了索引
才发现 CRLF，那时红已经追不上"谁写的"了。

`--fix` 不会吞改动：已跟踪文件只在"工作区去掉 CR 之后与索引字节完全一致"时才写盘；
未跟踪的新文件按行尾直接归一。无 Python 环境下的等价手工（已跟踪文件）：

```
git rm --cached <文件> && git checkout HEAD -- <文件>
# 整目录: git rm -r --cached <目录> && git checkout HEAD -- <目录>
```

根因侧已经同步收口：写**仓库内工件**的生成器一律显式 `newline="\n"`
（`lomfmt`、`lomdoc`、`loment_syscalls`、`loment_release`；`loment_manual`/`loment_status`
改得更早），否则每次在 Windows 上重生成都会把 CRLF 重新种回来。

## 5. pin 覆盖到了哪些扩展名（以及为什么）

`.gitattributes` 里声明 `eol=lf` 的范围 = 门禁的判据范围。本轮从"只钉 L0/L1 源码"
扩到"所有被逐字节或逐行读的文本"：

| 扩展名 | 谁按字节/逐行读 |
|---|---|
| `*.lomt` `*.lom` | 自举 lexer/parser/codegen 与参考实现逐字节对照 |
| `*.ll` | IR 逐字节比较（含自举种子） |
| `*.sha256`、`loment/build/SHA256SUMS` | `sha256sum -c` —— CRLF 会让 `\r` 混进文件名，整份清单校验失败 |
| `*.lock` | Cargo 每次都判定"被改写"，造成无意义噪声 |
| `*.csv` | 规则书（`sdk/rulebook/fidelity.csv`）逐行读 |
| `*.fui` `*.fus` | FUI 构建期读源码 |
| `*.svg` `*.html` `*.js` | 设计源与前端产物 |
| `*.def` | 链接器脚本 |

## 6. 复核者可以做的一次对抗

把任一 pinned 文件改成 CRLF（内容不动），然后：

- `python tools/loment_eol.py` → **必须红**，且红里指名文件 + 两条命令的修法；
- `git status` → **必须仍然报干净**（这条才是"为什么两个工作树会不一样"的现场证据）。

反过来，`python tools/loment_eol.py --fix` 之后同一文件必须回到 `w/lf`，且 `git status`
不变 —— 归一不产生内容改动。
