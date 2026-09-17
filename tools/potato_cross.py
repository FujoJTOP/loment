#!/usr/bin/env python3
# potato_cross.py — Potato 跨实现一致性对照 (M53, docs/147 §6)
#
# 判据: 同一形式对象, FujoOS 侧校验器 (tools/potato.py) 与 LinuxFUAI 侧独立实现
# (LinuxFUAI/tools/potato_verify.py) 必须给出相同判定 (合法/非法)。
# 对照集: 已提交形式对象 + 合法夹具 + 全部反例变异 (potato_test.MUTATORS)。
#
# 用法: python tools/potato_cross.py [--json]
# 退出码: 0 = 100% 一致 / 1 = 有分歧 / 2 = 环境缺失。

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402
import potato_test  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LFV = ROOT / "LinuxFUAI" / "tools" / "potato_verify.py"


def load_lfv():
    if not LFV.exists():
        return None
    spec = importlib.util.spec_from_file_location("potato_verify_lfv", LFV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cases() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for p in sorted((ROOT / "loment" / "build").glob("*.potato.json")):
        out.append((p.stem, json.loads(p.read_text(encoding="utf-8"))))
    legacy = ROOT / "loment" / "build" / "legacy" / "demo.v0.json"
    if legacy.exists():
        out.append(("legacy/demo.v0", json.loads(legacy.read_text(encoding="utf-8"))))
    out.append(("fixture", potato_test.fixture()))
    out.extend((f"mut:{n}", d) for n, d in potato_test.mutated_objects())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato_cross", description="跨实现一致性对照")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    lfv = load_lfv()
    if lfv is None:
        print(f"[ERR] 缺 {LFV.relative_to(ROOT)} (LinuxFUAI 私有库未就位)", file=sys.stderr)
        return 2

    rows, disagree = [], []
    for name, doc in cases():
        f_ok = not potato.validate(doc)
        l_ok = not lfv.verify(doc)
        rows.append({"case": name, "fujoos_valid": f_ok, "linuxfuai_valid": l_ok})
        if f_ok != l_ok:
            disagree.append(name)

    if a.json:
        print(json.dumps({"cases": len(rows), "disagreements": disagree, "rows": rows},
                         ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = "一致" if r["fujoos_valid"] == r["linuxfuai_valid"] else "分歧"
            print(f"  {mark}  {r['case']:34s} FujoOS={'合法' if r['fujoos_valid'] else '非法'}"
                  f" LinuxFUAI={'合法' if r['linuxfuai_valid'] else '非法'}")
        print(f"\npotato_cross: {len(rows) - len(disagree)}/{len(rows)} 判定一致")
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())
