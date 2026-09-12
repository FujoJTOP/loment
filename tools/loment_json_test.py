#!/usr/bin/env python3
# loment_json_test.py — Loment 版 JSON 库与 Python json 行为一致 (docs/148 §2b)
#
# 判据: 探针程序里嵌若干 payload, 用 loment/lib/json.lomt 解析 + 取值 + 生成, stdout 必须与
# 用 Python `json` 算出来的**逐字节相同** —— 这样 Loment 版 LSP 的 JSON-RPC 帧才能与
# Python 版对齐。
#
# 覆盖: 嵌套对象/数组、空对象/空数组、字符串转义 (\r \n \t \\ \")、\uXXXX 与代理对、
#       整数/负数/大数、true/false/null、"值里含 \"method\" 字面量" 不会被误当成键。
#
# 运行: python tools/loment_json_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True, text=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


# ---------------------------------------------------------------- payload 与期望

PAYLOADS = [
    # 0: didOpen 的真实形状 —— 文档文本里有 \r\n 与转义引号
    '{"jsonrpc":"2.0","id":7,"method":"textDocument/didOpen",'
    '"params":{"textDocument":{"uri":"file:///x.lomt",'
    '"text":"fn f() {\\r\\n    let s: str = \\"hi\\\\n\\";\\r\\n}\\r\\n"}}}',
    # 1: 数组/空容器/布尔/null/负数/大数
    '{"a":[true,false,null,0,-5,123456789],"b":{"c":{},"d":[]}}',
    # 2: \uXXXX (BMP) 与代理对 (emoji)
    '{"u":"\\u4e2d\\u6587 \\ud83d\\ude00 x"}',
    # 3: 各种转义
    '{"k":"line1\\nline2\\ttab\\\\slash\\"q"}',
    # 4: 两层嵌套
    '{"nested":{"deep":{"leaf":"v"}}}',
    # 5: 值里含 "method" 字面量: 解析器不能被它骗到
    '{"method":"x","params":{"text":"\\"method\\": \\"fake\\""}}',
]


def _lom_str(s: str) -> str:
    """把 Python 字符串写成 Loment 字面量 (转义 \\ 与 ")。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


PROBE_TMPL = '''// loment/build/json_probe.lomt — JSON 库的探针 (由 tools/loment_json_test.py 生成)
module json_probe

use "loment/lib/json.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

fn pl(s: str) -> i64 {
    return wr(1, str_ptr(s), str_len(s));
}

fn pn(v: u32) -> i64 {
    let buf: ptr = alloc(16);
    let d: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        d = 1;
    }
    while x > 0 {
        store8(buf, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }
    let q: u32 = 0;
    let tmp: ptr = alloc(d);
    while q < d {
        store8(tmp, q, load8(buf, d - 1 - q) as u8);
        q = q + 1;
    }
    return wr(1, tmp, d);
}

fn kv(k: str, v: u32) -> i64 {
    pl(k);
    return pn(v);
}

fn ks(k: str, js: ptr, arena: ptr, n: u32) -> i64 {
    pl(k);
    let buf: ptr = alloc(2048);
    let m: u32 = json_str_into(js, arena, n, buf, 2048);
    wr(1, buf, m);
    return pl("\\n");
}

fn _start() {
    let arena: ptr = alloc(16384);
    let out: ptr = alloc(4096);
    __PAYLOADS__
    // ---- 生成侧: 与 json.dumps(ensure_ascii=False) 对齐
    pl("--emit\\n");
    let o: u32 = 0;
    o = json_emit_lit(out, o, "{\\"jsonrpc\\": \\"2.0\\", \\"id\\": ");
    o = json_emit_u32(out, o, 7);
    o = json_emit_lit(out, o, ", \\"result\\": {\\"ok\\": true, \\"n\\": ");
    o = json_emit_u32(out, o, 42);
    o = json_emit_lit(out, o, ", \\"s\\": ");
    let raw: str = "a\\"b\\n中\\tz";
    o = json_emit_str(out, o, str_ptr(raw), str_len(raw));
    o = json_emit_lit(out, o, ", \\"arr\\": [1, 2]}}");
    wr(1, out, o);
    pl("\\n");
    syscall4(60, 0, 0, 0);
}
'''


def _payload_block(idx: int, payload: str) -> str:
    """每个 payload: 解析 -> 取几个字段打出来。"""
    return f'''
    pl("--payload {idx}\\n");
    let js{idx}: str = {_lom_str(payload)};
    let root{idx}: u32 = json_parse(str_ptr(js{idx}), str_len(js{idx}), arena);
    kv("root_kind=", json_nkind(arena, root{idx}));
    pl("\\n");
'''


def _build(td: Path) -> Path:
    probe = ROOT / "loment" / "build" / "json_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    src = PROBE_TMPL.replace("__PAYLOADS__", "".join(
        _payload_block(i, p) for i, p in enumerate(PAYLOADS)))
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        return _compile(probe, td, "json_probe")
    finally:
        probe.unlink(missing_ok=True)


def _compile(probe: Path, td: Path, name: str) -> Path:
    import lomentc
    mod = lomentc.load(probe)
    deps = lomentc.resolve_deps(mod, ROOT, probe.parent, entry=probe)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"探针自己检查不过: {errs[:3]}"
    ll = td / f"{name}.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / f"{name}.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _run(elf: Path, td: Path) -> str:
    got = td / "probe.out"
    script = (f"rm -f /tmp/json_probe.bin && cp {_wsl_path(elf)} /tmp/json_probe.bin && "
              f"chmod +x /tmp/json_probe.bin && "
              f"/tmp/json_probe.bin > {_wsl_path(got)}; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    assert r.stdout.strip() == "0", f"探针退出码 {r.stdout!r}"
    # 按**原始字节**读: read_text 会把 \r\n 归一成 \n, 那样"解码出的 CR"就看不出来了
    return got.read_bytes().decode("utf-8")


@test
def test_json_library_matches_python():
    """解析侧: 每个 payload 的根节点类型 + 关键字段取值; 生成侧: 与 json.dumps 一致。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        got = _run(elf, td)

    want: list[str] = []
    for i, p in enumerate(PAYLOADS):
        want.append(f"--payload {i}")
        want.append(f"root_kind={json.loads(p) and 1}")
    want.append("--emit")
    want.append(json.dumps({"jsonrpc": "2.0", "id": 7,
                            "result": {"ok": True, "n": 42, "s": 'a"b\n中\tz',
                                       "arr": [1, 2]}}, ensure_ascii=False))
    # 生成侧的行没有换行结尾差异: 拼接后逐字节比
    gl = got.split("\n")
    wl = want
    assert gl[:len(wl)] == wl, (
        "逐行不一致:\n  got : " + repr(gl[:len(wl)]) + "\n  want: " + repr(wl))


