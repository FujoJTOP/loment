#!/usr/bin/env python3
# lomlib.py — Loment 库系统的机器房 (Alpha2.1, docs/168)
#
# 判据见 docs/168 §2。这一层做四件事:
#   1. 依赖图: 边**从源码里的 use 来**, 不用声明 (判据 1)
#   2. 实例身份: 递归哈希 id(P) = H(自身源码 ⊕ {边名 -> id(子)}) (判据 2)
#   3. 能力需求: 沿依赖闭包**推导**, 不是作者声明 (判据 3)
#   4. 物化: 把实例集合摊成一棵编译器能直接吃的树 —— 编译器因此不知道版本 (判据 4)
#
# 与 tools/lompkg.py 的分工: lompkg 是 M57 的旧模型 (pkg.json + 逐包哈希), 有
# loment/tools/lompkg.lomt 孪生与**逐字节 stdout 判据**; 这里不动它, 新模型另起一层。
# 旧模型的缺口 (docs/168 §4.1): pkg_hash 只哈希自身文件、不含解析后的边, 于是
# "同一份源码绑到不同依赖"会被错误合并; 而且去重按 manifest 路径做, 同名多版本共存不了。
#
# 用法:
#   python tools/lomlib.py tree        DIR            # 依赖树 + 每个库的实例数
#   python tools/lomlib.py id          DIR            # 实例身份
#   python tools/lomlib.py cap         DIR            # 能力需求闭包 (带来源)
#   python tools/lomlib.py check       DIR            # 冲突检查 (同名不同域 / 重名实例)
#   python tools/lomlib.py materialize DIR --out DIR  # 物化成可编译的构建树
# 退出码: 0 = OK / 1 = 依赖或冲突错误 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402  (复用前端读源码: 清单、边、能力声明 —— 不新增解析器)

ROOT = Path(__file__).resolve().parent.parent

#: 名字形式的 `use <名字>` 的落点。**项目本地优先**: 消费方自己目录下的 deps/,
#: 然后才是仓库内置的四个根 (与 lomentc.NAME_ROOTS 同源)。
LOCAL_DEPS = "deps"
NAME_ROOTS = lomentc.NAME_ROOTS

#: 物化时给模块名加的后缀长度 (身份前 N 位)。
ID8 = 8


class LibError(Exception):
    """依赖/冲突层面的错误 —— 是**给用户的诊断**, 不是内部故障。"""


# ---------------------------------------------------------------- 节点

@dataclass
class Node:
    """一个**包**(目录) 或一个**单文件模块**(内置根里的 .lomt)。"""

    dir: Path                      # 包目录 (文件节点: 文件所在目录)
    files: list[Path]              # 参与身份计算的源码文件 (相对 dir)
    entry: Path | None = None      # 文件节点: 就是它自己; 目录节点: None
    name: str = ""                 # 包名 (标签, 不参与身份)
    version: str = "0.0.0"         # 版本 (标签, 不参与身份)
    mods: list = field(default_factory=list)     # 已装载的 Module
    edges: dict[str, "Node"] = field(default_factory=dict)   # use 名字 -> 子节点
    caps: list = field(default_factory=list)     # 自己声明的 Capability

    ident: str = ""                # 身份 (递归哈希), resolve 后填


def _read_manifest(d: Path) -> tuple[str, str]:
    """`.lomp` 里的标签。**必须先验**: 语言只有整数常量, 所以标签写成函数。

        module pkg
        pub fn name() -> str { return "mathutil"; }
        pub fn version() -> str { return "0.1.0"; }

    2026-09-15 实测 `pub const NAME: str = "x";` 是**语法错** (`期望 number（整数）`)。
    没有 `.lomp` 就用目录名 + "0.0.0" —— 清单是可选的。
    """
    for cand in sorted(d.glob("*.lomp")):
        try:
            mod = lomentc.load(cand)
        except lomentc.LomError as e:
            raise LibError(f"{cand}: 清单解析失败: {e}") from e
        name, ver = d.name, "0.0.0"
        for f in mod.funcs:
            if not f.pub or f.ret != "str":
                continue
            lit = _str_return(f)
            if lit is None:
                continue
            if f.name == "name":
                name = lit
            elif f.name == "version":
                ver = lit
        return name, ver
    return d.name, "0.0.0"


