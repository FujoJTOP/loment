#!/usr/bin/env python3
# lom_audit.py — 用 .lom 单源审计既有实现 (L0 落地路径, docs/141)
#
# 定位: lomc.py 是语言编译器 (通用); 本文件是项目审计 (FujoOS 特有)。
# 审计项:
#   [fuai/spec]    lom/fuai.lom  <->  sdk/fuai-spec/spec.json   双向逐字段
#   [fuai/fujo]    lom/fuai.lom  <->  kernel/src/syscall.rs     opcode + fujo_fn
#   [fuai/linux]   lom/fuai.lom  <->  LinuxFUAI/include/fuai.h  linux_fn
#   [fuai/param]   部署参数值    <->  实现源码中的数值声明 (陈旧注释检测)
#   [fuc/layout]   lom/fuc.lom   <->  tools/fuic.py 格式串 + kernel/src/fui/fuc.rs 常量
#
# 退出码: 0 = 0 差异, 1 = 有差异, 2 = 异常。

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOM_FUAI = ROOT / "lom" / "fuai.lom"
LOM_FUC = ROOT / "lom" / "fuc.lom"
LOM_FUJR = ROOT / "lom" / "fujr.lom"
SPEC = ROOT / "sdk" / "fuai-spec" / "spec.json"
SYS = ROOT / "kernel" / "src" / "syscall.rs"
FUAI_H = ROOT / "LinuxFUAI" / "include" / "fuai.h"
FUIC = ROOT / "tools" / "fuic.py"
FUC_RS = ROOT / "kernel" / "src" / "fui" / "fuc.rs"
FUJR_RS = ROOT / "kernel" / "src" / "fujr.rs"
FUJPACK = ROOT / "tools" / "fujopack.py"

# 生成物登记: (lom 源, 生成器, 磁盘路径) — 逐字节对账, 漂移即报错
GENERATED = [
    (LOM_FUC, "rust", ROOT / "lom" / "build" / "fuc.rs"),
    (LOM_FUC, "c", ROOT / "lom" / "build" / "fuc.h"),
    (LOM_FUC, "python", ROOT / "lom" / "build" / "fuc.py"),
    (LOM_FUC, "json", ROOT / "lom" / "build" / "fuc.json"),
    (LOM_FUC, "rust", ROOT / "kernel" / "src" / "fui" / "fuc_gen.rs"),
    (LOM_FUAI, "rust", ROOT / "lom" / "build" / "fuai.rs"),
    (LOM_FUAI, "c", ROOT / "lom" / "build" / "fuai.h"),
    (LOM_FUAI, "python", ROOT / "lom" / "build" / "fuai.py"),
    (LOM_FUAI, "json", ROOT / "lom" / "build" / "fuai.json"),
    (LOM_FUJR, "rust", ROOT / "lom" / "build" / "fujr.rs"),
    (LOM_FUJR, "c", ROOT / "lom" / "build" / "fujr.h"),
    (LOM_FUJR, "python", ROOT / "lom" / "build" / "fujr.py"),
    (LOM_FUJR, "json", ROOT / "lom" / "build" / "fujr.json"),
]

# 部署参数 -> 富士侧实现文件与别名模式 (项目特有知识, 故留在审计脚本而非语言里)
PARAM_BINDINGS = {
    "tau_high_default": {
        "fujo_files": [ROOT / "kernel" / "src" / "capability.rs"],
        "alias": r"(?:τ_high|tau_high|cfg7|cfg\s*7)",
    },
    "tau_low_default": {
        "fujo_files": [ROOT / "kernel" / "src" / "capability.rs"],
        "alias": r"(?:τ_low|tau_low|cfg8|cfg\s*8)",
    },
}

# 取整数时排除标识符内数字 (W46) 与小数 (0.45)
INT_RE = re.compile(r"(?<![A-Za-z_0-9.])(\d+)(?![0-9])")
DISPATCH_RE = re.compile(r"^\s*0x([0-9A-Fa-f]{4})\s*=>", re.MULTILINE)


def parse_dispatch(text: str) -> dict[int, str]:
    """syscall.rs: opcode -> 该分支的多行表达式文本。"""
    out: dict[int, str] = {}
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
        out[op] = " ".join(b for b in block if b)
        i = j
    return out