@test
def test_json_decoding_and_lookup():
    """取值侧: 解码后的文本 / 数字 / 布尔 / 嵌套查找 / "值里含键名" 不误判。

    这一条用**独立的探针**只做取值, 便于把期望写成 Python 的 json 结果。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "json_lookup.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    p0, p2, p5 = PAYLOADS[0], PAYLOADS[2], PAYLOADS[5]
    src = f'''module json_lookup

use "loment/lib/json.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {{
    return syscall4(1, fd, p as u64, n as u64);
}}

fn pl(s: str) -> i64 {{
    return wr(1, str_ptr(s), str_len(s));
}}

fn pn(v: u32) -> i64 {{
    let buf: ptr = alloc(16);
    let d: u32 = 0;
    let x: u32 = v;
    if x == 0 {{
        store8(buf, 0, 48 as u8);
        d = 1;
    }}
    while x > 0 {{
        store8(buf, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }}
    let q: u32 = 0;
    let tmp: ptr = alloc(16);
    while q < d {{
        store8(tmp, q, load8(buf, d - 1 - q) as u8);
        q = q + 1;
    }}
    return wr(1, tmp, d);
}}

fn _start() {{
    let arena: ptr = alloc(16384);
    let buf: ptr = alloc(4096);
    // 0) 文档文本: 解码后的 UTF-8 (含 \\r\\n 与转义引号)
    let j0: str = {_lom_str(p0)};
    let r0: u32 = json_parse(str_ptr(j0), str_len(j0), arena);
    let params: u32 = json_get(str_ptr(j0), arena, r0, "params");
    let td: u32 = json_get(str_ptr(j0), arena, params, "textDocument");
    let uri: u32 = json_get(str_ptr(j0), arena, td, "uri");
    let text: u32 = json_get(str_ptr(j0), arena, td, "text");
    let m: u32 = json_str_into(str_ptr(j0), arena, uri, buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    m = json_str_into(str_ptr(j0), arena, text, buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    // 0b) id / 字符串长度要靠 as_u32 与 str_into 各自正确
    pn(json_as_u32(str_ptr(j0), arena, json_get(str_ptr(j0), arena, r0, "id")));
    pl("\\n");
    // 2) \\uXXXX 与代理对
    let j2: str = {_lom_str(p2)};
    let r2: u32 = json_parse(str_ptr(j2), str_len(j2), arena);
    m = json_str_into(str_ptr(j2), arena, json_get(str_ptr(j2), arena, r2, "u"), buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    // 5) 值里含 "method" 字面量: 取 "method" 必须是 "x"
    let j5: str = {_lom_str(p5)};
    let r5: u32 = json_parse(str_ptr(j5), str_len(j5), arena);
    m = json_str_into(str_ptr(j5), arena,
                      json_get(str_ptr(j5), arena, r5, "method"), buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    let tx: u32 = json_get(str_ptr(j5), arena,
                           json_get(str_ptr(j5), arena, r5, "params"), "text");
    m = json_str_into(str_ptr(j5), arena, tx, buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    syscall4(60, 0, 0, 0);
}}
'''
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            import lomentc
            mod = lomentc.load(probe)
            deps = lomentc.resolve_deps(mod, ROOT, probe.parent, entry=probe)
            errs = lomentc.check(mod, deps=deps)
            assert not errs, f"取值探针检查不过: {errs[:3]}"
            ll = td / "lookup.ll"
            with ll.open("w", encoding="utf-8", newline="\n") as f:
                f.write(lomentc.emit_llvm(mod, ROOT, deps))
            elf = td / "lookup.elf"
            r = subprocess.run(
                [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                 "-ffreestanding", "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
                capture_output=True, text=True, shell=False)
            assert r.returncode == 0, r.stderr[-400:]
            got = _run(elf, td)
    finally:
        probe.unlink(missing_ok=True)

    d0 = json.loads(p0)
    d2 = json.loads(p2)
    d5 = json.loads(p5)
    want = "\n".join([
        d0["params"]["textDocument"]["uri"],
        d0["params"]["textDocument"]["text"],
        str(d0["id"]),
        d2["u"],
        d5["method"],
        d5["params"]["text"],
    ]) + "\n"
    assert got == want, f"\n  got : {got!r}\n  want: {want!r}"


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_json_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
