#!/usr/bin/env python3
# loment_bootstrap.py — 自举链一键引导 (M87, docs/150)
#
# 判据: 干净环境一条命令走完"自举前端"链路 ——
#   (1) 重新生成三个自举产物的形式对象, 与磁盘逐字节核对;
#   (2) 跑 M79/M80/M81 三项对照 (Loment 版 lexer/parser/checker vs Python 版);
#   (3) 打印引导报告 (编译器版本戳 + 每步结果)。
#
# 用法: python tools/loment_bootstrap.py [--json]
# 退出码: 0 = 全绿 / 1 = 有步骤失败 / 2 = 环境缺失。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SELFHOST = ROOT / "loment" / "selfhost"
BUILD = ROOT / "loment" / "build"
MODULES = ("lexer", "parser", "checker")


def step_artifacts() -> list[str]:
    """重新生成自举产物的形式对象并与磁盘核对。"""
    bad = []
    for name in MODULES:
        src = SELFHOST / f"{name}.lomt"
        dest = BUILD / f"selfhost_{name}.potato.json"
        mod, deps = lomentc.load_unit(src, ROOT)    # 唯一入口（docs/182 §1.10）
        want = lomentc.emit_potato(mod, ROOT, deps)
        if not dest.exists() or dest.read_text(encoding="utf-8") != want:
            bad.append(f"selfhost_{name}.potato.json")
    return bad


def step_checks() -> list[str]:
    """跑自举对照 (M79/M80/M81 前端 + M82/M83/M84 后端与定点)。"""
    import loment_p8_test as T
    failed = []
    for fn in (T.test_m79_loment_lexer_matches_python,
               T.test_m80_loment_parser_ast_dump,
               T.test_m81_loment_checker_matches_python,
               T.test_m82_loment_codegen_byte_identical,
               T.test_m83_m84_self_compile_and_fixed_point):
        try:
            fn()
        except AssertionError as e:
            failed.append(f"{fn.__name__}: {e}")
    return failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_bootstrap", description="自举链一键引导")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    mode = "check" if (("--check" in argv) if argv else False) else "run"
    report = {"mode": mode, "modules": list(MODULES), "steps": []}
    bad_art = step_artifacts()
    report["steps"].append({"step": "artifacts", "ok": not bad_art, "detail": bad_art})
    bad_chk = step_checks()
    report["steps"].append({"step": "selfhost-checks", "ok": not bad_chk, "detail": bad_chk})
    ok = not bad_art and not bad_chk
    report["ok"] = ok
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for s in report["steps"]:
            print(f"  [{'OK' if s['ok'] else 'FAIL'}] {s['step']}"
                  + (f" — {s['detail']}" if s["detail"] else ""))
        print(f"loment_bootstrap: {'全绿' if ok else '有失败'} "
              f"(自举前端 {len(MODULES)} 个模块)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