def _str_return(f) -> str | None:
    """`fn f() -> str { return "字面量"; }` -> 字面量 (AST: Return.expr -> StrLit.value)。

    只认**字面量**返回: 清单是给人看的标签, 接受表达式会逼出"清单能算多复杂"这种问题。
    """
    for s in getattr(f, "body", None) or []:
        e = getattr(s, "expr", None)
        if e is not None and type(e).__name__ == "StrLit":
            return e.value
    return None


def own_files(d: Path) -> list[Path]:
    """包**自己的**源码文件 —— 递归, 但**排除 `deps/`**。

    vendored 的依赖不是本包的源码: 混进来会让本包凭空多出依赖边、并把这些副本算进自己的
    身份哈希。2026-09-15 实测踩到过 (app 因此凭空多出一条 `use mathutil`, 还落到了内置根)。
    """
    return sorted(p for p in d.rglob("*.lomt")
                  if LOCAL_DEPS not in p.relative_to(d).parts)


def load_node(d: Path, entry: Path | None = None) -> Node:
    """装载一个包目录 (或单文件节点)。**只读源码**, 不做依赖解析。"""
    if entry is not None:
        files = [entry]
    else:
        files = own_files(d)
    mods = []
    for f in files:
        try:
            mods.append(lomentc.load(f))
        except lomentc.LomError as e:
            raise LibError(f"{f}: 解析失败: {e}") from e
    n = Node(dir=d, files=files, entry=entry, mods=mods)
    if entry is None:
        n.name, n.version = _read_manifest(d)
    else:
        n.name, n.version = entry.stem, "0.0.0"
        if mods:
            n.name = mods[0].name or n.name
    for m in mods:
        n.caps.extend(m.caps)
    return n


# ---------------------------------------------------------------- 边

#: 名字形式的 use **不带分号**, 而且必须按行匹配 (re.M) —— 语言就是这么定的:
#: `loment/examples/demo.lomt` 第 13 行是 `use mathutil`。写成 `use x;` 会被解析期拒绝
#: (`顶层只允许 use/capability/fn，得到 ';'`)。
_USE_NAME = re.compile(r"^([ \t]*)use[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*$", re.M)


def edge_names(n: Node) -> list[str]:
    """这个名字节点依赖了哪些**名字** —— 直接问前端, 不扫文本。

    依赖**不需要声明**, 它就是源码里的 `use` (判据 1)。所以这里读的是 AST 字段
    `name_imports`, 与 `use "路径"` 是两回事 (后者是包内的文件引用, 不是依赖边)。
    """
    out: list[str] = []
    for m in n.mods:
        for nm in m.name_imports:
            if nm not in out:
                out.append(nm)
    return out


def resolve_edge(name: str, consumer: Node) -> Node:
    """`use <名字>` -> 节点。**项目本地 d.e.p.s 优先**, 再落到内置根。"""
    cand = consumer.dir / LOCAL_DEPS / name
    if (cand / "pkg.lomp").exists() or cand.is_dir():
        if not cand.is_dir():
            raise LibError(f"{consumer.name}: 依赖 {name} 不是目录: {cand}")
        f = cand / f"{name}.lomt"
        if not f.exists():
            raise LibError(
                f"{consumer.name}: 依赖 {name} 里没有同名模块 {name}.lomt —— "
                f"`use <名字>` 指的是**包内与包同名的那个模块**")
        return load_node(cand)
    for rel in NAME_ROOTS:
        f = ROOT / rel / f"{name}.lomt"
        if f.exists():
            return load_node(f.parent, entry=f)
    raise LibError(f"{consumer.name}: 依赖 {name} 找不到 —— "
                   f"在 {consumer.dir / LOCAL_DEPS} 和内置根 {', '.join(NAME_ROOTS)} 里都没有")


# ---------------------------------------------------------------- 解析 + 身份

