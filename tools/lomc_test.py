#!/usr/bin/env python3
# lomc_test.py — L0 编译器自检 (docs/141 §6 验收)
#
# 覆盖: 正例解析 / 六类语义错误拦截 / 生成确定性 / --check 漂移检出 /
#       生成物与既有实现逐字面一致 (fuc 布局的真值锚定)。
#
# 运行: python tools/lomc_test.py    (退出码 0 = 全绿)

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOM_FUC = ROOT / "lom" / "fuc.lom"
LOM_FUAI = ROOT / "lom" / "fuai.lom"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def parse_text(src: str) -> lomc.Module:
    return lomc.Parser(lomc.lex(src), src).parse()


def errs_of(src: str) -> list[str]:
    return lomc.check(parse_text(src))


# ---------------------------------------------------------------- 正例

@test
def test_real_schemas_parse_clean():
    for p in (LOM_FUC, LOM_FUAI):
        mod = lomc.load(p)
        assert lomc.check(mod) == [], f"{p.name} 语义错误: {lomc.check(mod)}"


@test
def test_fuai_covers_all_primitives():
    mod = lomc.load(LOM_FUAI)
    assert len(mod.enums) == 1 and mod.enums[0].name == "Opcode"
    assert len(mod.enums[0].variants) == 46, len(mod.enums[0].variants)
    assert len(mod.params) == 2


@test
def test_fuc_layout_is_64_and_48():
    mod = lomc.load(LOM_FUC)
    node = next(r for r in mod.records if r.name == "Node")
    header = next(r for r in mod.records if r.name == "Header")
    assert node.size == 64 and header.size == 48
    # 无空洞: 字段连续铺满 64B
    cur = 0
    for f in sorted(node.fields, key=lambda x: x.offset):
        assert f.offset == cur, f"{f.name} 期望 @{cur} 实得 @{f.offset}"
        cur += lomc.TYPES[f.type][1]
    assert cur == 64, cur


# ---------------------------------------------------------------- 负例: 语义检查必须拦截

@test
def test_duplicate_enum_value_rejected():
    e = errs_of('module m\nenum E : u16 { a = 1 b = 1 }\n')
    assert any("同值" in x for x in e), e


@test
def test_enum_value_out_of_range_rejected():
    e = errs_of('module m\nenum E : u8 { a = 256 }\n')
    assert any("超出" in x for x in e), e


@test
def test_overlapping_fields_rejected():
    e = errs_of('module m\nrecord R layout(packed, size=8) { a : u32 @0 b : u32 @2 }\n')
    assert any("重叠" in x for x in e), e


@test
def test_field_overrun_rejected():
    e = errs_of('module m\nrecord R layout(packed, size=4) { a : u32 @2 }\n')
    assert any("越过" in x for x in e), e


@test
def test_duplicate_top_level_name_rejected():
    e = errs_of('module m\nconst X : u16 = 1\nrecord X layout(packed, size=4) { a : u32 @0 }\n')
    assert any("重复" in x for x in e), e


@test
def test_unknown_type_rejected_at_parse():
    try:
        parse_text('module m\nrecord R layout(packed, size=4) { a : u24 @0 }\n')
    except lomc.LomError as ex:
        assert "未知字段类型" in ex.msg, ex.msg
    else:
        raise AssertionError("u24 应被拒绝")


@test
def test_missing_module_rejected():
    try:
        parse_text('enum E : u16 { a = 1 }\n')
    except lomc.LomError as ex:
        assert "module" in ex.msg, ex.msg
    else:
        raise AssertionError("缺 module 应被拒绝")


# ---------------------------------------------------------------- 生成确定性 + 真值锚定

@test
def test_generation_is_deterministic():
    mod = lomc.load(LOM_FUAI)
    for kind, fn in lomc.EMITTERS.items():
        assert fn(mod) == fn(mod), f"{kind} 生成不确定"


@test
def test_generated_fuc_fmt_matches_fuic():
    """生成格式串必须与 tools/fuic.py 手写字面串逐字符一致 (真值锚定)。"""
    py = lomc.emit_python(lomc.load(LOM_FUC))
    fuic = (ROOT / "tools" / "fuic.py").read_text(encoding="utf-8")
    for name in ("NODE_FMT", "HEADER_FMT"):
        m = re.search(rf"^{name} = '(.+)'$", py, re.MULTILINE)
        assert m, f"{name} 未生成"
        assert f'"{m.group(1)}"' in fuic, f"{name} {m.group(1)!r} 不在 fuic.py"


