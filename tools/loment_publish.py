#!/usr/bin/env python3
# loment_publish.py — 把单仓里的 Loment / lompi 切出来，发到各自的私有库 (docs/171)
#
# 分工（用户 2026-09-15 定，2026-09-17 修订）：
#   * **开发在主仓**（FujoOS 单仓，`D:\Dev\FujoOS-FujoLang`），单仓里的内容**原样不动**；
#   * 两个库是**发布口**，而**每次开发提交之后都要 `--push` 一次**把主仓当时的状态发过去
#     —— 用户 2026-09-17："以后提交到这，开发提交也在这"。所以**开发提交也会落在那两个库里**，
#     只是**由这里发**，不是在那边写的。
#   * **发布是整体覆盖**（force-push 成 `main`）：**别在那边直接提交**，一定被下次发布盖掉。
#     那边的 `README.md` 与 `.gitattributes` 也是本工具直出的（见 §cmd_push），不是手写的。
#   * 名字与可见性：`FujoJTOP/loment` 与 `FujoJTOP/lompi`，**两个都公开**
#     （Loment 是用户 2026-09-16 定的；lompi 是 2026-09-17 定的 —— 见各自 spec 里的注解）。
#     可见性写进 spec 而不是手敲 `gh repo edit`，因为手敲的只生效一次，换台机器就丢。
#
#   python tools/loment_publish.py --status          # 切出来会是什么样（路径数 + 树哈希）
#   python tools/loment_publish.py --check           # 门禁: 清单自洽（路径都存在、有条目）
#   python tools/loment_publish.py --push loment     # 克隆 -> 过滤 -> 建库(如无) -> 推
#   python tools/loment_publish.py --push all
# 退出码: 0 = 好 / 1 = 清单有问题 / 2 = 用法错
#
# **为什么路径不重排**：Loment 那边**到处是按路径引用的**（`loment/tools/lomcli.lomt`、
# `lom/*.lom`、`docs/169`…），路径一改，文档与注释全失效。所以 Loment 保留原结构。
# lompi 那边是自足的小项目，把 `lompi/` **摊平到根**更像一个正经仓库（`lompi.lomt` 就在根上）。
#
# **历史**：用 `git filter-repo` 保留（在**克隆副本**里跑 —— 它在原仓是就地重写，绝不能对
# 工作仓下手）。切出来的每个库都带自己那段历史。

from __future__ import annotations

import argparse
import fnmatch
import re
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORG = "FujoJTOP"

