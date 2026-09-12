# 164 · Loment 源码包（源码 + 编辑器工具，不含二进制）

> 生成 `tools/loment_src.py` · 产物 `loment/dist/loment-<ver>-src.zip` · 判据 `loment_src --check`（进门禁）

## 0. 与发行包的分工

| | `loment_dist.py`（docs/162） | `loment_src.py`（本文） |
|---|---|---|
| 给谁 | 想**直接跑**的人 | 想**读/改/自己构建**的人 |
| 里面 | 预编译工具链（ELF）+ setup.exe | 全部 `.lomt` 源码 + 种子 + 文档 + VS Code 扩展 |
| 得到后 | 装完就能 `loment run` | `sh loment/bootstrap.sh` 用 clang 自建工具链 |
| 需要 | WSL（Windows 上）+ clang | 只要 clang（不需要 Python、不需要装任何 Loment 二进制） |

## 1. 包里是什么

- `loment/` —— 语言本体：`selfhost/`（lexer/parser/checker/codegen/driver）、`tools/`（格式化器/文档/语言服务）、
  `lib/json.lomt`、`examples/`、`corpus.json`、`bootstrap.sh`、`loment.ld`、
  **自举种子** `loment/build/selfhost_driver.ll`；
- `editors/vscode/` —— 扩展源码（语法 + LSP 客户端 + 任务），外加**打好的** `loment-vscode.vsix`（可直接装）；
- `tools/lom*.py` `tools/potato*.py` `tools/loment*.py` `tools/mono_trace.py` `tools/vscode_ext.py` —— 参考实现与工具链（自举判据就是拿它逐字节对照）；
- `docs/` —— Loment 的规范（141/143/144）、冻结面（158）、自举（150/159）、手册（`docs/manual/`）、发行与签名（162/163）；
- `README-SRC.md`（生成）、`LICENSE`、`SHA256SUMS`（包内校验和）。

## 2. 构建方式：只从 **git 索引**取内容

`_tracked()` = `git ls-files` 选中路径，`_blobs()` = 一次 `git cat-file --batch :path` 读 blob。两个后果：

1. **自动排除构建产物** —— 工作区里 `loment/build/*.ll`、`*.potato.json` 等未跟踪文件根本进不来（曾把 17MB 的
   build 目录算进来，实际包只有 1.3MB）；
2. **包是提交的纯函数** —— 索引侧恒为 LF，内容与提交一致，不受工作区改动影响；配合固定时间戳（1980-01-01）
   与排序，**两次构建字节相同**（判据里有这条）。

## 3. 踩到的两个坑（都写进判据/注释）

- **按数字区间选文档会串门**：`docs/14*.md` 会把别的线的东西收进来（`docs/144-kernel-boundaries-and-risks.md`、
  `docs/145-parallel-development-foundation.md`、`docs/146-kernel-stack-arena.md` 都是内核线的）。改成
  **按名字选**（`docs/*loment*` + 显式列出 141/142/147），并且明确排除 `docs/153`（UIsport 线）。
- **`loment/` 目录 19MB 但已跟踪只有 2.6MB** —— 差的全是构建产物；这也是"必须走 git 索引"而不是
  "直接递归打包目录"的原因（用目录就会把别人的构建垃圾和本地探针一起发出去）。

## 4. 判据与复现

```bash
python tools/loment_src.py --list      # 会打进去哪些路径
python tools/loment_src.py --check     # 确定性 + 包内 SHA256SUMS 自洽 + 8 个关键文件齐 (无参数=门禁模式)
python tools/loment_src.py --emit      # 出 loment/dist/loment-<ver>-src.zip
```

`--check` 无参数即门禁模式，已进 `tools/ci.py` 的静态门禁；工件进 `loment/build/release-manifest.json`。
包内自带 `SHA256SUMS`，解包后 `sha256sum -c SHA256SUMS` 就能核。
