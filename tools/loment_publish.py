#!/usr/bin/env python3
# loment_publish.py — 把单仓里的 Loment / lompi 切出来，发到各自的私有库 (docs/171)
#
# 分工（用户 2026-09-15 定）：
#   * **开发在主仓**（FujoOS 单仓，`D:\Dev\FujoOS-FujoLang`），单仓里的内容**原样不动**；
#   * 两个新库是**发布口**，不是开发地 —— 别在那里直接提交（提交了会被下次发布盖掉）。
#   * 名字：`FujoJTOP/loment` 与 `FujoJTOP/lompi`，**私有**。
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
            "loment/", "lom/", "editors/",
            "docs/manual/",
            "docs/14*-loment-*.md", "docs/141-l0-lom-spec.md",
            "docs/15*-loment-*.md", "docs/16*-loment-*.md", "docs/17*-loment-*.md",
            "tools/lom*",
            ".claude/skills/loment/",
            "LICENSE",
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
        "readme": """# Loment

Loment is a systems programming language for writing software that runs without a
runtime. Its syntax is a strict subset of Rust, extended with **capability domains** —
a first-class way to state which part of a program may touch which resource.

The compiler emits native x86-64 executables (Linux ELF and Windows PE) directly.
Programs link against no runtime and no libc; building a program requires neither
Python nor a C compiler.

Project site: <https://fujojtop.github.io/FujoOSwebsite/loment/>

## Status

Current version: `0.1.4-pre1`.

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
        # lompi 暂时仍是私有的 (用户 2026-09-16 只说开源 Loment 本体) —— 要一起开源就改这里。
        "visibility": "private",
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


def cmd_check() -> int:
    bad = []
    for name, spec in REPOS.items():
        n, _ = _tree_hash(name)
        if n < spec["floor"]:
            bad.append(f"{name}: 只切出 {n} 个文件（下限 {spec['floor']}）—— 路径清单可能写漏了")
        for p in spec["paths"]:
            if _rel_ok(p) == 0:
                bad.append(f"{name}: {p} 一个文件都没匹配到")
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
