#!/usr/bin/env python3
# loment_editors_test.py — "不用 VS Code" 的编辑器支持判据 (docs/157 §3.6)
#
# 两条:
#   1. **语法表不漂移**: editors/vim/syntax/loment.vim 里的关键字/类型词必须都在 VS Code 的
#      TextMate 语法里出现 (vim 侧是手写的, 最容易和语法源脱节);
#   2. **Vim 真能高亮**: 用无头 vim 打开一个 .lomt, 断言 filetype 与几个位置的语法组
#      (关键字/类型/函数名/字符串/注释) —— 不是"文件存在"这种纸面判据。
#
# 运行: python tools/loment_editors_test.py   (无 vim 时第 2 条 SKIP, 退出码 0)

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIM = ROOT / "editors" / "vim"
VIM_SYNTAX = VIM / "syntax" / "loment.vim"
VSCODE_GRAMMAR = ROOT / "editors" / "vscode" / "syntaxes" / "loment.tmLanguage.json"
TESTS: list[tuple[str, object]] = []

PROBE = """module probe

/// 文档注释
fn add(a: u32, b: u32) -> u32 {
    // 普通注释
    let s: str = "hi";
    return a;
}
"""


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _vim() -> str | None:
    return shutil.which("vim") or shutil.which("nvim")


@test
def test_vim_syntax_keywords_match_grammar():
    """vim 语法里的词必须都在 TextMate 语法里出现 —— 否则同一个词两边说法不一致。

    注意: 语法源把词分在**很多** scope 里 (declaration / `variable.language.self` /
    `constant.language` ...), 所以判据是"出现在语法源的任意模式里", 不是某个固定 scope。
    """
    vim_kw, vim_ty = set(), set()
    for line in VIM_SYNTAX.read_text(encoding="utf-8").splitlines():
        m = re.match(r"syn keyword (loment\w+)\s+(.*)$", line)
        if not m:
            continue
        group, words = m.group(1), m.group(2).split()
        if group in ("lomentKeyword", "lomentBool", "lomentSelf"):
            vim_kw.update(words)
        elif group in ("lomentType", "lomentBuiltin"):
            vim_ty.update(words)
    assert vim_kw and vim_ty, "没从 vim 语法里解析出词表 (文件格式变了?)"

    grammar_raw = VSCODE_GRAMMAR.read_text(encoding="utf-8")
    assert "loment" in grammar_raw, "TextMate 语法文件读不出来?"
    missing = sorted(w for w in (vim_kw | vim_ty)
                     if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])",
                                      grammar_raw))
    assert not missing, (
        f"vim 语法里的词在 TextMate 语法里找不到: {missing}\n"
        f"(要么语法源漏了它们, 要么 vim 侧多写了)")
    print(f"      vim ⊆ 语法源: 关键字 {len(vim_kw)} + 类型/内建 {len(vim_ty)} 个词都在语法里")


@test
def test_vim_highlights_lomt():
    """无头 vim: filetype 认成 loment, 且各类 token 落到对的语法组。

    命令里**只有字面量**: 把 editors/vim 拷成临时目录里的 `rtp/`, 以 cwd = 临时目录运行,
    于是是 `set rtp+=rtp` / `edit probe.lomt` / `writefile(g:o, 'out.txt')` —— 不拼任何路径。
    (顺带避开 `-S script`: Windows 的 vim 会等输入而挂住; 用一串 `-c` 最稳。)
    """
    vim = _vim()
    if not vim:
        print("      SKIP: 无 vim/nvim")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        shutil.copytree(VIM, td / "rtp")
        with (td / "probe.lomt").open("w", encoding="utf-8", newline="\n") as f:
            f.write(PROBE)
        # vim 对 `-c` 有**数量上限** (Too many "+command" arguments), 所以把命令用 `|` 串成
        # 两条; 命令里只有字面量 (全部相对 cwd = 临时目录)。
        setup = ("set rtp+=rtp | filetype plugin on | syntax on | edit probe.lomt | "
                 "let g:o = [] | call add(g:o, 'filetype=' . &filetype)")
        checks = (" | ".join([
            # (line, col) 都是 1-based, 按 PROBE 数出来
            "call add(g:o, 'kw=' . synIDattr(synID(4, 1, 1), 'name'))",       # fn
            "call add(g:o, 'ty=' . synIDattr(synID(4, 11, 1), 'name'))",      # u32
            "call add(g:o, 'fnname=' . synIDattr(synID(4, 4, 1), 'name'))",   # add
            "call add(g:o, 'cmt=' . synIDattr(synID(5, 5, 1), 'name'))",      # //
            "call add(g:o, 'doc=' . synIDattr(synID(3, 1, 1), 'name'))",      # ///
            "call add(g:o, 'str=' . synIDattr(synID(6, 18, 1), 'name'))",     # "hi"
            "call add(g:o, 'ret=' . synIDattr(synID(7, 5, 1), 'name'))",      # return
            "call writefile(g:o, 'out.txt')",
            "qa!",
        ]))
        args = [vim, "-N", "-u", "NONE", "-es", "--not-a-term",
                "-c", setup, "-c", checks]
        r = subprocess.run(args, capture_output=True, text=True, timeout=180,
                           shell=False, cwd=str(td))
        out = td / "out.txt"
        assert out.exists(), (f"vim 没写出结果 (rc={r.returncode}): "
                              f"{r.stdout[-200:]}{r.stderr[-200:]}")
        got = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines()
                   if "=" in ln)
    assert got.get("filetype") == "loment", got
    assert got["kw"] == "lomentKeyword", got
    assert got["ty"] == "lomentType", got
    assert got["ret"] == "lomentKeyword", got
    assert got["fnname"] == "lomentFuncName", got
    assert got["cmt"] == "lomentComment", got
    assert got["doc"] == "lomentDocComment", got
    assert got["str"] == "lomentString", got
    print("      无头 vim: filetype=loment; fn/u32/函数名/注释/文档注释/字符串/return 各就各位")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_editors_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