#: 每个库：仓库名、切哪些路径（支持 glob）、重排规则、根 README。
REPOS: dict[str, dict] = {
    "loment": {
        "repo": f"{ORG}/loment",
        "paths": [
            # 目录（整棵）。**`lompi/` 在这儿**：Loment 开发树离不开它
            # （`loment_dist.py:67` 拿它构建随包发行的 lompi 工具，`loment_lompi_test.py`
            # 读它的源）—— 所以 lompi 的可见性必须与这个库一致。用户 2026-09-17 定：
            # **lompi 也开源**，于是两边都是 public（原先"只开源 Loment 本体"的划分作废）。
            "loment/", "lom/", "lompi/", "editors/",
            "docs/manual/",
            ".claude/skills/loment/", ".claude/skills/lompi/",
            # 工具：`tools/lom*` 一次收全（lomelf / lomentc / lomc / lomdoc / lom_audit …）
            "tools/lom*",
            # 文档：主体走 glob，**但 glob 会静默漏掉名字里没有 "loment" 的那些** ——
            # 下面这几条 explicit 的就是补漏。2026-09-17 审计出 5 篇（110/142/147/176/177），
            # 其中 142/147 还被 `docs/175` 正文引用着：漏了它们，开发口里就有断链。
            "docs/14*-loment-*.md", "docs/141-l0-lom-spec.md",
            "docs/15*-loment-*.md", "docs/16*-loment-*.md", "docs/17*-loment-*.md",
            "docs/110-w36-potato-lang.md",
            "docs/142-potato-v0.md",
            "docs/147-potato-v1-spec.md",
            "docs/176-frozen-surface-cost.md",
            "docs/177-ffi-complete.md",
            # 仓库约定与许可：**开发口缺了它们就没法按本仓的规矩工作**
            # （`CLAUDE.md` 是工作区级指令；`.gitignore` 不管住的话 `selfhost_driver.ll`
            #   那种大件与构建产物会跟着进来）。
            # **`.gitattributes` 必须在这里** —— 工具会往它**追加**一段（不再覆盖，见
            # `cmd_push`），前提是它已经跟着筛出来；不在清单里的话追加的是空文件，
            # `*.lomt text eol=lf` 那条规矩照样丢。2026-09-17 第一次修漏写这一条，
            # 远端那份仍只有工具自己那段，是查"克隆出来还是 CRLF"才发现的。
            "CLAUDE.md", "AGENTS.md", ".gitignore", ".gitattributes", "LICENSE",
            # **`scripts/` 里只有这两个是 Loment 线的** —— 别整目录收（其余是 FujoOS 的
            # 内核/ISO 构建脚本）。这两个被工具链当成**必需的启动脚本**引用：
            # `loment_seed.py:72` 的 LAUNCH_SCRIPTS 点名 `scripts/lomc.ps1`（缺了它
            # "装工具不需要 Python"这条会静默退化），`loment_release.py:91` 与
            # `loment_src.py:49` 两个都点名。2026-09-17 把整个 `scripts/` 当 FujoOS 排除
            # 是**错的**，是 `loment_seed_test` 1/4 红查出来的。
            "scripts/lomc.ps1", "scripts/install-lsp.ps1",
            # **工具集按"门禁跑得起来"算, 不按名字 glob。** `tools/lom*` 那个 glob 实测
            # 只捞到 **57/127** —— 静默漏掉 `ci.py`（门禁本身!）与整个 Potato 工具集,
            # 而 `lomentc.py` 会 `import potato`, 于是开发口里**连编译器都跑不起来**。
            # 2026-09-17 搬开发口时撞出来的; `--check` 里有一条闭包判据钉住它。
            "tools/ci.py", "tools/_safepath.py", "tools/mono_trace.py",
            "tools/potato.py", "tools/potato_assert.py", "tools/potato_cross.py",
            "tools/potato_from.py", "tools/potato_llm_arm.py", "tools/potato_measure.py",
            "tools/potato_test.py", "tools/vscode_ext.py", "tools/vscode_ext_test.py",
            "tools/fuai_contract_check.py",
        ],
        "renames": [],
        "floor": 250,
        #: 仓库的对外元数据。**放在这里而不是手敲 `gh repo edit`** —— 手敲的只生效一次,
        #: 下次建库/换机器就丢了; 写进 spec 则每次 `--push` 都会对齐 (值一样时是无操作)。
        #: 用户 2026-09-16 定: **Loment 公开**, 且**不打 tag** (tag 要等真发布)。
        "visibility": "public",
        "description": "Loment — a systems programming language with a Rust-subset syntax "
                       "and capability domains; compiles to native binaries with no runtime",
        "homepage": "https://fujojtop.github.io/FujoOSwebsite/loment/",
        "topics": ["programming-language", "compiler", "rust", "self-hosted",
                   "systems-programming", "capability-security", "no-std"],
        #: 写进仓库根的 `.gitattributes`（与 README 同一种做法：发布工具直出，不靠手敲）。
        #: 两件事：**把 Loment 源码认到某个语言名下**，以及**把生成物排除出统计**。
        #: 详见文件里的注释 —— 尤其是"为什么现在写 Rust 而不是 Loment"。
        "gitattributes": """# GitHub classifies a repository with Linguist. This repository has two kinds of bytes
# that Linguist would otherwise get wrong:
#
#   * `.lomt` / `.lom` are Loment source. Linguist does not list those extensions yet, and
#     it refuses to guess: `Language.find_by_alias` returns nil for a name it does not know
#     and caches that, so `linguist-language=Loment` would not "declare it in advance" — it
#     would drop these files out of the statistics entirely. They are attributed to Rust
#     instead: Loment's syntax is a strict subset of Rust, so keywords, strings, comments
#     and the resulting highlighting line up.
#     Loment is not yet eligible for inclusion — Linguist asks for roughly 2000 files per
#     extension per year, spread across repositories, and does not accept very new
#     languages. **Once it is included, change these two lines to `Loment`** — or drop them
#     and let the extensions be detected on their own.
#   * `loment/build/` holds build output — the compiler seed as LLVM IR, per-target Potato
#     form objects, transpiled Rust and C, the genesis assembler. Excluding it keeps a
#     1.8 MB generated file from deciding that this repository is "LLVM".
*.lomt linguist-language=Rust
*.lom  linguist-language=Rust
loment/build/** linguist-generated
""",
        "readme": """# Loment

Loment is a systems programming language for writing software that runs without a
runtime. Its syntax is a strict subset of Rust, extended with **capability domains** —
a first-class way to state which part of a program may touch which resource.

The compiler emits native x86-64 executables (Linux ELF and Windows PE) directly.
Programs link against no runtime and no libc; building a program requires neither
Python nor a C compiler.

Project site: <https://fujojtop.github.io/FujoOSwebsite/loment/>

## Status

Current version: `0.1.4-pre2`.

The language surface is frozen — syntax, type rules, diagnostics, unit loading and the
capability semantics are documented in `docs/158-loment-freeze.md`, and any change to
them has to go through the process described there. The implementation is not frozen.

Prebuilt toolchains are not published yet; see [Getting the toolchain](#getting-the-toolchain).

## Getting the toolchain

### Prebuilt packages

Linux and Windows packages will be published in this repository's
[Releases](https://github.com/FujoJTOP/loment/releases). They are not available yet.

### Building from source

The compiler rebuilds itself from sources kept in this repository. No Python, no
interpreter and no C compiler are required — the repository ships an assembler that
serves as the starting point of the build chain:

```
$ sh loment/bootstrap.sh
...
SEED BOOTSTRAP OK
```

The script rebuilds the compiler from the committed seed and verifies that the rebuilt
result is byte-identical to the seed, then checks that the second and third stages reach
a fixed point. Given an entry file, it also prints the compiler output for that file:

```
$ sh loment/bootstrap.sh hi.lomt > hi.ll
```

## A first program

```rust
module hello

fn _start() {
    let s: str = "hello from Loment\\n";
    syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64);
    syscall4(60, 0, 0, 0);
}
```

```
$ loment run hello.lomt
hello from Loment
```

The entry point is `_start`; there is no `printf`, and output goes through a system call.
The output is a single self-contained executable.

## Documentation

| Document | Contents |
|---|---|
| `.claude/skills/loment/SKILL.md` | Language guide: syntax, built-in functions, error codes, commands. Self-contained; the fastest way in for both people and coding agents. |
| `loment/examples/tour.lomt` | The whole language in one file, with commentary. |
| `docs/143-l1-loment-v0.md` | Language specification. |
| `docs/146-loment-capability-semantics.md` | Capability domains. |
| `docs/` | Design and measurement records, numbered by document. |

The command line has 38 commands; `loment help` lists them, `loment cheat` is a one-page
summary. Most design documents under `docs/` are written in Chinese.

## Repository layout

| Path | Contents |
|---|---|
| `loment/selfhost/` | The compiler. It is written in Loment. |
| `loment/tools/` | Command-line front end, formatter, documentation generator, language server, linker. |
| `loment/examples/` | Example programs. |
| `lom/` | Interface layer: one declaration source that generates constants and decoders for other languages. |
| `editors/` | Editor support: syntax highlighting, completion and navigation for VS Code and Vim. |
| `tools/` | Build, packaging and verification tools. |
| `docs/` | Design and measurement records. |

## Contributing

Development takes place in a private monorepo, and this repository is generated from a
subset of it — commits made here are overwritten by the next publication. Bug reports and
questions are welcome as issues; patches should be discussed there first.

## License

MIT. See [LICENSE](LICENSE).
""",
    },
    "lompi": {
        "repo": f"{ORG}/lompi",
        "paths": ["lompi/", ".claude/skills/lompi/"],
        # 摊平: 让 `lompi.lomt` / `lpi_*.lomt` / `fixture/` 直接在根上
        "renames": [("lompi/", "")],
        "floor": 30,
        # **公开** (用户 2026-09-17 定)。原先 2026-09-16 是私有的, 理由是"用户只说了开源
        # Loment 本体"; 但 Loment 开发树离不开 `lompi/` (`loment_dist.py:67` 拿它构建随包
        # 发行的 lompi 工具), 而 `FujoJTOP/loment` 是开发口且公开 —— 两边可见性必须一致,
        # 否则要么公开库缺件、要么开发口得转私有。用户选了"lompi 也开源"。
        "visibility": "public",
        "description": "lompi — Loment 的包管理器（库是一个目录，身份是内容哈希）",
        "homepage": "https://fujojtop.github.io/FujoOSwebsite/loment/lompi/",
        "topics": ["package-manager", "loment"],
        "readme": """# lompi

**Loment 库的包管理器** —— 库是一个目录，身份是**内容哈希**，依赖就是源码里的 `use`。
用 **Loment 自己写**的，由 Loment 工具链编出来。

**它是独立命令，不是 Loment 的子命令**：`loment help` 里没有它，直接敲 `lompi`。

## 建它

需要先有 Loment 工具链（<https://github.com/FujoJTOP/loment>），然后**在本目录里**：

```
loment build lompi.lomt -o lompi
```

（必须在**本目录**下调用：本工具链按 **CWD** 解析 `use "lpi_cli.lomt"` 这种相对导入。）

```
lompi version
lompi index   <store>
lompi show    <store> <name[@version]>
lompi tree    <store> <name[@version]>
lompi resolve <store> <name[@version]>          # 打锁（按哈希钉住）
lompi check   <dir>                             # 校验一个库目录
```

自检驱动 `lpi_test.lomt`：每个模块一个 `selftest_<模块>()`，汇总后**退出码即结论**。

## 这个仓库是什么

**它是 FujoOS 单仓的发布口，不是开发地。** 开发在主仓里做，这里切出来发布
（主仓的 `tools/loment_publish.py`）。**别在这里直接提交** —— 下次发布会把它盖掉。

指南：`.claude/skills/lompi/SKILL.md`
""",
    },
}


