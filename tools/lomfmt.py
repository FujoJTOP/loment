#!/usr/bin/env python3
# lomfmt.py — Loment 格式化器 (M55, docs/148)
#
# 判据: 幂等 (格式化两次结果相同) + 语义保持 (格式化前后形式对象一致)。
# 实现: 复用 lomc 词法器 -> 多字符运算符合并 -> 缩进/空格规则重排。
# 不改变 token 序列的语义, 只改空白与换行。
#
# 用法:
#   python tools/lomfmt.py FILE [FILE...]          # 输出到 stdout
#   python tools/lomfmt.py --check FILE [FILE...]  # 只检查是否已格式化 (退出码 1 = 需格式化)
#   python tools/lomfmt.py --write FILE [FILE...]  # 原地写回
# 退出码: 0 = OK / 1 = --check 有差异 / 2 = 读取或词法错误。

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

# 需要合并的多字符运算符 (词法器逐字符产出 punct)
MERGE = {"->", "::", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>", "..", "=>"}
# 这些运算符两侧各留一个空格 (二元)
BINARY = {"+", "-", "*", "/", "%", "=", "==", "!=", "<", "<=", ">", ">=",
          "&&", "||", "&", "|", "^", "<<", ">>", "->", "=>"}
# 这些符号前不留空格
NO_SPACE_BEFORE = {",", ";", ")", "]", "}", ":", "::", ".", ".."}
# 这些符号后不留空格
NO_SPACE_AFTER = {"(", "[", ".", "::", "!", ".."}
# 顶层关键字 (用于顶层之间插空行)
TOP_KW = {"module", "use", "capability", "const", "struct", "enum", "trait", "impl",
          "fn", "excluded", "pub", "interrupt"}
INDENT = "    "


def merge_ops(toks: list[lomc.Tok]) -> list[lomc.Tok]:
    out: list[lomc.Tok] = []
    i = 0
    while i < len(toks):
        a = toks[i]
        if a.kind == "punct" and i + 1 < len(toks) and toks[i + 1].kind == "punct":
            two = a.val + toks[i + 1].val
            if two in MERGE:
                out.append(lomc.Tok("punct", two, a.line, a.col))
                i += 2
                continue
        out.append(a)
        i += 1
    return out


def _is_value_end(tok: lomc.Tok | None) -> bool:
    """上一个 token 是否结束一个值 (用于判断 & - * + 是一元还是二元)。"""
    if tok is None:
        return False
    if tok.kind in ("ident", "number", "string"):
        return tok.val not in ("let", "return", "if", "else", "while", "for", "in",
                               "match", "mut", "fn", "const", "guard", "as", "pub")
    return tok.val in (")", "]", "}")


_CONTROL = {"if", "while", "match", "for", "in", "return", "guard", "as"}


def _needs_space(pp: lomc.Tok | None, prev: lomc.Tok | None, cur: lomc.Tok) -> bool:
    if prev is None:
        return False
    pv, cv = prev.val, cur.val
    if cv in NO_SPACE_BEFORE or pv in NO_SPACE_AFTER:
        return False
    if cv in ("(", "["):
        if prev.kind in ("ident", "number", "string") and pv not in _CONTROL:
            return False  # 调用/下标/类型参数
        if pv in (")", "]"):
            return False
    if cv == "!" or pv == "!":
        return False
    if cv in BINARY or pv in BINARY:
        if cv in ("+", "-", "&") and not _is_value_end(prev):
            return False  # 一元
        if pv in ("+", "-", "&") and not _is_value_end(pp):
            return False  # 一元 (看更早的 token)
        return True
    return True


_TYPE_BODY_KW = {"struct", "enum", "trait", "match"}
_BRACE_KW = {"struct", "enum", "trait", "impl", "fn", "if", "while", "for", "match",
             "else", "capability"}


def _brace_kind(line_toks: list[lomc.Tok]) -> str:
    """当前 { 属于哪种块: type (逗号分隔的声明体) / match (臂列表) / code。"""
    for t in reversed(line_toks):
        if t.kind == "ident" and t.val in _TYPE_BODY_KW:
            return "match" if t.val == "match" else "type"
        if t.kind == "ident" and t.val in _BRACE_KW:
            return "code"
    return "code"


def format_source(src: str) -> str:
    toks = [t for t in merge_ops(lomc.lex(src)) if t.kind != "eof"]
    out: list[str] = []
    line: list[str] = []
    line_toks: list[lomc.Tok] = []
    stack: list[str] = []
    pp: lomc.Tok | None = None
    prev: lomc.Tok | None = None
    depth = 0
    after_close = False  # 上一个 token 是 } : 下一条语句另起一行
    last_top = ""

    def flush() -> None:
        nonlocal line, line_toks
        if line:
            out.append(INDENT * depth + "".join(line))
            line, line_toks = [], []

    for t in toks:
        v = t.val
        if after_close:
            after_close = False
            if v not in (";", ",", "else", ")", "]", ".", "}"):
                flush()
        # 顶层声明之间空行 (同类单行声明不插空行; 多行声明之间插空行)
        if depth == 0 and t.kind == "ident" and v in TOP_KW:
            flush()
            if out and out[-1] != "" and last_top and (last_top != v or out[-1] == "}"):
                out.append("")
            last_top = v
        if v == "{":
            kind = _brace_kind(line_toks)
            if _needs_space(pp, prev, t):
                line.append(" ")
            line.append("{")
            flush()
            stack.append(kind)
            depth += 1
            pp, prev = prev, t
            continue
        if v == "}":
            flush()
            depth = max(0, depth - 1)
            if stack:
                stack.pop()
            line.append("}")
            line_toks.append(t)
            pp, prev, after_close = prev, t, True
            continue
        if v == ";":
            line.append(";")
            flush()
            pp, prev = prev, t
            continue
        if v == ",":
            line.append(",")
            if stack and stack[-1] in ("type", "match"):
                flush()
            pp, prev = prev, t
            continue
        if line and _needs_space(pp, prev, t):
            line.append(" ")
        line.append(_render(t))
        line_toks.append(t)
        pp, prev = prev, t
    flush()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _render(t: lomc.Tok) -> str:
    if t.kind == "string":
        esc = t.val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    return t.val


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = "print"
    files: list[str] = []
    for a in argv:
        if a == "--check":
            mode = "check"
        elif a == "--write":
            mode = "write"
        else:
            files.append(a)
    if not files:
        print("用法: lomfmt.py [--check|--write] FILE...", file=sys.stderr)
        return 2
    bad = 0
    for f in files:
        p = Path(f)
        try:
            src = p.read_text(encoding="utf-8")
            want = format_source(src)
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] {f}: {e}", file=sys.stderr)
            return 2
        if mode == "print":
            sys.stdout.write(want)
        elif mode == "check":
            if src != want:
                bad += 1
                print(f"[DIFF] {f}: 需要格式化")
            else:
                print(f"[OK] {f}")
        else:
            if src != want:
                p.write_text(want, encoding="utf-8", newline="\n")
                print(f"[FMT] {f}")
            else:
                print(f"[OK] {f}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
