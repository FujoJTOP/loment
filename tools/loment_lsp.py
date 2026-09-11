#!/usr/bin/env python3
# loment_lsp.py — Loment 语言服务 (M56, docs/148)
#
# 判据: 补全 / 跳转 / 诊断 三个能力可用。本文件实现最小 LSP (JSON-RPC over stdio):
#   initialize / textDocument/didOpen / didChange / definition / completion
# 用 `handle()` 直接驱动可做无编辑器自测 (见 loment_tools_test.py)。
#
# 用法:
#   python tools/loment_lsp.py            # 标准 LSP stdio 模式
#   python tools/loment_lsp.py --demo F   # 自测: 打开 F 并打印诊断/补全/跳转
# 退出码: 0 = OK / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import lomentc  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
KEYWORDS = ["fn", "let", "if", "else", "while", "for", "in", "match", "struct", "enum",
            "trait", "impl", "const", "return", "mut", "pub", "use", "module",
            "capability", "guard", "excluded", "interrupt", "as"]
TYPE_KEYWORDS = ["u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "bool", "str",
                 "ptr", "()", "Option", "Result"]


def _parse(text: str):
    return lomentc.Parser(lomc.lex(text), text).parse()


def _diagnostics(text: str, path: str) -> list[dict]:
    try:
        mod = _parse(text)
        errs = lomentc.check(mod)
    except lomc.LomError as e:
        return [{"range": {"start": {"line": max(0, e.line - 1), "character": 0},
                           "end": {"line": max(0, e.line - 1), "character": 200}},
                 "severity": 1, "source": "loment", "message": str(e)}]
    out = []
    for msg in errs:
        line = 0
        head = msg.split(":", 1)[0]
        if head.strip().isdigit():
            line = max(0, int(head.strip()) - 1)
            msg = msg.split(":", 1)[1].strip()
        out.append({"range": {"start": {"line": line, "character": 0},
                              "end": {"line": line, "character": 200}},
                    "severity": 1, "source": "loment", "message": msg})
    return out


def _decls(text: str) -> dict[str, dict]:
    """符号表: 名字 -> {kind, line}。"""
    mod = _parse(text)
    syms: dict[str, dict] = {}
    for f in mod.funcs:
        syms[f.name] = {"kind": "fn", "line": f.line - 1}
    for s in mod.structs:
        syms[s.name] = {"kind": "struct", "line": s.line - 1}
    for e in mod.enums:
        syms[e.name] = {"kind": "enum", "line": e.line - 1}
    for t in mod.traits:
        syms[t.name] = {"kind": "trait", "line": t.line - 1}
    for c in mod.consts:
        syms[c.name] = {"kind": "const", "line": c.line - 1}
    for c in mod.caps:
        syms[c.name] = {"kind": "capability", "line": c.line - 1}
    return syms