def _git(*a: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *a], cwd=str(cwd or ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", shell=False)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(a)} 失败: {r.stderr[-300:]}")
    return r.stdout


def _all_rows() -> list[str]:
    """整棵 HEAD 的 `ls-tree -r` 输出（`<mode> <type> <sha>\\t<path>`）。

    **不能用 `git ls-tree <路径>` 逐个取** —— `ls-tree` **不展开 glob**（`ls-files` 才展开），
    于是 `tools/lom*` 这种模式一个文件都匹配不到（2026-09-15 踩过：文件数凭空少了 81）。
    取全表，再在 Python 里按模式筛。
    """
    return [x for x in _git("ls-tree", "-r", "HEAD").splitlines() if x.strip()]


def _hit(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        elif fnmatch.fnmatchcase(path, p):
            return True
    return False


def _selected(name: str) -> list[str]:
    spec = REPOS[name]
    return sorted(r for r in _all_rows() if _hit(r.split("\t", 1)[1], spec["paths"]))


def _rel_ok(p: str) -> int:
    """这个模式匹配到几个**已跟踪**文件。"""
    return len([r for r in _all_rows() if _hit(r.split("\t", 1)[1], [p])])


def _tree_hash(name: str) -> tuple[int, str]:
    """(文件数, 内容树哈希)。哈希按 `ls-tree -r` 的确定性输出算 —— "要发的东西变了没"。"""
    rows = _selected(name)
    h = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:12]
    return len(rows), h