def audit_fuai(diffs: list[str]) -> tuple[int, int]:
    mod = lomc.load(LOM_FUAI)
    enum = mod.enums[0]
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    prims = {p["opcode"]: p for p in spec["primitives"]}
    lom_by_op = {v.value: v for v in enum.variants}

    # --- (a) .lom <-> spec.json 双向
    for v in enum.variants:
        p = prims.get(v.value)
        if p is None:
            diffs.append(f"[fuai/spec ] {v.name}: opcode 0x{v.value:04X} 在 .lom 但 spec.json 未登记")
            continue
        if p["name"] != v.name:
            diffs.append(f"[fuai/spec ] 0x{v.value:04X}: .lom 名 {v.name} != spec 名 {p['name']}")
        for key in ("layer", "sig", "fujo", "semantics"):
            want = p.get(key)
            got = v.meta.get(key)
            if (want or None) != (got or None):
                diffs.append(f"[fuai/spec ] {v.name}.{key}: .lom={got!r} spec={want!r}")
        if (p.get("linux") or None) != (v.meta.get("linux") or None):
            diffs.append(
                f"[fuai/spec ] {v.name}.linux: .lom={v.meta.get('linux')!r} spec={p.get('linux')!r}"
            )
    for op, p in prims.items():
        if op not in lom_by_op:
            diffs.append(f"[fuai/spec ] {p['name']}: spec 有 opcode 0x{op:04X} 但 .lom 缺登记")

    # --- (a2) 权威翻转 (docs/141 v1): spec.json 必须是 .lom 的生成物
    import lom_spec_emit as _lse

    want_spec = _lse.emit(mod)
    for t in _lse.TARGETS:
        if not t.exists():
            diffs.append(f"[fuai/gen  ] {t.name} 缺失")
        elif t.read_text(encoding="utf-8") != want_spec:
            diffs.append(f"[fuai/gen  ] {t.relative_to(ROOT)} 与 .lom 生成结果不一致 (重新生成)")

    # --- (b) .lom <-> syscall.rs dispatch
    text = SYS.read_text(encoding="utf-8")
    dispatch = parse_dispatch(text)
    assert dispatch, "syscall.rs dispatch 解析失败"
    for v in enum.variants:
        if v.value not in dispatch:
            diffs.append(f"[fuai/fujo ] {v.name}: opcode 0x{v.value:04X} 不在 dispatch")
            continue
        fn = v.meta.get("fujo", "")
        if fn and not re.search(rf"\b{re.escape(fn)}\s*\(", dispatch[v.value]):
            diffs.append(f"[fuai/fujo ] {v.name}: dispatch 分支不含 {fn}")

    # --- (c) .lom <-> fuai.h
    htxt = FUAI_H.read_text(encoding="utf-8")
    funcs = set(re.findall(r"\b(fuai_[a-z0-9_]+)\s*\(", htxt))
    for v in enum.variants:
        linux = v.meta.get("linux")
        if linux:
            if linux not in funcs:
                diffs.append(f"[fuai/linux] {v.name}: {linux} 不在 fuai.h")
        else:
            if v.meta.get("layer") == "core" and f"fuai_{v.name}" in funcs:
                diffs.append(f"[fuai/linux] {v.name}: .lom 未登记 linux 但 fuai.h 出现 fuai_{v.name}")

    # --- (d) 部署参数: 富士侧源码不得出现 Linux 侧取值 (陈旧注释检测)
    for p in mod.params:
        bind = PARAM_BINDINGS.get(p.name)
        if not bind:
            continue
        fujo_v, linux_v = p.values.get("fujo"), p.values.get("linux")
        if fujo_v is None or linux_v is None or fujo_v == linux_v:
            continue
        for f in bind["fujo_files"]:
            if not f.exists():
                continue
            for ln, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if not re.search(bind["alias"], line):
                    continue
                for m in INT_RE.finditer(line):
                    n = int(m.group(1))
                    if n == linux_v:
                        diffs.append(
                            f"[fuai/param] {f.name}:{ln}: 提到 {p.name}={n} 但富士侧应为 "
                            f"{fujo_v}（{n} 是 LinuxFUAI 部署值）— 陈旧数值"
                        )
    return len(enum.variants), len(prims)


