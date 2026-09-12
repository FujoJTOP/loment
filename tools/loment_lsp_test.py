#!/usr/bin/env python3
# loment_lsp_test.py — Loment 版语言服务 (loment/tools/lsp.lomt) 的协议判据
#
# 为什么: 开发者不该为了写 Loment 装 Python。这条测试用**真协议**驱动 Loment 版 LSP
# (经 WSL 跑 Linux ELF): 批量喂入带 Content-Length 分帧的 JSON-RPC, 解析它的输出帧,
# 覆盖 initialize / didOpen 诊断 / didChange / completion / definition / shutdown / exit。
#
# 用**文件重定向**而不是管道: WSL 互操作下管道会卡住 (`wsl -e` 的 stdin 要等 EOF), 喂文件
# 既确定又快。消息序列是预先定好的, 所以批量喂没有损失。
#
# 运行: python tools/loment_lsp_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import loment_lsp as PY_LSP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lsp.lomt"
TESTS: list[tuple[str, object]] = []

GOOD = ("module t\n\n/// 一个辅助函数\nfn helper() -> u32 {\n    return 1;\n}\n\n"
        "fn main() -> u32 {\n    return helper();\n}\n")
BAD = ("module t\n\nfn helper() -> u32 {\n    return 1;\n}\n\n"
       "fn main() -> u32 {\n    return missing_fn();\n}\n")
DUP = ("module t\n\nfn a() -> u32 {\n    return 1;\n}\n\n"
       "fn a() -> u32 {\n    return 2;\n}\n")


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


def _build(td: Path) -> Path:
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lsp.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lsp.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lsp.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-500:]
    return elf


def frame(msg: dict) -> bytes:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def parse_frames(raw: bytes) -> list[dict]:
    out: list[dict] = []
    i = 0
    while i < len(raw):
        j = raw.find(b"\r\n\r\n", i)
        if j < 0:
            break
        hdr = raw[i:j].decode("ascii", "replace")
        n = 0
        for line in hdr.splitlines():
            if line.lower().startswith("content-length:"):
                n = int(line.split(":", 1)[1].strip())
        body = raw[j + 4:j + 4 + n]
        if len(body) < n:
            break
        out.append(json.loads(body.decode("utf-8")))
        i = j + 4 + n
    return out


class Batch:
    """把一串消息喂给 LSP 二进制, 拿回按序的输出帧。"""

    def __init__(self, elf: Path, td: Path):
        self.elf = elf
        self.td = td

    def run(self, msgs: list[dict]) -> tuple[list[dict], int, str]:
        inp = self.td / "in.bin"
        inp.write_bytes(b"".join(frame(m) for m in msgs))
        for a in (["rm", "-f", "/tmp/loment_lsp.bin"],
                  ["cp", _wsl_path(self.elf), "/tmp/loment_lsp.bin"],
                  ["chmod", "+x", "/tmp/loment_lsp.bin"],
                  ["rm", "-f", "/tmp/loment_lsp_in.bin"],
                  ["cp", _wsl_path(inp), "/tmp/loment_lsp_in.bin"]):
            r = subprocess.run(["wsl", "-e", *a], capture_output=True, shell=False)
            assert r.returncode == 0, (a, r)
        r = subprocess.run(
            ["wsl", "-e", "bash", "-lc",
             "/tmp/loment_lsp.bin < /tmp/loment_lsp_in.bin > /tmp/loment_lsp_out.bin "
             "2>/tmp/loment_lsp_err.bin; echo -n $?"],
            capture_output=True, text=True, timeout=300, shell=False)
        rc = int(r.stdout.strip() or -1)
        raw = subprocess.run(["wsl", "-e", "cat", "/tmp/loment_lsp_out.bin"],
                             capture_output=True, shell=False).stdout
        err = subprocess.run(["wsl", "-e", "cat", "/tmp/loment_lsp_err.bin"],
                             capture_output=True, shell=False).stdout.decode("utf-8", "replace")
        return parse_frames(raw), rc, err


