#!/usr/bin/env python3
# loment_manual.py — 语言手册站点生成 (M93, docs/151)
#
# 判据: 手册与编译器同版本 —— 站点里写入编译器版本戳, --check 逐字节对账。
#   python tools/loment_manual.py --emit docs/manual
#   python tools/loment_manual.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomdoc  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"
SPECS = ["docs/141-l0-lom-spec.md", "docs/142-potato-v0.md", "docs/143-l1-loment-v0.md",
         "docs/144-loment-native-backend.md", "docs/146-loment-capability-semantics.md",
         "docs/147-potato-v1-spec.md", "docs/148-loment-toolchain.md",
         "docs/149-loment-kernel-integration.md", "docs/150-loment-selfhost.md",
         # 借用/所有权的边界 (docs/205 R1): 哪些能证、哪些不证。**放在规范那一栏**是
         # 有意的 —— 它讲的是实现的界, 而读者问"这段能不能编过"时, 它就是规范性的答案。
         "docs/206-loment-ownership-boundary.md"]


def compiler_version() -> str:
    h = hashlib.sha256((ROOT / "tools" / "lomentc.py").read_bytes()).hexdigest()[:12]
    return f"lomentc-{h}"


def build() -> dict[str, str]:
    ver = compiler_version()
    out: dict[str, str] = {}
    idx = ["# Loment 语言手册", "",
           f"> 编译器版本戳: `{ver}`（由 tools/loment_manual.py 生成）", "",
           "## 规范", ""]
    for s in SPECS:
        p = ROOT / s
        if p.exists():
            idx.append(f"- [{p.name}](../{p.name})")
    idx += ["", "## 示例 API", ""]
    for src in sorted(EX.glob("*.lomt")):
        mod = lomentc.load(src)
        deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
        if lomentc.check(mod, deps=deps):
            continue
        text = lomdoc.render(mod, src.read_text(encoding="utf-8"),
                             src.relative_to(ROOT).as_posix())
        out[f"api/{src.stem}.md"] = text
        idx.append(f"- [{src.stem}](api/{src.stem}.md)")
    out["index.md"] = "\n".join(idx).rstrip() + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_manual")
    ap.add_argument("--emit", metavar="DIR")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    files = build()
    # 无参数 = 门禁模式 (与 loment_release / loment_status 同一约定: ci.py 的静态
    # 门禁按 `main()` 无参调用)。原先无参直接 rc=2, 于是它**从来没进过 CI** ——
    # 结果手册的编译器版本戳漂了整整一版没人抓到 (2026-09-13)。
    if a.check or not (a.emit):
        base = ROOT / "docs" / "manual"
        bad = 0
        for rel, text in files.items():
            p = base / rel
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                bad += 1
                print(f"[DIFF] docs/manual/{rel}")
        print(f"loment_manual: {len(files) - bad}/{len(files)} 与编译器版本一致")
        return 1 if bad else 0
    if a.emit:
        base = Path(a.emit)
        for rel, text in files.items():
            p = base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            # 显式 LF: 仓库对 *.md 声明了 eol=lf, 用平台默认换行会让 25 个手册文件
            # 在 Windows 上每次 --emit 都变成"已修改"(docs/158 §改动流程的纸面噪声)。
            with p.open("w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        print(f"[OK] {len(files)} 个文件 -> {base}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