def audit_fuc(diffs: list[str]) -> None:
    mod = lomc.load(LOM_FUC)
    node = next(r for r in mod.records if r.name == "Node")
    header = next(r for r in mod.records if r.name == "Header")
    py = lomc.emit_python(mod)

    def grab(name: str) -> str | None:
        m = re.search(rf"^{name} = (.+)$", py, re.MULTILINE)
        if not m:
            return None
        v = m.group(1).strip()
        if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
            v = v[1:-1]  # 去引号: 与源码里的字面串直接比对
        return v

    # tools/fuic.py 手写格式串
    fuic_txt = FUIC.read_text(encoding="utf-8")
    node_fmt = grab("NODE_FMT")
    header_fmt = grab("HEADER_FMT")
    if node_fmt and node_fmt not in fuic_txt:
        diffs.append(f"[fuc/layout] Node 格式串 {node_fmt} 未出现在 tools/fuic.py")
    if header_fmt and header_fmt not in fuic_txt:
        diffs.append(f"[fuc/layout] Header 格式串 {header_fmt} 未出现在 tools/fuic.py")

    # kernel/src/fui/fuc.rs 必须从生成模块取常量, 不能退回手写
    rs = FUC_RS.read_text(encoding="utf-8")
    if "pub use super::fuc_gen::*;" not in rs:
        diffs.append("[fuc/layout] fuc.rs 未 re-export fuc_gen (常量可能又变回手写)")
    # tools/fuic.py 的打包器必须引用生成模块, 不能退回手写 struct.pack
    if "spec_from_file_location" not in fuic_txt or "fuc.py" not in fuic_txt:
        diffs.append("[fuc/layout] fuic.py 未引用 lom/build/fuc.py (打包器可能退回手写)")
    if header.size != 48:
        diffs.append(f"[fuc/layout] Header.size={header.size} != 48")

    # kernel 侧生成物必须与 lomc 输出逐字节一致 (接管真源后, 漂移不可能)
    gen_rs = ROOT / "kernel" / "src" / "fui" / "fuc_gen.rs"
    want_rs = lomc.emit_rust(mod)
    if not gen_rs.exists():
        diffs.append("[fuc/kernel] kernel/src/fui/fuc_gen.rs 缺失")
    elif gen_rs.read_text(encoding="utf-8") != want_rs:
        diffs.append("[fuc/kernel] fuc_gen.rs 与 .lom 生成结果不一致 (重新生成即可)")


def audit_generated(diffs: list[str]) -> int:
    """所有登记生成物必须与 .lom 生成结果逐字节一致。"""
    cache: dict[Path, lomc.Module] = {}
    for lom, kind, path in GENERATED:
        if lom not in cache:
            cache[lom] = lomc.load(lom)
        want = lomc.EMITTERS[kind](cache[lom])
        if not path.exists():
            diffs.append(f"[gen        ] {path.relative_to(ROOT)} 缺失")
        elif path.read_text(encoding="utf-8") != want:
            diffs.append(f"[gen        ] {path.relative_to(ROOT)} 与 {lom.name} 生成结果不一致")
    return len(GENERATED)


def audit_fujr(diffs: list[str]) -> None:
    mod = lomc.load(LOM_FUJR)
    hdr = next(r for r in mod.records if r.name == "Header")
    sec = next(r for r in mod.records if r.name == "Section")
    if (hdr.size, sec.size) != (64, 32):
        diffs.append(f"[fujr       ] Header/Section = {hdr.size}/{sec.size}, 期望 64/32")
    # 内核侧仍认 FUJR 魔数
    rs = FUJR_RS.read_text(encoding="utf-8")
    if "FUJR" not in rs:
        diffs.append("[fujr       ] kernel/src/fujr.rs 未见 FUJR 魔数")
    # 宿主打包器必须引用生成模块
    pk = FUJPACK.read_text(encoding="utf-8")
    if "spec_from_file_location" not in pk or "fujr.py" not in pk:
        diffs.append("[fujr       ] fujopack.py 未引用 lom/build/fujr.py")