def resolve(entry_dir: Path) -> dict[str, Node]:
    """解析整棵依赖图, 回填身份; 检测环。

    **环必须在这里查** (判据 5): 内容哈希替代不了它 —— A 里 `use B`、B 里 `use A`,
    两边源码哈希都算得出。
    """
    root = load_node(entry_dir)
    seen: dict[Path, Node] = {}
    stack: list[str] = []
    _ = root

    def walk(n: Node) -> Node:
        """后序: **先算子节点的身份, 再算自己** —— 身份是递归的, 顺序不能反。

        返回**缓存里的那个对象**: `resolve_edge` 每次会新建 Node, 若不复用, 同一个包被
        两条路径引到时就会得到两个对象, 其中一个的身份永远是空串。
        """
        key = n.entry or n.dir
        if key in seen:
            return seen[key]
        if n.name in stack:
            raise LibError("依赖环: " + " -> ".join(stack + [n.name]))
        stack.append(n.name)
        for en in edge_names(n):
            n.edges[en] = walk(resolve_edge(en, n))
        n.ident = _identity(n)
        stack.pop()
        seen[key] = n
        return n

    def _identity(n: Node) -> str:
        """判据 2: `id(P) = H(自身源码 ⊕ {边名 -> id(子)})`。

        **不能只看自身源码** —— 同一份源码在两个构建里绑到不同的依赖上, 语义不同,
        只看自己会把它们错误地合并成一份。
        """
        h = hashlib.sha256()
        h.update(n.name.encode("utf-8"))
        h.update(b"\0")
        base = n.dir
        for f in n.files:
            rel = f.relative_to(base).as_posix() if f.is_relative_to(base) else f.name
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
        for en in sorted(n.edges):
            h.update(en.encode("utf-8"))
            h.update(b"\0")
            h.update(n.edges[en].ident.encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    # 后序: 依赖在前, 每个**实例**只出现一次 (身份相同 = 同一个实例)。
    def postorder(n: Node, out: list[Node], ids: set[str]) -> None:
        if n.ident in ids:
            return
        for c in n.edges.values():
            postorder(c, out, ids)
        if n.ident not in ids:
            ids.add(n.ident)
            out.append(n)

    root = walk(root)
    order: list[Node] = []
    postorder(root, order, set())
    return {"root": root, "order": order}


# ---------------------------------------------------------------- 能力闭包

def capability_closure(g: dict) -> tuple[dict[str, list[str]], list[str]]:
    """判据 3: `requires(P) = 自身 guard 的域 ∪ ⋃ requires(子)`。

    返回 (闭包, 冲突). 闭包: 能力名 -> 来源链 (`a -> b -> c`), 便于回答"这段是谁要的"。
    冲突: 同名能力**域不同** —— 必须报, 不能静默取一个。
    """
    closure: dict[str, list[str]] = {}
    domain: dict[str, tuple] = {}
    conflicts: list[str] = []

    def walk(n: Node, chain: list[str], seen: set) -> None:
        me = chain + [n.name]
        for c in n.caps:
            here = (c.space, c.lo, c.hi)
            if c.name in domain and domain[c.name] != here:
                conflicts.append(
                    f"能力 {c.name} 在两处声明了**不同的域**: "
                    f"{domain[c.name][0]}[{domain[c.name][1]}..{domain[c.name][2]}] "
                    f"({closure[c.name][-1]}) vs {here[0]}[{here[1]}..{here[2]}] "
                    f"({' -> '.join(me)})")
            elif c.name not in domain:
                domain[c.name] = here
                closure[c.name] = [" -> ".join(me)]
        for en in sorted(n.edges):
            child = n.edges[en]
            key = child.ident
            if key in seen:
                continue
            seen.add(key)
            walk(child, me, seen)

    walk(g["root"], [], set())
    return closure, conflicts


def instance_counts(g: dict) -> dict[str, int]:
    """每个库名有几份实例 —— 多版本时 >1。**必须打出来**: 藏起来的多份才是问题。"""
    out: dict[str, int] = {}
    for n in g["order"]:
        out[n.name] = out.get(n.name, 0) + 1
    return out


def top_level(n: Node) -> dict[str, str]:
    """实例的顶层名字 -> 种类。发射符号是**平的**, 所以这张表就是冲突面。"""
    out: dict[str, str] = {}
    for m in n.mods:
        for f in m.funcs:
            out.setdefault(f.name, "函数")
        for s in m.structs:
            out.setdefault(s.name, "结构体")
        for e in m.enums:
            if not getattr(e, "from_prelude", False):
                out.setdefault(e.name, "枚举")
        for c in m.consts:
            out.setdefault(c.name, "常量")
    return out


def name_conflicts(g: dict) -> list[str]:
    """跨实例的**顶层重名** —— 编译器的发射符号是平的, 这条今天必然报 E13。

    所以多版本共存**不是物化能绕过的**: 同一库的两个版本必然导出同一批名字。
    这里把它**精确报出来**(哪个名字、来自哪两个实例), 而不是让它跑到编译器那里
    变成一句看不出所以然的 E13。真要共存, 得让编译器按实例给符号起名 (docs/168 §5)。
    """
    seen: dict[str, Node] = {}
    out: list[str] = []
    for n in g["order"]:
        for nm, kind in sorted(top_level(n).items()):
            if nm == "_start":
                # 入口**不该**共存, 所以别给它"按实例起名就能共存"那条忠告 (那是误导)。
                # 入口冲突由 cmd_check 里那条专用诊断负责。
                continue
            prev = seen.get(nm)
            if prev is None:
                seen[nm] = n
            elif prev.ident != n.ident:
                out.append(
                    f"顶层名 {kind} {nm} 在 {prev.name}[{prev.ident[:ID8]}] 与 "
                    f"{n.name}[{n.ident[:ID8]}] 里都有 —— 发射符号是平的, 编译器会报 E13。"
                    f"同名多实例要共存需按实例起名 (docs/168 §5)")
    return out


# ---------------------------------------------------------------- 物化

_MODULE_LINE = re.compile(r"^([ \t]*module[ \t]+)([A-Za-z_][A-Za-z0-9_]*)([ \t]*)$", re.M)


def materialize(g: dict, out_dir: Path) -> list[dict]:
    """把实例集合摊成一棵**编译器能直接吃**的树 (判据 4)。

    每个实例落到自己的目录, 模块名带身份后缀 (判据 4 要求编译器不知道版本 ——
    它只看到一堆互不同名的模块); 消费方的 `use <名字>` 被改写成指向**它绑定的那个实例**。

    只改**整行**的 `module X` / `use X` (正则按行锚定, 所以行内注释与字符串不受影响)。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # 每个节点一个落地目录: <name>__<id8>
    home: dict[str, Path] = {}
    for n in g["order"]:
        home[n.ident] = out_dir / f"{n.name}__{n.ident[:ID8]}"
    mapping: list[dict] = []
    for n in g["order"]:
        d = home[n.ident]
        d.mkdir(parents=True, exist_ok=True)
        for f in n.files:
            rel = f.relative_to(n.dir) if f.is_relative_to(n.dir) else Path(f.name)
            dst = d / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            txt = f.read_text(encoding="utf-8")
            txt = _MODULE_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}__{n.ident[:ID8]}",
                                   txt)
            txt = _rewrite_uses(txt, n, home)
            dst.write_text(txt, encoding="utf-8", newline="\n")   # 必须 LF: 自举链按原始字节读
            mapping.append({"from": str(f), "to": str(dst)})
    (out_dir / "materialize-map.json").write_text(
        json.dumps({"map": mapping}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return mapping


def _rewrite_uses(txt: str, n: Node, home: dict[str, Path]) -> str:
    """`use <名字>` -> `use "<相对路径>/<名字>__<id8>.lomt"` (同样不带分号)。"""

    def sub(m: re.Match) -> str:
        indent, name = m.group(1), m.group(2)
        child = n.edges.get(name)
        if child is None:
            return m.group(0)
        target = home[child.ident] / f"{name}.lomt"
        return f'{indent}use "{_rel(target, home[n.ident])}"'

    return _USE_NAME.sub(sub, txt)


def _rel(target: Path, base: Path) -> str:
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        import os
        return os.path.relpath(target, base).replace("\\", "/")


# ---------------------------------------------------------------- CLI

def _tree_lines(n: Node, prefix: str, seen: set[str], out: list[str]) -> None:
    for en in sorted(n.edges):
        c = n.edges[en]
        dup = c.ident in seen
        out.append(f"{prefix}{en} -> {c.name} {c.version} [{c.ident[:ID8]}]"
                   + ("  (同一实例, 已见)" if dup else ""))
        if not dup:
            seen.add(c.ident)
            _tree_lines(c, prefix + "  ", seen, out)


def cmd_tree(a) -> int:
    g = resolve(Path(a.dir))
    r = g["root"]
    print(f"{r.name} {r.version} [{r.ident[:ID8]}]")
    seen = {r.ident}
    lines: list[str] = []
    _tree_lines(r, "  ", seen, lines)
    for ln in lines:
        print(ln)
    counts = instance_counts(g)
    multi = {k: v for k, v in counts.items() if v > 1}
    print(f"[OK] {len(g['order'])} 个实例")
    if multi:
        # 多版本共存**不是错误**, 但必须让用户看见 (docs/168 §5)
        print("[NOTE] 同名多实例: "
              + ", ".join(f"{k} x{v}" for k, v in sorted(multi.items())))
    return 0


def cmd_id(a) -> int:
    g = resolve(Path(a.dir))
    print(f"{g['root'].name} {g['root'].ident}")
    return 0


def cmd_cap(a) -> int:
    g = resolve(Path(a.dir))
    closure, conflicts = capability_closure(g)
    if not closure and not conflicts:
        print("[OK] 无能力声明")
        return 0
    for name in sorted(closure):
        print(f"{name} <- {closure[name][0]}")
    for c in conflicts:
        print(f"[CONFLICT] {c}", file=sys.stderr)
    return 1 if conflicts else 0


def cmd_check(a) -> int:
    g = resolve(Path(a.dir))
    _, conflicts = capability_closure(g)
    conflicts += name_conflicts(g)
    # 入口冲突: 多个实例都带 _start 时只有入口程序的算数 (docs/168 §5)
    starts = [n.name for n in g["order"]
              if n is not g["root"] and any(f.name == "_start" for m in n.mods for f in m.funcs)]
    for c in conflicts:
        print(f"[CONFLICT] {c}", file=sys.stderr)
    for s in starts:
        print(f"[CONFLICT] 依赖 {s} 也定义了 _start —— 入口只能有一个", file=sys.stderr)
    n = len(g["order"])
    counts = instance_counts(g)
    multi = {k: v for k, v in counts.items() if v > 1}
    if conflicts or starts:
        return 1
    if multi:
        print("[NOTE] 同名多实例: " + ", ".join(f"{k} x{v}" for k, v in sorted(multi.items())))
    print(f"[OK] {n} 个实例, 无冲突")
    return 0


def cmd_materialize(a) -> int:
    g = resolve(Path(a.dir))
    # 有冲突就不物化: 物化出来的树编不过, 与其让错误在编译器那里变形, 不如在这里拦住。
    bad = name_conflicts(g)
    if bad:
        for c in bad:
            print(f"[CONFLICT] {c}", file=sys.stderr)
        return 1
    mp = materialize(g, Path(a.out))
    print(f"[OK] {len(g['order'])} 个实例 -> {a.out} ({len(mp)} 个文件)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomlib", description="Loment 库系统 (docs/168)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("tree", cmd_tree), ("id", cmd_id),
                     ("cap", cmd_cap), ("check", cmd_check)):
        s = sub.add_parser(name)
        s.add_argument("dir")
        s.set_defaults(fn=fn)
    m = sub.add_parser("materialize")
    m.add_argument("dir")
    m.add_argument("--out", required=True)
    m.set_defaults(fn=cmd_materialize)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except LibError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
