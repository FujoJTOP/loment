#!/usr/bin/env python3
# loment_release.py — 发布清单与可复现包 (M95/M99/M100, docs/152)
#
# 判据: 发布清单覆盖全部 Loment 工件, 每条带 sha256; --check 可被第三方机器复现。
#   python tools/loment_release.py --emit
#   python tools/loment_release.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "build" / "release-manifest.json"
GLOBS = [
    "tools/lomc.py", "tools/lom_audit.py", "tools/lomc_test.py", "tools/lomentc.py",
    "tools/lomentc_test.py", "tools/potato.py", "tools/potato_test.py",
    "tools/potato_cross.py", "tools/potato_from.py", "tools/potato_measure.py",
    "tools/potato_assert.py", "tools/loment.py", "tools/lomfmt.py", "tools/lomdoc.py",
    "tools/lompkg.py", "tools/loment_diag.py", "tools/loment_build.py",
    "tools/loment_lsp.py", "tools/loment_tools_test.py", "tools/loment_boot.py",
    "tools/loment_p7_test.py", "tools/loment_p8_test.py", "tools/loment_p9_test.py",
    "tools/loment_syscalls.py", "tools/loment_manual.py", "tools/ci.py",
    "loment/examples/*.lomt", "loment/selfhost/*.lomt", "loment/corpus.json",
    "lom/*.lom", "docs/14*.md", "docs/15*.md", "docs/manual/*.md",
    "docs/manual/api/*.md",
]


def sha(p: Path) -> str:
    """内容哈希 (通用换行: 同一文件在 LF/CRLF 检出下哈希相同)。"""
    text = p.read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build() -> dict:
    files = []
    for g in GLOBS:
        for p in sorted(ROOT.glob(g)):
            if p.is_file():
                files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)})
    return {"release": "loment-1.0-pre", "files": files}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_release")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    want = build()
    if a.emit:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(want, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"[OK] {OUT.relative_to(ROOT)} ({len(want['files'])} 个工件)")
        return 0
    if a.check:
        if not OUT.exists():
            print(f"[ERR] {OUT.relative_to(ROOT)} 缺失 (运行 --emit)")
            return 1
        got = json.loads(OUT.read_text(encoding="utf-8"))
        gmap = {x["path"]: x["sha256"] for x in got.get("files", [])}
        wmap = {x["path"]: x["sha256"] for x in want["files"]}
        bad = [p for p in wmap if gmap.get(p) != wmap[p]]
        extra = [p for p in gmap if p not in wmap]
        for p in bad[:8]:
            print(f"[DIFF] {p}")
        for p in extra[:8]:
            print(f"[STALE] {p}")
        print(f"loment_release: {len(wmap) - len(bad)}/{len(wmap)} 一致"
              f"{f' (+{len(extra)} 陈旧)' if extra else ''}")
        return 1 if bad or extra else 0
    print("[ERR] 需要 --emit 或 --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