@test
def test_lsp_protocol_roundtrip():
    """一整套: initialize -> didOpen(好) -> didChange(坏) -> completion -> definition
    -> shutdown -> exit, 逐帧核对。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    uri = "file:///t.lomt"
    bline = BAD.splitlines().index("    return missing_fn();")
    hline = GOOD.splitlines().index("fn helper() -> u32 {")   # 引用在 GOOD 文档里查
    cline = GOOD.splitlines().index("    return helper();")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"method": "textDocument/didOpen", "params": {"textDocument":
                                                      {"uri": uri, "text": GOOD}}},
        {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        # 引用处 -> 声明处 (GOOD 文档: 第 8 行调用 helper, 声明在第 2 行)
        {"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": cline, "character": 12}}},
        {"method": "textDocument/didChange", "params": {
            "textDocument": {"uri": uri}, "contentChanges": [{"text": BAD}]}},
        # 未声明的词 -> null
        {"jsonrpc": "2.0", "id": 4, "method": "textDocument/definition",
         "params": {"textDocument": {"uri": uri},
                    "position": {"line": bline, "character": 12}}},
        {"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}},
        {"method": "exit"},
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        frames, rc, err = Batch(elf, td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    assert len(frames) == 7, f"应有 7 帧, 实得 {len(frames)}"

    init = frames[0]
    caps = init["result"]["capabilities"]
    assert init["id"] == 1 and caps["textDocumentSync"] == 1, init
    assert caps["definitionProvider"] is True, caps
    assert init["result"]["serverInfo"]["name"] == "loment-lsp", init

    d1 = frames[1]
    assert d1["method"] == "textDocument/publishDiagnostics", d1
    assert d1["params"]["uri"] == uri and d1["params"]["diagnostics"] == [], d1

    comp = frames[2]
    items = comp["result"]["items"]
    labels = {it["label"] for it in items}
    kinds = {it["label"]: it["kind"] for it in items}
    assert {"fn", "let", "u32", "helper", "main"} <= labels, sorted(labels)[:24]
    assert kinds["fn"] == 14 and kinds["u32"] == 6, kinds
    assert kinds["helper"] == 3 and kinds["main"] == 3, kinds
    py_items = PY_LSP.handle({"id": 9, "method": "textDocument/completion",
                              "params": {"textDocument": {"uri": uri}}},
                             {uri: GOOD})[0]["result"]["items"]
    py_words = {i["label"] for i in py_items if i["kind"] in (14, 6)}
    assert py_words <= labels, f"与 Python 版的关键字/类型词不一致: {sorted(py_words - labels)}"

    dfn = frames[3]["result"]
    assert dfn is not None and dfn["uri"] == uri, dfn
    assert dfn["range"]["start"]["line"] == hline, (dfn, hline)

    d2 = frames[4]
    dg = d2["params"]["diagnostics"]
    assert any(d["code"] == "E002" and d["range"]["start"]["line"] == bline for d in dg),         (bline, dg)
    assert all(d["severity"] == 1 and d["source"] == "loment" for d in dg), dg
    assert all(d["message"].startswith(d["code"]) for d in dg), dg

    assert frames[5]["result"] is None, frames[5]
    assert frames[6]["result"] is None, frames[6]
    print(f"      8 条消息 -> 7 帧全对 (干净 0 诊断 / 补全含 helper+main / 跳转引用->行 "
          f"{hline} / didChange 后 E002 @{bline} / 未声明词->null / shutdown null)")


@test
def test_lsp_diagnostics_match_selfhosted_checker():
    """诊断的**码与行号**来自自举 checker (与参考实现规则等价 63/63)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    cases = [
        ("dup", DUP, "E013", [i for i, l in enumerate(DUP.splitlines())
                                 if l.startswith("fn a")][-1], 1),   # 报的是**重复**的那个
        ("undeclared", BAD, "E002", BAD.splitlines().index("    return missing_fn();"), 1),
        ("clean", GOOD, None, 0, 0),
    ]
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    for name, text, _c, _l, _n in cases:
        msgs.append({"method": "textDocument/didOpen",
                     "params": {"textDocument": {"uri": f"file:///{name}.lomt",
                                                  "text": text}}})
    msgs += [{"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}},
             {"method": "exit"}]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        frames, rc, err = Batch(elf, td).run(msgs)
    assert rc == 0, f"LSP 退出码 {rc}: {err[-300:]}"
    diags = [f["params"]["diagnostics"] for f in frames[1:1 + len(cases)]]
    assert len(diags) == len(cases), frames
    for (name, _t, want_code, want_line, want_n), dg in zip(cases, diags):
        if want_code is None:
            assert not dg, (name, dg)
        else:
            assert any(d["code"] == want_code and d["range"]["start"]["line"] == want_line
                       for d in dg), (name, want_code, want_line, dg)
            assert len(dg) >= want_n, (name, want_n, dg)
        for d in dg:
            assert d["code"].startswith("E") and len(d["code"]) == 4, d
    print(f"      {len(cases)} 个用例的码/行号正确 (重名 E013 / 未声明 E002 / 干净 0 条)")


@test
def test_lsp_check_mode_is_python_free():
    """`lsp --check FILE` -> `路径:行:列: E0NN 标题`, 退出码 0/1 —— 编辑器任务与 CI 可用,
    且**不需要 Python** (与诊断共用同一套 checker 与文案表)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lsp_check_case.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(DUP + "\nfn m() -> u32 {\n    return nope();\n}\n",
                     encoding="utf-8", newline="\n")
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            for a in (["rm", "-f", "/tmp/loment_lsp.bin"],
                      ["cp", _wsl_path(elf), "/tmp/loment_lsp.bin"],
                      ["chmod", "+x", "/tmp/loment_lsp.bin"]):
                subprocess.run(["wsl", "-e", *a], capture_output=True, shell=False)
            # 把**绝对 WSL 路径**当数据传给 --check (不经 shell, 也不猜仓库在哪) ——
            # 早先这里写死了 `cd /mnt/d/Dev/FujoOS-FujoLang`, 换个检出位置就 rc=2
            # (服务打不开文件), 会被误读成"服务没构建"。
            abs_probe = _wsl_path(probe)
            r = subprocess.run(["wsl", "-e", "/tmp/loment_lsp.bin", "--check", abs_probe],
                               capture_output=True, text=True, timeout=180, shell=False)
            out = r.stdout
            assert r.returncode == 1, f"有错文件应当 rc=1, 实得 {r.returncode}: {out!r} {r.stderr[-200:]!r}"
            lines = [ln for ln in out.splitlines() if ": E" in ln]
            assert any("E013" in ln and ln.startswith(abs_probe) for ln in lines), out
            assert any("E002" in ln for ln in lines), out
            for ln in lines:
                head = ln.split(": E")[0]
                assert head.startswith(abs_probe + ":"), ln
                # 路径之后正好是 `:行:列` (两个冒号)
                assert head[len(abs_probe):].count(":") == 2, ln
            clean = _wsl_path(ROOT / "loment" / "examples" / "mathutil.lomt")
            r2 = subprocess.run(["wsl", "-e", "/tmp/loment_lsp.bin", "--check", clean],
                                capture_output=True, text=True, timeout=180, shell=False)
            assert r2.returncode == 0 and r2.stdout.strip() == "", (r2.returncode, r2.stdout)
    finally:
        probe.unlink(missing_ok=True)
    print("      --check: 有错 -> rc=1 且逐行 `路径:行:列: E0NN 标题`; 干净 -> rc=0 无输出")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_lsp_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