@test
def test_generated_fuc_consts_match_kernel():
    """生成常量必须与内核采用的常量一致 (fuc.rs 已改为 re-export fuc_gen)。"""
    mod = lomc.load(LOM_FUC)
    gen = (ROOT / "kernel" / "src" / "fui" / "fuc_gen.rs").read_text(encoding="utf-8")
    fuc_rs = (ROOT / "kernel" / "src" / "fui" / "fuc.rs").read_text(encoding="utf-8")
    want = {
        "MAGIC": next(c.value for c in mod.consts if c.name == "MAGIC"),
        "VERSION": next(c.value for c in mod.consts if c.name == "VERSION"),
        "NODE_SIZE": next(r.size for r in mod.records if r.name == "Node"),
    }
    for name, val in want.items():
        m = re.search(rf"pub const {name}: \w+ = ([0-9x_A-Fa-f]+)", gen)
        assert m, f"fuc_gen.rs 缺 {name}"
        got = int(m.group(1).replace("_", ""), 0)
        assert got == val, f"{name}: fuc_gen.rs={got} .lom={val}"
    assert "pub use super::fuc_gen::*;" in fuc_rs, "fuc.rs 未 re-export fuc_gen"


@test
def test_check_detects_drift_and_missing():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "gen.rs"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out)]) == 0
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out), "--check"]) == 0
            out.write_text("// corrupted\n", encoding="utf-8")
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out), "--check"]) == 1
            out.unlink()
            assert lomc.main([str(LOM_FUC), "--emit-rust", str(out), "--check"]) == 1


@test
def test_semantic_error_exit_code():
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.lom"
        bad.write_text("module m\nenum E : u8 { a = 1 b = 1 }\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomc.main([str(bad), "--print", "json"]) == 1


@test
def test_meta_parses_and_nests():
    src = 'module m\nmeta {\n  title = "t"\n  layers {\n    core = "c"\n    "host-ext" = "h"\n  }\n}\nconst X : u8 = 1\n'
    mod = parse_text(src)
    assert mod.meta is not None
    assert mod.meta.entries["title"] == "t"
    assert mod.meta.entries["layers"]["host-ext"] == "h"


@test
def test_meta_duplicate_key_rejected():
    try:
        parse_text('module m\nmeta { a = "1" a = "2" }\nconst X : u8 = 1\n')
    except lomc.LomError as ex:
        assert "重复" in ex.msg, ex.msg
    else:
        raise AssertionError("meta 重复键应被拒绝")


@test
def test_param_note_parsed():
    mod = lomc.load(LOM_FUAI)
    notes = {p.name: p.note for p in mod.params}
    assert notes["tau_low_default"] == "一致"
    assert "非接口差异" in notes["tau_high_default"]


@test
def test_spec_json_is_generated():
    """权威翻转: spec.json 必须等于 lom/fuai.lom 的生成结果 (docs/141 v1)。"""
    import lom_spec_emit as lse

    want = lse.emit(lomc.load(LOM_FUAI))
    for t in lse.TARGETS:
        assert t.exists(), f"{t} 缺失"
        assert t.read_text(encoding="utf-8") == want, f"{t} 与生成结果不一致"


@test
def test_fujr_schema_layout():
    mod = lomc.load(ROOT / "lom" / "fujr.lom")
    hdr = next(r for r in mod.records if r.name == "Header")
    sec = next(r for r in mod.records if r.name == "Section")
    assert (hdr.size, sec.size) == (64, 32)
    assert lomc._py_struct_format(hdr) == "<III52x"
    assert lomc._py_struct_format(sec) == "<I4xQQI4x"


@test
def test_fujr_byte_roundtrip():
    """用生成结构重打包既有 .run, 必须与原文件逐字节一致。"""
    import importlib.util as ilu

    spec = ilu.spec_from_file_location("t_fujr", ROOT / "lom" / "build" / "fujr.py")
    F = ilu.module_from_spec(spec)
    spec.loader.exec_module(F)
    src = ROOT / "sdk" / "build" / "m31_res.run"
    assert src.exists(), "缺少 m31_res.run 基准"
    b = src.read_bytes()
    _magic, ver, count = F.HEADER_STRUCT.unpack_from(b, 0)
    secs = [F.SECTION_STRUCT.unpack_from(b, F.HEADER_SIZE + i * F.SECTION_SIZE) for i in range(count)]
    out = bytearray(F.HEADER_STRUCT.pack(F.MAGIC, F.VERSION, count))
    off = F.HEADER_SIZE + F.SECTION_SIZE * count
    payload = bytearray()
    for tag, o, sz, fv in secs:
        al = (off + F.SECTION_ALIGN - 1) & ~(F.SECTION_ALIGN - 1)
        payload += b"\x00" * (al - off)
        off = al
        payload += b[o:o + sz]
        off += sz
        out += F.SECTION_STRUCT.pack(tag, al, sz, fv)
    out += payload
    assert bytes(out) == b, f"重打包不一致 ({len(out)} / {len(b)} B)"


@test
def test_fujopack_uses_generated():
    src = (ROOT / "tools" / "fujopack.py").read_text(encoding="utf-8")
    assert "spec_from_file_location" in src and "fujr.py" in src
    assert '"FUJR"' not in src, "打包器仍写死 FUJR 魔数"


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nlomc_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
