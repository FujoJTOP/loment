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
        "readme": """# Loment

FujoOS 自研的**底层语言**：Rust 的**严格子集**，只多加了**能力域**（`capability` / `guard`）
这一层语义。能直接编成 **x86-64 原生可执行文件**（Linux ELF / Windows PE）——
**没有运行时、没有 libc、不需要 Python**。

## 从哪开始

| 想干什么 | 去哪 |
|---|---|
| 写一个 Loment 程序 | `.claude/skills/loment/SKILL.md` —— 自足的指南（语法/内建/错误码/命令） |
| 看语言的完整样子 | `loment/examples/tour.lomt`（一个文件过完整门语言） |
| 看编译器怎么实现的 | `loment/selfhost/` —— **用 Loment 写的 Loment 编译器** |
| 装工具链 | `docs/162-loment-distribution.md` |
| 语言规范 | `docs/143-l1-loment-v0.md` · `docs/146-loment-capability-semantics.md` |

```
loment run hi.lomt        # 编译 + 链接 + 跑
loment help               # 全部命令
```

## 这个仓库是什么

**它是 FujoOS 单仓的发布口，不是开发地。** 开发在主仓里做，这里按路径切出来发布
（`tools/loment_publish.py`）。**别在这里直接提交** —— 下次发布会把它盖掉。

## 相邻的东西

- `lompi` —— Loment 库的包管理器，**独立命令**（不是 Loment 的子命令）：
  <https://github.com/FujoJTOP/lompi>
""",
    },
    "lompi": {
        "repo": f"{ORG}/lompi",
        "paths": ["lompi/", ".claude/skills/lompi/"],
        # 摊平: 让 `lompi.lomt` / `lpi_*.lomt` / `fixture/` 直接在根上
        "renames": [("lompi/", "")],
        "floor": 30,
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
                 "commit", "--quiet", "-m", f"README: {spec['repo']} 是发布口, 开发在主仓",
                 cwd=td)
            n = len(_git("ls-files", cwd=td).splitlines())
            print(f"[{name}] 切出 {n} 个文件；建库(如无)并推送")
            # 先看看库在不在（在就只推，不在才建）—— `gh repo create` 对已存在的库会报错。
            have = subprocess.run(["gh", "repo", "view", spec["repo"], "--json", "name"],
                                  capture_output=True, text=True, shell=False)
            if have.returncode != 0:
                r = subprocess.run(["gh", "repo", "create", spec["repo"], "--private",
                                    f"--description={name} (FujoOS 发布口, 开发在主仓)"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", shell=False)
                if r.returncode != 0:
                    print(((r.stdout or "") + (r.stderr or ""))[-500:])
                    return 1
                print(f"[{name}] 建库 {spec['repo']} (private)")
            # **推成 `main`**：`gh repo create --source --push` 会把**当前分支名**
            # （这里是 `Fujoos-FujoLang-DEV`）当成默认分支 —— 一个新库顶着单仓的开发分支名
            # 很怪（2026-09-15 第一次跑就撞上了）。空库的**第一个 ref** 会成为默认分支。
            _git("remote", "add", "origin", f"https://github.com/{spec['repo']}.git", cwd=td)
            _git("push", "--quiet", "--force", "-u", "origin", "HEAD:main", cwd=td)
            print(f"[{name}] 推到 main")
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
