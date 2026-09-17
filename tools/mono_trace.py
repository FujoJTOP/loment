#!/usr/bin/env python3
# mono_trace.py — 单态化金标轨迹 (M6/M7/M8, docs/156)
#
# 用途: 把参考实现 lomentc 的**单态化结果**打印成可核对的清单 —— 自举版 codegen 只吃 token 流,
# 没有 AST, 要复刻 `prepare()` 的实例化, 就需要一份"应该生成哪些实例、按什么顺序、叫什么名字"
# 的金标数据。这个工具就是产出它的地方 (也是 docs/156 里那几张表的来源)。
#
# 用法:
#   python tools/mono_trace.py loment/examples/native_gen.lomt
#   python tools/mono_trace.py --all          # 对全部示例跑一遍摘要
#   python tools/mono_trace.py --check        # 断言命名规则与 docs/156 记录一致
# 退出码: 0 = 成功 / 1 = 断言失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# docs/156 记录的金标 (名字 -> 由哪个泛型声明 + 实参而来)。--check 用它兜住命名规则别漂。
GOLDEN = {
    "max_u32": ("max", ["u32"]),
    "max_i32": ("max", ["i32"]),
    "Pair_u32": ("Pair", ["u32"]),
    "Opt_u32": ("Opt", ["u32"]),
    "Result_u32_u32": ("Result", ["u32", "u32"]),
}


def trace(path: Path) -> dict:
    """返回单模块的单态化轨迹: 泛型声明 / 实例 / 顺序 / 命名。"""
    mod = lomentc.load(path)
    deps = lomentc.resolve_deps(mod, ROOT, path.parent, entry=path)
    pre, _ = lomentc.prepare(mod, deps)
    generics, instances, order = [], [], []
    for f in pre.funcs:
        if f.from_generic:
            instances.append({
                "name": f.name,
                "from": f.from_generic,
                "args": list(f.generic_args),
                "params": [(p.name, p.type) for p in f.params],
                "ret": f.ret,
            })
            order.append(f.name)
        else:
            order.append(f.name)
    for f in mod.funcs:
        if f.tparams:
            generics.append({"name": f.name, "tparams": list(f.tparams)})
    # 泛型 struct/enum 声明也在 mod 里 (prepare 不改声明本身, 只改使用点)
    gstructs = [s.name for s in mod.structs if s.tparams]
    genums = [e.name for e in mod.enums if getattr(e, "tparams", [])]
    return {
        "file": path.name,
        "generic_funcs": generics,
        "generic_structs": gstructs,
        "generic_enums": genums,
        "emit_order": order,
        "instances": instances,
    }


def naming_ok(name: str, base: str, args: list[str]) -> bool:
    """命名规则: base + "_" + "_".join(args) (与 _instantiate 同式)。"""
    return name == base + "_" + "_".join(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mono_trace", description="单态化金标轨迹")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)

    targets: list[Path] = []
    if a.file:
        targets = [Path(a.file)]
    elif a.all or a.check:
        targets = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    else:
        ap.print_help()
        return 2

    bad: list[str] = []
    traces = []
    for t in targets:
        if not t.is_absolute():
            t = (ROOT / t).resolve()
        try:
            tr = trace(t)
        except Exception as e:  # noqa: BLE001  有些示例原生后端本身不支持
            if a.file:
                print(f"[ERR] {t.name}: {type(e).__name__}: {e}")
                return 1
            continue
        traces.append(tr)
        for inst in tr["instances"]:
            if not naming_ok(inst["name"], inst["from"], inst["args"]):
                bad.append(f"{tr['file']}: {inst['name']} != {inst['from']}_{'_'.join(inst['args'])}")
            if a.check and inst["name"] in GOLDEN:
                gb, ga = GOLDEN[inst["name"]]
                if inst["from"] != gb or inst["args"] != ga:
                    bad.append(f"{tr['file']}: {inst['name']} 金标漂移 (from={inst['from']} args={inst['args']})")

    if a.json:
        print(json.dumps(traces, ensure_ascii=False, indent=2))
        return 0 if not bad else 1

    for tr in traces:
        if not (tr["instances"] or tr["generic_funcs"] or tr["generic_structs"] or tr["generic_enums"]):
            continue
        print(f"== {tr['file']}")
        if tr["generic_funcs"]:
            print("   泛型函数: " + ", ".join(f"{g['name']}<{','.join(g['tparams'])}>"
                                              for g in tr["generic_funcs"]))
        if tr["generic_structs"] or tr["generic_enums"]:
            print("   泛型类型: " + ", ".join(tr["generic_structs"] + tr["generic_enums"]))
        print("   发射顺序: " + " -> ".join(tr["emit_order"]))
        for inst in tr["instances"]:
            print(f"   实例 {inst['name']}: 由 {inst['from']}<{','.join(inst['args'])}> 而来, "
                  f"参数 {inst['params']} -> {inst['ret']}")

    if a.check:
        for b in bad:
            print(f"[ERR] {b}")
        n_inst = sum(len(t["instances"]) for t in traces)
        print(f"[{'OK' if not bad else 'FAIL'}] mono_trace --check "
              f"({len(traces)} 个文件, {n_inst} 个实例, {len(bad)} 个问题)")
        return 0 if not bad else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
