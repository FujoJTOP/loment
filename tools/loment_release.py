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
    "tools/potato_llm_arm.py",
    "tools/potato_assert.py", "tools/loment.py", "tools/lomfmt.py", "tools/lomdoc.py",
    "tools/lompkg.py", "tools/loment_diag.py", "tools/loment_build.py",
    "tools/loment_lsp.py", "tools/loment_tools_test.py", "tools/loment_boot.py",
    "tools/loment_p7_test.py", "tools/loment_p8_test.py", "tools/loment_p9_test.py",
    "tools/loment_syscalls.py", "tools/loment_manual.py", "tools/ci.py",
    "tools/vscode_ext.py", "tools/vscode_ext_test.py", "tools/mono_trace.py",
    "tools/loment_ir_diff.py", "tools/loment_rule_parity.py",
    "tools/loment_seed.py", "tools/loment_seed_test.py",
    "tools/loment_fmt_test.py", "tools/loment_audit.py",
    "tools/loment_doc_test.py",
    "tools/loment_filetype.py", "tools/loment_filetype_test.py",
    # 无 Python 自举 (docs/159): 启动脚本 + 种子 (参考实现发射的驱动 IR) + Loment 版格式化器
    "loment/bootstrap.sh", "loment/build/selfhost_driver.ll", "loment/tools/*.lomt",
    "editors/loment.ico",
    "editors/vscode/package.json", "editors/vscode/language-configuration.json",
    "editors/vscode/README.md", "editors/vscode/src/*.js",
    "editors/vscode/syntaxes/*.json",
    "loment/examples/*.lomt", "loment/selfhost/*.lomt", "loment/corpus.json",
    "lom/*.lom", "docs/14*.md", "docs/15*-loment-*.md", "docs/16*-loment-*.md",
    "docs/manual/*.md",
    "docs/manual/api/*.md",
]


def sha(p: Path) -> str:
    """内容哈希。文本按**通用换行**归一后哈希 (同一文件在 LF/CRLF 检出下哈希相同);
    二进制 (图标/压缩包) 按原始字节哈希 —— 清单里两类可以混, 消费者只比 sha256。"""
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build() -> dict:
    files = []
    for g in GLOBS:
        for p in sorted(ROOT.glob(g)):
            if p.is_file():
                files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)})
    return {"release": "loment-1.0-pre", "files": files}


def checksums_text(doc: dict) -> str:
    """SHA256SUMS 风格的校验和清单 (M88)。"""
    return "".join(f"{x['sha256']}  {x['path']}\n" for x in doc["files"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_release")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--checksums", metavar="PATH", help="写 SHA256SUMS 风格清单 (M88)")
    a = ap.parse_args(argv)
    want = build()
    if a.checksums:
        Path(a.checksums).write_text(checksums_text(want), encoding="utf-8")
        print(f"[OK] {a.checksums} ({len(want['files'])} 行)")
        return 0
    if a.emit:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(want, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"[OK] {OUT.relative_to(ROOT)} ({len(want['files'])} 个工件)")
        return 0
    if a.check or not (a.emit or a.checksums):
        # 无参数 = 门禁模式 (与仓库其它工具同一约定: ci.py 的静态门禁按 main() 调用)
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
    print("[ERR] 需要 --emit 或 --checksums", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