def audit_l1(diffs: list[str]) -> None:
    """L1 产物必须与 .lomt 转译结果一致 (docs/143)。"""
    import lomentc

    src = ROOT / "loment" / "examples" / "demo.lomt"
    if not src.exists():
        diffs.append("[l1         ] loment/examples/demo.lomt 缺失")
        return
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    for e in lomentc.check(mod, deps=deps):
        diffs.append(f"[l1         ] {e}")
    for path, want in (
        (ROOT / "loment" / "build" / "demo.rs", lomentc.emit_rust(mod, ROOT, deps)),
        (ROOT / "loment" / "build" / "native.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_agg.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_agg.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_str.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_str.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_slice.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_slice.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_mut.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_mut.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_gen.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_gen.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_trait.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_trait.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_res.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_res.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_mem.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_mem.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_bits.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_bits.lomt"), ROOT)),
        (ROOT / "loment" / "build" / "native_entry.ll",
         lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_entry.lomt"), ROOT)),
    ):
        if not path.exists():
            diffs.append(f"[l1         ] {path.relative_to(ROOT)} 缺失")
        elif path.read_text(encoding="utf-8") != want:
            diffs.append(f"[l1         ] {path.relative_to(ROOT)} 与 .lomt 转译结果不一致")

    # M45/M46: 每个示例必须有一份通过独立校验器的形式对象 (缺失/过期 = 门禁失败)
    import potato
    for src2 in sorted((ROOT / "loment" / "examples").glob("*.lomt")):
        obj = ROOT / "loment" / "build" / f"{src2.stem}.potato.json"
        m2 = lomentc.load(src2)
        want = lomentc.emit_potato(m2, ROOT, lomentc.resolve_deps(m2, ROOT, src2.parent, entry=src2))
        if not obj.exists():
            diffs.append(f"[l1         ] {obj.relative_to(ROOT)} 缺失 (M46 强制导出)")
            continue
        got = obj.read_text(encoding="utf-8")
        if got != want:
            diffs.append(f"[l1         ] {obj.relative_to(ROOT)} 与 .lomt 形式对象不一致")
            continue
        errs = potato.validate(json.loads(got))
        if errs:
            diffs.append(f"[l1         ] {obj.relative_to(ROOT)} 形式对象非法: {errs[0]}")

    # M50: A1–A4 断言表必须与形式对象一致
    import potato_assert
    cap = ROOT / "loment" / "build" / "cap_asserts.rs"
    want_cap = potato_assert.emit_rust(potato_assert.load_objects())
    if not cap.exists():
        diffs.append(f"[l1         ] {cap.relative_to(ROOT)} 缺失 (M50 断言绑定)")
    elif cap.read_text(encoding="utf-8") != want_cap:
        diffs.append(f"[l1         ] {cap.relative_to(ROOT)} 与形式对象不一致 (M50)")

    # M72: 系统调用层必须由 lom/fuai.lom 单源生成
    import loment_syscalls
    syscalls = ROOT / "loment" / "build" / "fuai_syscalls.lomt"
    want_sys = loment_syscalls.emit()
    if not syscalls.exists():
        diffs.append(f"[l1         ] {syscalls.relative_to(ROOT)} 缺失 (M72)")
    elif syscalls.read_text(encoding="utf-8") != want_sys:
        diffs.append(f"[l1         ] {syscalls.relative_to(ROOT)} 与 lom/fuai.lom 不一致 (M72)")

    # M79/M80/M81/M82: 自举 lexer/parser/checker/codegen 的形式对象必须与源码一致
    for stem in ("lexer", "parser", "checker", "codegen"):
        sh = ROOT / "loment" / "selfhost" / f"{stem}.lomt"
        sh_obj = ROOT / "loment" / "build" / f"selfhost_{stem}.potato.json"
        if not sh.exists():
            continue
        m3 = lomentc.load(sh)
        want_sh = lomentc.emit_potato(m3, ROOT,
                                      lomentc.resolve_deps(m3, ROOT, sh.parent, entry=sh))
        if not sh_obj.exists():
            diffs.append(f"[l1         ] {sh_obj.relative_to(ROOT)} 缺失 (自举 {stem})")
        elif sh_obj.read_text(encoding="utf-8") != want_sh:
            diffs.append(f"[l1         ] {sh_obj.relative_to(ROOT)} 与 selfhost/{stem}.lomt 不一致")

    # M93/M95/M99: 手册站点与发布清单必须与当前工件一致
    import loment_manual
    import loment_release
    for rel, text in loment_manual.build().items():
        p = ROOT / "docs" / "manual" / rel
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            diffs.append(f"[l1         ] docs/manual/{rel} 与编译器版本不一致 (M93)")
            break
    man = ROOT / "loment" / "build" / "release-manifest.json"
    want_rel = {x["path"]: x["sha256"] for x in loment_release.build()["files"]}
    if not man.exists():
        diffs.append(f"[l1         ] {man.relative_to(ROOT)} 缺失 (M95)")
    else:
        got_rel = {x["path"]: x["sha256"]
                   for x in json.loads(man.read_text(encoding="utf-8")).get("files", [])}
        bad = [k for k in want_rel if got_rel.get(k) != want_rel[k]]
        if bad or set(got_rel) != set(want_rel):
            diffs.append(f"[l1         ] release-manifest.json 与工件不一致 (M95): {bad[:2]}")


def main() -> int:
    diffs: list[str] = []
    try:
        n_prim, n_spec = audit_fuai(diffs)
        audit_fuc(diffs)
        audit_fujr(diffs)
        n_gen = audit_generated(diffs)
        audit_l1(diffs)
    except lomc.LomError as e:
        print(f"[ERR] .lom 解析失败: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] 审计异常: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print("LOM AUDIT v0  (.lom 单源 -> 实现)")
    print(f"  fuai: {n_prim} 原语 (spec.json 登记 {n_spec})")
    print("  fuc : Header 48B / Node 64B")
    print("  fujr: Header 64B / Section 32B")
    print(f"  gen : {n_gen} 个生成物逐字节对账")
    if diffs:
        print(f"\n[DIFF] {len(diffs)} 项:")
        for d in diffs:
            print("  " + d)
        return 1
    print("\n[OK] 0 差异 — 实现与 .lom 单源一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
