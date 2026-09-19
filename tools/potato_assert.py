#!/usr/bin/env python3
# potato_assert.py — Potato 形式对象 -> 内核断言绑定表 (M50, docs/147 §4)
#
# 判据 (docs/110 §5.3): 形式对象驱动 A1–A4 的断言语义, 不读源码。
# 本工具只消费形式对象 (Potato), 生成 Rust 断言表 —— 内核侧在 P7 (M39/M42) include! 接入。
#
# 绑定规则 (docs/147 §4):
#   A1 无越权执行 : 能力域必须有名空间 + 有限区间       a1 = space 非空 && hi >= lo
#   A2 可审计     : 每个 guard 站点必须落审计计数        a2 = guards >= 0 (编译器保证 guard => 审计)
#   A3 模型缺席仍运行: 单元不得申请模型/AI 能力空间       a3 = 无 AI_SPACES 中的空间
#   A4 失败计数与降级: 可撤销能力必须声明 revocable       a4 = revocable
#
# 用法:
#   python tools/potato_assert.py --emit-rust loment/build/cap_asserts.rs
#   python tools/potato_assert.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OBJ_DIR = ROOT / "loment" / "build"
AI_SPACES = {"ai", "model", "llm", "net_llm", "infer"}
HEADER = "// 由 tools/potato_assert.py 从 loment/build/*.potato.json 生成 —— 请勿手改。"


def load_objects() -> list[dict]:
    objs = []
    for p in sorted(OBJ_DIR.glob("*.potato.json")):
        objs.append(json.loads(p.read_text(encoding="utf-8")))
    return objs


def rows(objs: list[dict]) -> list[dict]:
    out = []
    for doc in objs:
        guards = doc.get("guards", 0)
        for c in doc.get("capabilities", []):
            d = c["domain"]
            a3 = d["space"] not in AI_SPACES
            out.append({
                "unit": doc["unit"], "name": c["name"], "space": d["space"],
                "lo": d["lo"], "hi": d["hi"], "revocable": bool(c["revocable"]),
                "guards": int(guards),
                "a1": bool(d["space"]) and d["hi"] >= d["lo"],
                "a2": int(guards) >= 0,
                "a3": a3,
                "a4": bool(c["revocable"]),
            })
    return out


def _rs_bool(v: bool) -> str:
    return "true" if v else "false"


def emit_rust(objs: list[dict]) -> str:
    rs = rows(objs)
    lines = [
        HEADER,
        "// 断言绑定: A1 域封闭 / A2 审计站点 / A3 模型缺席 / A4 可撤销 (docs/147 §4)。",
        "",
        "#[derive(Clone, Copy)]",
        "pub struct CapAssert {",
        "    pub unit: &'static str,",
        "    pub name: &'static str,",
        "    pub space: &'static str,",
        "    pub lo: u64,",
        "    pub hi: u64,",
        "    pub revocable: bool,",
        "    pub guards: u64,",
        "    pub a1: bool,",
        "    pub a2: bool,",
        "    pub a3: bool,",
        "    pub a4: bool,",
        "}",
        "",
        f"pub static CAP_ASSERTS: &[CapAssert] = &[  // {len(rs)} 条",
    ]
    for r in rs:
        lines.append(
            f'    CapAssert {{ unit: "{r["unit"]}", name: "{r["name"]}", '
            f'space: "{r["space"]}", lo: {r["lo"]}, hi: {r["hi"]}, '
            f'revocable: {_rs_bool(r["revocable"])}, guards: {r["guards"]}, '
            f'a1: {_rs_bool(r["a1"])}, a2: {_rs_bool(r["a2"])}, '
            f'a3: {_rs_bool(r["a3"])}, a4: {_rs_bool(r["a4"])} }},')
    lines += [
        "];",
        "",
        "/// 内核启动自检: 返回 (检查数, 失败数); 断言 failed == 0 (P7 接入)。",
        "pub fn assert_a1_a4() -> (u64, u64) {",
        "    let mut checked = 0u64;",
        "    let mut failed = 0u64;",
        "    for c in CAP_ASSERTS {",
        "        checked += 1;",
        "        if !(c.a1 && c.a2 && c.a3 && c.a4) {",
        "            failed += 1;",
        "        }",
        "    }",
        "    (checked, failed)",
        "}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato_assert", description="形式对象 -> 内核断言表")
    ap.add_argument("--emit-rust", metavar="PATH")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--print", dest="print_rs", action="store_true")
    a = ap.parse_args(argv)

    objs = load_objects()
    if not objs:
        print("[ERR] 没有形式对象 (先运行 lomentc --emit-potato)", file=sys.stderr)
        return 2
    for doc in objs:
        errs = potato.validate(doc)
        if errs:
            print(f"[ERR] {doc.get('unit')}: 形式对象非法: {errs[0]}", file=sys.stderr)
            return 1
    want = emit_rust(objs)
    rs = rows(objs)
    bad = sum(1 for r in rs if not (r["a1"] and r["a2"] and r["a3"] and r["a4"]))
    if a.print_rs:
        sys.stdout.write(want)
        return 0
    if a.check:
        dest = ROOT / "loment" / "build" / "cap_asserts.rs"
        if not dest.exists():
            # S3 之后**产物不在索引里**（`docs/189` §3.0）：「文件不在」是仓库的
            # **默认状态**，不是漂移 —— 提示一下，按需生成的路是 `--emit-rust`。
            print(f"[SKIP] {dest.relative_to(ROOT)} 未生成 (按需生成: --emit-rust)")
            return 1 if bad else 0
        if dest.read_text(encoding="utf-8") != want:
            print(f"[ERR] {dest.relative_to(ROOT)} 与形式对象不一致", file=sys.stderr)
            return 1
        print(f"[OK] cap_asserts.rs 一致 ({len(rs)} 条, 失败 {bad})")
        return 1 if bad else 0
    if a.emit_rust:
        Path(a.emit_rust).write_text(want, encoding="utf-8")
        print(f"[OK] {a.emit_rust} ({len(rs)} 条断言, A1-A4 失败 {bad})")
        return 1 if bad else 0
    print("[ERR] 需要 --emit-rust / --check / --print", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
