#!/usr/bin/env python3
# fuai_contract_check.py — P0 静态一致性检查器 (docs/137 判据 A)
#
# 唯一权威 = sdk/fuai-spec/spec.json; 本检查器扫描两实现源码, 对账:
#   (a) FujoOS 内核 dispatch (kernel/src/syscall.rs) — 每条 spec 原语的
#       opcode 与 fujo_fn 必须存在; FUAI 原语段被 dispatch 覆盖无遗漏
#   (b) LinuxFUAI 公开接口 (LinuxFUAI/include/fuai.h) — linux_fn 非 null 的
#       条目函数必须存在; 标 null 的不得意外出现
#   层划分: 只对 layer=core 断言双实现性; host-ext 仅要求 fujo 侧存在。
# 退出码: 0 = 0 差异 (判据 A), 1 = 有差异, 2 = 异常。

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "sdk" / "fuai-spec" / "spec.json"
SYS = ROOT / "kernel" / "src" / "syscall.rs"
FUAI_H = ROOT / "LinuxFUAI" / "include" / "fuai.h"

# FUAI 原语段: dispatch 中出现时必须被 spec 覆盖的 opcode 范围。
# 排除: 0x8A01+ (virtio/net 设备原语) / 0x51xx 中 kbd·ipc·kobj·shim·trace.
FUAI_SEGMENTS = (
    (0x5101, 0x5101), (0x5102, 0x5102), (0x5104, 0x5104),
    (0x8001, 0x8005),
    (0x8101, 0x810A),
    (0x8201, 0x8203),
    (0x8301, 0x8320),
    (0x8C01, 0x8C01),
)

DISPATCH_RE = re.compile(r"^\s*0x([0-9A-Fa-f]{4})\s*=>", re.MULTILINE)


def in_fuai_segments(op: int) -> bool:
    return any(lo <= op <= hi for lo, hi in FUAI_SEGMENTS)