def cmd_status() -> int:
    for name, spec in REPOS.items():
        n, h = _tree_hash(name)
        print(f"{name:8s} -> {spec['repo']}")
        print(f"   文件 {n} 个 ({'/'.join(spec['paths'][:3])} …)   树哈希 {h}")
        for p in spec["paths"]:
            c = _rel_ok(p)
            if c == 0:
                print(f"   [空] {p}")
    return 0


def _gate_missing(spec: dict) -> list[str]:
    """`ci.py --static-only` 跑得起来所需的 `tools/*.py`，减去路径清单已经覆盖的。

    **为什么必须有这条判据**：清单原先是靠名字 glob（`tools/lom*`）定的，实测只捞到
    **57/127** —— 静默漏掉 `ci.py`（门禁本身）与整个 Potato 工具集，而 `lomentc.py`
    会 `import potato`，于是开发口里**连编译器都跑不起来**（`python tools/lomentc.py`
    在 `--emit-potato` 那条路上直接 ImportError）。

    **靠手数清单是数不对的** —— 那个漏是在搬到开发口、真去跑门禁时才撞出来的。
    所以把"门禁闭包"写成判据：门禁要跑的东西必须都在。
    """
    tools = ROOT / "tools"
    allpy = {p.stem for p in tools.glob("*.py")}

    def covered(stem: str) -> bool:
        f = f"tools/{stem}.py"
        return any(fnmatch.fnmatch(f, g) for g in spec["paths"])

    have = {s for s in allpy if covered(s)}
    need = set(have)
    ci = tools / "ci.py"
    if ci.exists():
        head = ci.read_text(encoding="utf-8").split("STATIC_CHECKS")[1][:3000]
        for m in re.findall(r'"([a-z0-9_]+)"', head):
            if m in allpy:
                need.add(m)
    changed = True
    while changed:                      # import 闭包
        changed = False
        for n in sorted(need):
            src = (tools / f"{n}.py").read_text(encoding="utf-8", errors="replace")
            for m in re.findall(r'^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)', src, re.M):
                if m in allpy and m not in need:
                    need.add(m)
                    changed = True
    return sorted(need - have)