def handle(msg: dict, docs: dict[str, str]) -> list[dict]:
    """纯函数式 LSP 处理: 输入一条消息, 输出若干条待发送消息。"""
    method = msg.get("method", "")
    params = msg.get("params") or {}
    rid = msg.get("id")
    if method == "initialize":
        return [{"jsonrpc": "2.0", "id": rid, "result": {
            "capabilities": {"textDocumentSync": 1,
                             "definitionProvider": True,
                             "documentFormattingProvider": True,
                             "completionProvider": {"triggerCharacters": [".", ":"]}},
            "serverInfo": {"name": "loment-lsp", "version": "0.2"}}}]
    if method in ("textDocument/didOpen", "textDocument/didChange"):
        td = params.get("textDocument") or {}
        uri = td.get("uri", "")
        text = (params.get("text") or
                (td.get("text") if "text" in td else None) or
                ((params.get("contentChanges") or [{}])[-1].get("text")))
        if text is not None:
            docs[uri] = text
        return [{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                 "params": {"uri": uri, "diagnostics": _diagnostics(docs.get(uri, ""), uri)}}]
    if method == "textDocument/definition":
        uri = (params.get("textDocument") or {}).get("uri", "")
        pos = params.get("position") or {}
        text = docs.get(uri, "")
        lines = text.splitlines()
        ln = pos.get("line", 0)
        word = ""
        if 0 <= ln < len(lines):
            import re
            # 光标可能落在词中间 (编辑器就是这样报的): 先向左扩到词首, 再取整词
            line = lines[ln]
            ch = pos.get("character", 0)
            start = min(max(ch, 0), len(line))
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
                start -= 1
            m = re.match(r"[A-Za-z_]\w*", line[start:])
            if m:
                word = m.group(0)
        sym = _decls(text).get(word)
        if not sym:
            return [{"jsonrpc": "2.0", "id": rid, "result": None}]
        return [{"jsonrpc": "2.0", "id": rid, "result": {
            "uri": uri, "range": {"start": {"line": sym["line"], "character": 0},
                                  "end": {"line": sym["line"], "character": 200}}}}]
    if method == "textDocument/completion":
        uri = (params.get("textDocument") or {}).get("uri", "")
        items = [{"label": k, "kind": 14} for k in KEYWORDS]
        items += [{"label": t, "kind": 6} for t in TYPE_KEYWORDS]
        try:
            for name, sym in _decls(docs.get(uri, "")).items():
                items.append({"label": name,
                              "kind": {"fn": 3, "struct": 22, "enum": 13,
                                       "trait": 8, "const": 21, "capability": 6}
                                      .get(sym["kind"], 6)})
        except Exception:  # noqa: BLE001  编辑中的语法错误不影响补全
            pass
        return [{"jsonrpc": "2.0", "id": rid, "result": {"isIncomplete": False,
                                                         "items": items}}]
    if method == "textDocument/formatting":
        # M55/M56: 格式化由语言服务提供 (编辑器侧因此不需要自己起进程)
        uri = (params.get("textDocument") or {}).get("uri", "")
        src = docs.get(uri, "")
        try:
            import lomfmt  # noqa: PLC0415
            new = lomfmt.format_source(src)
        except Exception as e:  # noqa: BLE001  语法错误时不动文档
            return [{"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32603, "message": f"格式化失败: {e}"}}]
        if new == src:
            return [{"jsonrpc": "2.0", "id": rid, "result": []}]
        lines = src.split("\n")
        return [{"jsonrpc": "2.0", "id": rid, "result": [{
            "range": {"start": {"line": 0, "character": 0},
                      "end": {"line": len(lines), "character": 0}},
            "newText": new}]}]
    if method == "shutdown":
        return [{"jsonrpc": "2.0", "id": rid, "result": None}]
    if rid is not None:
        return [{"jsonrpc": "2.0", "id": rid, "result": None}]
    return []


def _serve() -> int:
    docs: dict[str, str] = {}
    while True:
        headers: dict[str, str] = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return 0
            line = line.strip()
            if not line:
                break
            k, _, v = line.decode("ascii", "replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        if n <= 0:
            continue
        msg = json.loads(sys.stdin.buffer.read(n).decode("utf-8"))
        for out in handle(msg, docs):
            body = json.dumps(out, ensure_ascii=False).encode("utf-8")
            sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
            sys.stdout.buffer.write(body)
            sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_lsp", description="Loment 语言服务")
    ap.add_argument("--demo", metavar="FILE", help="无编辑器自测")
    a = ap.parse_args(argv)
    if not a.demo:
        return _serve()
    p = Path(a.demo).resolve()
    text = p.read_text(encoding="utf-8")
    uri = p.as_uri()
    docs: dict[str, str] = {}
    print("== initialize ==")
    print(json.dumps(handle({"id": 1, "method": "initialize", "params": {}}, docs)[0]["result"],
                     ensure_ascii=False))
    print("== didOpen -> diagnostics ==")
    for m in handle({"method": "textDocument/didOpen",
                     "params": {"textDocument": {"uri": uri, "text": text}}}, docs):
        print(f"{len(m['params']['diagnostics'])} 条诊断")
    print("== completion ==")
    items = handle({"id": 2, "method": "textDocument/completion",
                    "params": {"textDocument": {"uri": uri}}}, docs)[0]["result"]["items"]
    print(f"{len(items)} 项: " + ", ".join(i["label"] for i in items[:10]) + " ...")
    print("== definition (第一处 fn 名) ==")
    ln = next(i for i, s in enumerate(text.splitlines()) if s.strip().startswith("fn "))
    col = text.splitlines()[ln].index("fn") + 3
    res = handle({"id": 3, "method": "textDocument/definition",
                  "params": {"textDocument": {"uri": uri},
                             "position": {"line": ln, "character": col}}}, docs)[0]["result"]
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
