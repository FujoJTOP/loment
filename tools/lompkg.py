#!/usr/bin/env python3
# lompkg.py — Loment 包管理器 (M57, docs/148)
#
# 判据: 依赖解析 (拓扑序 + 环检测) + 校验和 (锁定与校验)。
# 包 = 一个目录, 内含 pkg.json:
#   {"name": "mathutil", "version": "0.1.0",
#    "deps": {"base": {"path": "../base"}}}
# 校验和 = 对包内所有 *.lomt (按相对路径排序) 逐文件 sha256 再合并, 与锁文件比对。
#
# 用法:
#   python tools/lompkg.py resolve pkg.json [--lock pkg.lock] [--write]
#   python tools/lompkg.py verify pkg.json --lock pkg.lock
# 退出码: 0 = OK / 1 = 解析或校验失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def pkg_hash(d: Path) -> str:
    h = hashlib.sha256()
    files = sorted(p for p in d.rglob("*.lomt"))
    if not files:
        h.update(b"<empty>")
    for p in files:
        h.update(p.relative_to(d).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def load_manifest(p: Path) -> dict:
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str):
        raise ValueError(f"{p}: 缺 name")
    if not isinstance(doc.get("version"), str):
        raise ValueError(f"{p}: 缺 version")
    deps = doc.get("deps", {})
    if not isinstance(deps, dict):
        raise ValueError(f"{p}: deps 必须是对象")
    return doc


def resolve(manifest_path: Path) -> list[dict]:
    """深度优先拓扑序 (依赖在前); 检测缺失/环。"""
    out: list[dict] = []
    done: set[Path] = set()
    stack: list[Path] = []

    def visit(mp: Path) -> None:
        mp = mp.resolve()
        if mp in stack:
            chain = " -> ".join(p.parent.name for p in stack + [mp])
            raise ValueError(f"依赖环: {chain}")
        if mp in done:
            return
        stack.append(mp)
        doc = load_manifest(mp)
        for name, spec in (doc.get("deps") or {}).items():
            rel = spec.get("path") if isinstance(spec, dict) else None
            if not isinstance(rel, str):
                raise ValueError(f"{mp}: 依赖 {name} 缺 path")
            child = (mp.parent / rel).resolve()
            cman = child / "pkg.json" if child.is_dir() else child
            if not cman.exists():
                raise ValueError(f"{mp}: 依赖 {name} 不存在: {child}")
            visit(cman)
        stack.pop()
        done.add(mp)
        out.append({"name": doc["name"], "version": doc["version"],
                    "dir": str(mp.parent), "hash": pkg_hash(mp.parent)})

    visit(manifest_path)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="lompkg", description="Loment 包管理器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("manifest")
    r.add_argument("--lock", default="pkg.lock")
    r.add_argument("--write", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("manifest")
    v.add_argument("--lock", required=True)
    a = ap.parse_args(argv)

    try:
        pkgs = resolve(Path(a.manifest))
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1

    if a.cmd == "resolve":
        for i, p in enumerate(pkgs):
            print(f"  {i}. {p['name']} {p['version']} sha256:{p['hash'][:16]}")
        print(f"[OK] {len(pkgs)} 个包 (拓扑序)")
        if a.write:
            Path(a.lock).write_text(
                json.dumps({"lockfile": 1, "packages": pkgs}, ensure_ascii=False,
                           indent=2) + "\n", encoding="utf-8")
            print(f"[OK] 锁文件 -> {a.lock}")
        return 0

    lock = json.loads(Path(a.lock).read_text(encoding="utf-8"))
    want = {p["name"]: p["hash"] for p in lock.get("packages", [])}
    bad = 0
    for p in pkgs:
        w = want.get(p["name"])
        if w is None:
            bad += 1
            print(f"[MISS] {p['name']}: 不在锁文件")
        elif w != p["hash"]:
            bad += 1
            print(f"[DIFF] {p['name']}: 校验和不符 (锁 {w[:16]} vs 实际 {p['hash'][:16]})")
        else:
            print(f"[OK] {p['name']} {p['version']}")
    print(f"lompkg: {len(pkgs) - bad}/{len(pkgs)} 校验通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