def cmd_check() -> int:
    bad = []
    for name, spec in REPOS.items():
        n, _ = _tree_hash(name)
        if n < spec["floor"]:
            bad.append(f"{name}: 只切出 {n} 个文件（下限 {spec['floor']}）—— 路径清单可能写漏了")
        for p in spec["paths"]:
            if _rel_ok(p) == 0:
                bad.append(f"{name}: {p} 一个文件都没匹配到")
        if name == "loment":
            miss = _gate_missing(spec)
            if miss:
                bad.append(f"{name}: 门禁闭包还缺 {len(miss)} 个 —— "
                           f"{' '.join('tools/' + m + '.py' for m in miss[:5])}"
                           f"{' …' if len(miss) > 5 else ''}（加进 paths，别靠手数）")
    for b in bad:
        print(f"[ERR] {b}")
    if bad:
        return 1
    print("loment_publish: 两个库的路径清单自洽")
    return 0


def _edit_meta(spec: dict) -> int:
    """把仓库的对外元数据对齐到 spec：**可见性 / 简介 / 官网 / 主题**。

    为什么要它: 这些值原先靠手敲 `gh repo edit`, 只生效一次 —— 换机器、重建库、
    或者谁手动改回私有, 就悄悄漂了, 而 `--check` 看不见(它只看路径清单)。
    写进 spec 之后, **每次 `--push` 都对齐一次**, 值本来就对时是空操作。

    **可见性是唯一有"外向"后果的那一项**: 从私有变公开会在 GitHub 上留痕且基本不可逆,
    所以变了要**打出来**, 不安静地做。
    """
    repo = spec["repo"]
    r = subprocess.run(["gh", "repo", "view", repo, "--json",
                        "visibility,description,homepageUrl,repositoryTopics"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False)
    if r.returncode != 0:
        print(f"[{repo}] 读不到元数据: {r.stderr[-200:]}")
        return 1
    cur = json.loads(r.stdout)
    args, notes = [], []
    if cur.get("visibility", "").lower() != spec["visibility"]:
        # `gh` 在这里有一道刻意的闸: 改可见性必须**同时**给 `--accept-visibility-change-consequences`,
        # 不然直接拒绝 (实测: "use of --visibility flag requires ...")。别把它当成要绕的障碍 ——
        # 它就是"这一步有外向后果"的那句话, 我们的 spec 已经把它写明了, 照给。
        args += ["--visibility", spec["visibility"], "--accept-visibility-change-consequences"]
        notes.append(f"可见性 {cur.get('visibility','?')} -> {spec['visibility']}")
    if (cur.get("description") or "") != spec["description"]:
        args += ["--description", spec["description"]]
        notes.append("简介")
    if (cur.get("homepageUrl") or "") != spec["homepage"]:
        args += ["--homepage", spec["homepage"]]
        notes.append("官网地址")
    have = {t["name"] for t in (cur.get("repositoryTopics") or [])}
    add = [t for t in spec["topics"] if t not in have]
    if add:
        args += ["--add-topic", ",".join(add)]
        notes.append("主题 +" + ",".join(add))
    if not args:
        print(f"[{repo}] 元数据已对齐")
        return 0
    e = subprocess.run(["gh", "repo", "edit", repo, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False)
    if e.returncode != 0:
        print(f"[{repo}] 改元数据失败: {((e.stdout or '') + (e.stderr or ''))[-300:]}")
        return 1
    print(f"[{repo}] 元数据: " + "; ".join(notes))
    return 0


def cmd_push(which: str) -> int:
    names = list(REPOS) if which == "all" else [which]
    for name in names:
        spec = REPOS[name]
        td = Path(tempfile.mkdtemp(prefix=f"pub-{name}-"))
        try:
            print(f"[{name}] 克隆到临时目录（filter-repo 会就地重写，绝不在工作仓里跑）")
            _git("clone", "--no-local", "--quiet", str(ROOT), str(td))
            args = ["filter-repo", "--force"]
            for p in spec["paths"]:
                # `--path` **只吃字面路径**；带通配的必须走 `--path-glob`（2026-09-15 踩过：
                # 用 --path 传 `tools/lom*` 一个文件都不匹配，loment 那侧凭空少了 81 个文件）。
                args += ["--path-glob", p] if ("*" in p or "?" in p) else ["--path", p]
            for a, b in spec["renames"]:
                args += ["--path-rename", f"{a}:{b}"]
            g = subprocess.run(["git", *args], cwd=str(td), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", shell=False)
            if g.returncode != 0:
                print(g.stderr[-500:])
                return 1
            (td / "README.md").write_text(spec["readme"], encoding="utf-8", newline="\n")
            _git("add", "README.md", cwd=td)
            if spec.get("gitattributes"):
                # **追加，不覆盖**（2026-09-17 修）。这个文件**不是发布工具的私产**：它带着
                # `*.lomt text eol=lf` 那条规矩，而自举的逐字节判据依赖"检出换行不漂"
                # （仓库里那段注释写着为什么：同一 commit 在两台机器上文件字节不同，
                # 就会冒出"只在某个 worktree 才红"的假回归）。
                #
                # 原实现无条件覆盖，于是 `loment` 克隆出来时 `codegen.lomt` 是 **CRLF**,
                # `loment_p8_test` 的 AST 逐字节判据当场红（Loment 侧看到 `\n\n`、Python 侧
                # 看到 `\n`）。以前 `loment` 只是"没人从它构建"的发布镜像所以没暴露；
                # 2026-09-17 它成了**开发口**，就直接踩上了。
                # **前插, 不是追加。** `.gitattributes` 是**后者覆盖前者**, 而工具这段是
                # **通用**规则 (`loment/build/** linguist-generated`), 仓库那份是**具体**
                # 规则 (`loment/build/selfhost_driver.ll … linguist-generated=true -diff`)。
                # 追加的话通用规则会盖掉具体的 —— 今天两者一致所以看不出, 明天不一致就是
                # 静默改变语义。惯例也是"通用在前、具体在后"。
                ga = td / ".gitattributes"
                cur = ga.read_text(encoding="utf-8") if ga.exists() else ""
                if spec["gitattributes"].strip() not in cur:
                    ga.write_text((spec["gitattributes"].rstrip("\n") + "\n\n" + cur).lstrip("\n"),
                                  encoding="utf-8", newline="\n")
                _git("add", ".gitattributes", cwd=td)
            _git("-c", "user.name=loment_publish", "-c", "user.email=noreply@fujo.invalid",
                 "commit", "--quiet", "-m", "README — 说清这是什么、怎么开始",
                 cwd=td)
            n = len(_git("ls-files", cwd=td).splitlines())
            print(f"[{name}] 切出 {n} 个文件；建库(如无)并推送")
            # 先看看库在不在（在就只推，不在才建）—— `gh repo create` 对已存在的库会报错。
            have = subprocess.run(["gh", "repo", "view", spec["repo"], "--json", "name"],
                                  capture_output=True, text=True, shell=False)
            if have.returncode != 0:
                r = subprocess.run(["gh", "repo", "create", spec["repo"],
                                    f"--{spec['visibility']}",
                                    f"--description={spec['description']}"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", shell=False)
                if r.returncode != 0:
                    print(((r.stdout or "") + (r.stderr or ""))[-500:])
                    return 1
                print(f"[{name}] 建库 {spec['repo']} ({spec['visibility']})")
            # **推成 `main`**：`gh repo create --source --push` 会把**当前分支名**
            # （这里是 `Fujoos-FujoLang-DEV`）当成默认分支 —— 一个新库顶着单仓的开发分支名
            # 很怪（2026-09-15 第一次跑就撞上了）。空库的**第一个 ref** 会成为默认分支。
            _git("remote", "add", "origin", f"https://github.com/{spec['repo']}.git", cwd=td)
            _git("push", "--quiet", "--force", "-u", "origin", "HEAD:main", cwd=td)
            print(f"[{name}] 推到 main")
            if _edit_meta(spec) != 0:
                return 1
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_publish")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--push", metavar="NAME", choices=[*REPOS, "all"])
    a = ap.parse_args(argv)
    if a.push:
        return cmd_push(a.push)
    if a.status:
        return cmd_status()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
