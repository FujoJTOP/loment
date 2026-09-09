#!/usr/bin/env python3
# loment_build.py — 增量构建 + 构建缓存 (M65/M66, docs/148)
#
# 判据:
#   M65 增量编译: 改动单文件只重编该文件及其下游, 其余命中缓存;
#   M66 构建缓存: 冷/热构建时间对照 (内容哈希命中率)。
# 缓存键 = sha256(源文件 + 递归 use 的 .lomt 内容); 键不变则跳过生成。
#
# 用法:
#   python tools/loment_build.py DIR [--out OUT] [--report] [--clean]
# 退出码: 0 = 全部成功 / 1 = 有文件编译失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
USE_RE = re.compile(r'use\s+"([^"]+)"')


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p.resolve())


def dep_files(p: Path, root: Path, seen: set[Path]) -> list[Path]:
    """递归收集 use "*.lomt" 依赖 (只跟 .lomt, 不跟 .lom 布局)。"""
    p = p.resolve()
    if p in seen or not p.exists():
        return []
    seen.add(p)
    out = [p]
    for m in USE_RE.finditer(p.read_text(encoding="utf-8")):
        rel = m.group(1)
        if not rel.endswith(".lomt"):
            continue
        for cand in ((root / rel), (p.parent / rel)):
            if cand.exists():
                out += dep_files(cand, root, seen)
                break
    return out


def cache_key(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def build_one(p: Path, out: Path, root: Path) -> dict:
    key = cache_key(dep_files(p, root, set()))
    rec = {"file": _rel(p), "key": key, "artifacts": [], "ok": True,
           "error": ""}
    try:
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, root, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        if errs:
            rec["ok"] = False
            rec["error"] = errs[0]
            return rec
        rust = lomentc.emit_rust(mod, root, deps)
        potato = lomentc.emit_potato(mod, root, deps)
        (out / f"{p.stem}.rs").write_text(rust, encoding="utf-8")
        (out / f"{p.stem}.potato.json").write_text(potato, encoding="utf-8")
        rec["artifacts"] = [f"{p.stem}.rs", f"{p.stem}.potato.json"]
        try:
            ll = lomentc.emit_llvm(mod, root, deps)
            (out / f"{p.stem}.ll").write_text(ll, encoding="utf-8")
            rec["artifacts"].append(f"{p.stem}.ll")
        except lomentc.LomError as e:  # 原生后端不支持的部分: 记录但不失败
            rec["error"] = f"IR: {e}"
    except Exception as e:  # noqa: BLE001
        rec["ok"] = False
        rec["error"] = str(e)
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_build", description="增量构建 + 缓存")
    ap.add_argument("dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    src_dir = Path(a.dir).resolve()
    out = Path(a.out).resolve() if a.out else src_dir / "build"
    if not src_dir.is_dir():
        print(f"[ERR] 目录不存在: {src_dir}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / ".loment-cache.json"
    old: dict[str, dict] = {}
    if index_path.exists() and not a.clean:
        try:
            old = {r["file"]: r for r in json.loads(
                index_path.read_text(encoding="utf-8")).get("records", [])}
        except (OSError, json.JSONDecodeError):
            old = {}

    files = sorted(p for p in src_dir.glob("*.lomt"))
    t0 = time.perf_counter()
    records, hit, miss, bad = [], 0, 0, 0
    for p in files:
        rel = _rel(p)
        prev = old.get(rel)
        key = cache_key(dep_files(p, ROOT, set()))
        if prev and prev.get("key") == key and all(
                (out / x).exists() for x in prev.get("artifacts", [])):
            hit += 1
            records.append(prev)
            continue
        miss += 1
        rec = build_one(p, out, ROOT)
        rec["file"] = rel
        records.append(rec)
        if not rec["ok"]:
            bad += 1
    dt = time.perf_counter() - t0
    index_path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")
    print(f"[{'COLD' if hit == 0 else 'HOT '}] {len(files)} 个单元: "
          f"{hit} 命中 / {miss} 重编 / {bad} 失败 — {dt * 1000:.1f} ms")
    if a.report:
        print("| 文件 | 缓存 | 产物 | 备注 |")
        print("|---|---|---|---|")
        for r in records:
            state = "命中" if r in records[:hit] and r.get("key") in {x.get("key") for x in records[:hit]} else "重编"
            print(f"| `{r['file']}` | {state} | {', '.join(r['artifacts'])} | "
                  f"{r.get('error', '')} |")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