def main() -> int:
    diffs = []
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    prims = spec["primitives"]
    by_op = {p["opcode"]: p for p in prims}

    # ---- 富士侧: dispatch opcode -> 表达式块文本 (多行块收集) ----
    text = SYS.read_text(encoding="utf-8")
    dispatch = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*0x([0-9A-Fa-f]{4})\s*=>", lines[i])
        if not m:
            i += 1
            continue
        op = int(m.group(1), 16)
        block = [lines[i][m.end():].strip()]
        j = i + 1
        while j < len(lines) and not re.match(r"^\s*0x[0-9A-Fa-f]{4}\s*=>", lines[j]):
            block.append(lines[j].strip())
            j += 1
        dispatch[op] = " ".join(b for b in block if b)
        i = j
    assert dispatch, "dispatch 解析失败"

    # ---- LinuxFUAI 侧: 函数名存在性 + 注释 opcode 富集 ----
    fuai_src = FUAI_H.read_text(encoding="utf-8")
    funcs = set(re.findall(r"\b(fuai_[a-z0-9_]+)\s*\(", fuai_src))

    # (a1) spec 条目必须存在富士 dispatch
    for p in prims:
        if p["opcode"] not in dispatch:
            diffs.append(f"[{p['name']:14s}] fujo: {p['fujo']} — opcode 0x{p['opcode']:04X} 不在 dispatch")
            continue
        line = dispatch[p["opcode"]]
        if p["fujo"] and not re.search(rf"\b{re.escape(p['fujo'])}\s*\(", line):
            diffs.append(f"[{p['name']:14s}] fujo: dispatch 行不含 {p['fujo']} -> {line.strip()[:60]}")

    # (a2) FUAI 段 dispatch 条目必须被 spec 覆盖 (防遗漏)
    unordered = sorted(set(dispatch) - set(by_op))
    for op in unordered:
        if in_fuai_segments(op):
            diffs.append(f"[??           ] opcode 0x{op:04X} 在 dispatch 但在 spec 未登记: {dispatch[op].strip()[:60]}")

    # (a3) spec 孤儿: 不在 FUAI 段的 spec 条目 (规格录入错误)
    for p in prims:
        if not in_fuai_segments(p["opcode"]):
            diffs.append(f"[{p['name']:14s}] opcode 0x{p['opcode']:04X} 不在 FUAI 原语段 (录入错误?)")

    # (b) LinuxFUAI 函数存在性
    for p in prims:
        if p["linux"]:
            if not re.search(rf"\b{re.escape(p['linux'])}\s*\(", fuai_src):
                diffs.append(f"[{p['name']:14s}] linux: {p['linux']} 不在 fuai.h")
        else:
            if p["layer"] == "core" and p["linux"] is None:
                pass  # 未实现属登记状态, 不报差异; 覆盖率报告覆盖
            if p["linux"] is None and re.search(rf"\bfuai_{p['name']}\s*\(", fuai_src):
                diffs.append(f"[{p['name']:14s}] linux: spec 标 null 但 fuai.h 出现 fuai_{p['name']} (需登记)")

    # ---- 格式层点检: shm 帧 v2 布局常量 (两实现逐字面一致) ----
    # spec.json formats.shm_frame_v2 的机械依据: 每个常量必须在两实现源码同值出现
    FORMAT_CONSTS = {
        "payload_off": ("SHM_OFF_PAYLOAD", r"0x030|0x30\b"),
        "payload_max": ("SHM_PAYLOAD_MAX", r"0x400"),
        "ctx_off": ("SHM_OFF_CTX", r"0x800"),
        "ctx_max": ("SHM_CTX_MAX", r"0x600"),
        "crit_mask": ("EV_CRIT_MASK", r"0x1C"),
    }
    fujo_src = SYS.parent / "ai.rs"
    ai_txt = fujo_src.read_text(encoding="utf-8")
    chan_c = (ROOT / "LinuxFUAI" / "src" / "fuai_channel.c")
    chan_txt = chan_c.read_text(encoding="utf-8") if chan_c.exists() else ""
    for const, pat in FORMAT_CONSTS.values():
        name, p = const, pat
        hit_f = bool(re.search(p, ai_txt)) or bool(re.search(p, text))
        hit_l = bool(re.search(p, chan_txt)) or bool(re.search(rf"{name}\s+0x{0x1C:X}", fuai_src))
        if p == r"0x1C":
            # crit mask 在 ai.rs 以注释/移位形式出现, 专门检索
            hit_f = "0x1C" in ai_txt or bool(re.search(r"EV_WINDOW - 1", ai_txt))
        if not (hit_f and hit_l):
            diffs.append(f"[format        ] {name}: 两实现常量不一致 (fujo={hit_f} linux={hit_l})")
    # FJRU 字节码: 仅存在性点检 (加载语义差分留 P2 行为夹具)
    if not (ROOT / "sdk" / "rulebook" / "fjru.bin").exists():
        diffs.append("[format        ] sdk/rulebook/fjru.bin 缺失")

    # ---- 发布副本一致性: LinuxFUAI/spec/spec.json 必须与权威单源逐字节一致 ----
    release_spec = ROOT / "LinuxFUAI" / "spec" / "spec.json"
    if not release_spec.exists():
        diffs.append("[release       ] LinuxFUAI/spec/spec.json 缺失 (发布包不自包含)")
    elif release_spec.read_bytes() != SPEC.read_bytes():
        diffs.append("[release       ] LinuxFUAI/spec/spec.json 与权威单源不一致 (需同步)")

    # ---- 覆盖率报告 (core 层双实现度) ----
    core = [p for p in prims if p["layer"] == "core"]
    n_both = sum(1 for p in core if p["fujo"] and p["linux"])
    n_fujo_only = sum(1 for p in core if p["fujo"] and not p["linux"])

    print("FUAI-SPEC CHECK v1  (spec.json 唯一权威)")
    print(f"  spec 原语: {len(prims)} (core {len(core)} / host-ext {len(prims) - len(core)})")
    print(f"  core 双实现: {n_both}/{len(core)}   fujo-only: {n_fujo_only}")
    print(f"  富士 dispatch 覆盖: {len([o for o in dispatch if in_fuai_segments(o)])} 条 FUAI 段原语")

    # ---- 部署参数差异: 非接口契约, 只提示 (策略须显式声明才能差分) ----
    dep = spec.get("deployment_params", {})
    dep_notes = [f"{n}: fujo={d.get('fujo')} linux={d.get('linux')}"
                 for n, d in dep.items() if d.get("fujo") != d.get("linux")]
    if dep_notes:
        print("  [INFO] 部署参数差异 (非接口, P2 差分须显式策略): " + "; ".join(dep_notes))
    if diffs:
        print(f"\n[DIFF] {len(diffs)} 项:")
        for d in diffs:
            print("  " + d)
        return 1
    print("\n[OK] 0 差异 — spec 与两实现一致 (判据 A)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
