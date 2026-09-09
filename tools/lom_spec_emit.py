#!/usr/bin/env python3
# lom_spec_emit.py — 由 lom/fuai.lom 生成 FUAI 契约文档 spec.json (docs/141 v1)
#
# 权威翻转: 此前 spec.json 是权威、.lom 由它迁移而来; 现在 .lom 是权威,
# spec.json 与 LinuxFUAI/spec/spec.json 均为生成物 (两者必须逐字节一致)。
#
# 用法:
#   python tools/lom_spec_emit.py --check    # 只对账, 有差异退出 1
#   python tools/lom_spec_emit.py            # 写盘 (两份副本)

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOM = ROOT / "lom" / "fuai.lom"
TARGETS = (
    ROOT / "sdk" / "fuai-spec" / "spec.json",
    ROOT / "LinuxFUAI" / "spec" / "spec.json",
)
PRIM_KEYS = ("layer", "sig", "fujo", "linux", "semantics")


def j(v: object) -> str:
    return json.dumps(v, ensure_ascii=False)


def emit(mod: lomc.Module) -> str:
    if mod.meta is None:
        raise SystemExit("[ERR] lom/fuai.lom 缺 meta 块")
    meta = mod.meta.entries
    enum = next(e for e in mod.enums if e.name == "Opcode")

    def group(name: str, last: bool) -> list[str]:
        g = meta.get(name) or {}
        out = [f'  "{name}": {{']
        items = list(g.items())
        for i, (k, v) in enumerate(items):
            out.append(f"    {j(k)}: {j(v)}" + ("," if i < len(items) - 1 else ""))
        out.append("  }" + ("" if last else ","))
        return out

    L = ["{"]
    L.append(f'  "$schema": {j(meta.get("schema", ""))},')
    L.append(f'  "title": {j(meta.get("title", ""))},')
    L.append(f'  "version": {j(meta.get("version", ""))},')
    L.append(f'  "authority": {j(meta.get("authority", ""))},')
    L += group("layers", last=False)

    L.append('  "primitives": [')
    for i, v in enumerate(enum.variants):
        obj: OrderedDict[str, object] = OrderedDict()
        obj["opcode"] = v.value
        obj["name"] = v.name
        for k in PRIM_KEYS:
            obj[k] = v.meta.get(k)
        L.append("    " + json.dumps(obj, ensure_ascii=False) + ("," if i < len(enum.variants) - 1 else ""))
    L.append("  ],")

    L += group("formats", last=False)
    L += group("gates", last=False)

    L.append('  "deployment_params": {')
    for i, p in enumerate(mod.params):
        L.append(f'    {j(p.name)}: {{')
        keys = list(p.values.items())
        for k, val in keys:
            L.append(f'      {j(k)}: {val},')
        L.append(f'      "note": {j(p.note)}')
        L.append("    }" + ("," if i < len(mod.params) - 1 else ""))
    L.append("  }")
    L.append("}")
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    try:
        mod = lomc.load(LOM)
    except lomc.LomError as e:
        print(f"[ERR] {LOM}: {e}", file=sys.stderr)
        return 1
    want = emit(mod)
    diffs = []
    for t in TARGETS:
        if not t.exists():
            diffs.append(f"{t}: 缺失")
        elif t.read_text(encoding="utf-8") != want:
            diffs.append(f"{t}: 与 lom/fuai.lom 生成结果不一致")
    if diffs:
        print(f"[DIFF] spec.json 生成物对账: {len(diffs)} 项")
        for d in diffs:
            print("  " + d)
        if not check:
            for t in TARGETS:
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_text(want, encoding="utf-8", newline="\n")
                print(f"[OK] 写入 {t.relative_to(ROOT)} ({len(want)}B)")
            return 0
        return 1
    print(f"[OK] spec.json 与 lom/fuai.lom 一致（{len(want)}B, {len(TARGETS)} 份副本）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
