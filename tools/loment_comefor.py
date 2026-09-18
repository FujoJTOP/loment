#!/usr/bin/env python3
"""loment_comefor.py — `comefor` / `byuse` 的 **token 层展开**（S4.1，`docs/184` §1/§3.1/§3.2）。

## 它干什么

`comefor let "reg" to { <一段 Loment 程序> }` 定义一个**方言词**，从这个词到
`byuse "reg" done` 之间，它出现的地方由**那段程序在编译期跑**决定替换成什么。

展开**发生在 token 流上、解析之前** —— 所以 `Parser` 从头到尾看不见 `comefor`/`byuse`，
连 `elif` 链都不用加一个分支（`docs/184` §6）。这不是省事，是 `docs/184` §2 那条
"两边共处的层只有 token"逼出来的：自举的 parser 没接进管线。

## 宏体拿到什么（§3.2 定的四条契约）

    ct_n() -> u64                              游标起还有多少 token
    ct_tok(i: u64, buf: ptr) -> u64            源 token 的字段 -> buf；越界 -> 0
    ct_out(buf: ptr) -> u64                    buf -> 输出流（文本按跨度回源里查）
    ct_syn(k: u64, txt: ptr, ln: u64, line: u64, col: u64) -> u64   合成一个 token

记录 **20 字节**：`+0 kind(u32) +4 off(u32) +8 len(u32) +12 line(u32) +16 col(u32)`。
`off`/`len` 圈的是源里的**原始片段**（`"hello"` 的 len 是 7，不是 5）—— 与自举侧的
token 记录逐字段同序。

**吃多少**：宏体 `fn main() -> u64` 的返回值 = 吃掉的源 token 数，从游标起点算起
（§3.1 定的；显式报数，可审计）。

## 它不是什么

不做语义宏（§2）、不做嵌套（§1）、**吐出的 token 不再扫一遍**（否则 `reg` 吐 `reg`
就是死循环，而"不能再扫"是单趟扫描的直接推论 —— `docs/184` §1 说这套块不能嵌套，
这是同一件事的另一种说法）。**
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import loment_interp  # noqa: E402

#: token 记录的五个字段（20 字节，**与自举侧逐字段同序**）。
R_KIND, R_OFF, R_LEN, R_LINE, R_COL, REC = 0, 4, 8, 12, 16, 20

#: kind 的数字编码 —— **与自举侧 `lexer.lomt` 头那条注释同表**
#:（`kind: 0=ident 1=number 2=string 3=punct 4=eof`）。
KINDS = ("ident", "number", "string", "punct", "eof")

#: `ct_n` 能报的最大值（u32）。**与自举侧同式** —— 到没到尾靠它，不靠 `eof`。
_MAXU32 = 0xFFFFFFFF


class ComeforError(lomc.LomError):
    """`comefor` / `byuse` 上的错。复用 `LomError` 的形状（行/列 + 消息）。"""


def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "little")


def _put32(b: bytearray, o: int, v: int) -> None:
    b[o:o + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")


def _is_ident(t: lomc.Tok, name: str) -> bool:
    return t.kind == "ident" and t.val == name


class _SpanIndex:
    """源 token 的**跨度索引**：`(off, len) -> Tok`。

    `ct_out` 要按跨度把文本找回来 —— 这样"吐出的 token"与"源里那个 token"是同一个
    文本**和**同一个位置（`docs/184` §4 那条"位置是构造出来的"）。跨度是唯一的，
    所以这个映射没有歧义。
    """

    def __init__(self, toks: list) -> None:
        self.m: dict[tuple[int, int], lomc.Tok] = {}
        for t in toks:
            if t.kind != "eof":
                self.m.setdefault((t.off, t.len), t)

    def get(self, off: int, ln: int):
        return self.m.get((off, ln))


class _MacroInterp(loment_interp.Interp):
    """把 S4.0 的解释器接上**四个 token 内建**（覆写 `_builtin`，认不得的交回父类）。

    **为什么不写进 `loment_interp.py`**：那四个内建只在"展开"这个语境里有意义
    （要游标、要输出流），而 S4.0 的解释器是"跑一支程序、出一个值"，两者不同职。
    分开之后 S4.0 那条判据（两个解释器跑同一段程序同结果）仍然只考解释器本身。
    """

    def __init__(self, mod, ctx) -> None:
        super().__init__(mod)
        self.ctx = ctx

    def _builtin(self, n: str, a: list, line: int):
        r = ct_builtin(self.ctx, n, a)
        return super()._builtin(n, a, line) if r is None else r


class Macro:
    """一个定义好的方言词：它的名字、体（已解析成模块）、以及体在源里的位置。"""

    def __init__(self, word: str, mod, line: int, col: int) -> None:
        self.word = word
        self.mod = mod
        self.line = line
        self.col = col


def ct_builtin(ctx, n: str, a: list):
    """四个 token 内建。**认得就返回，不认得返回 `None`**（交回 `loment_interp`）。

    单独拎成一支是为了让**内建表只有一处** —— 自举侧那份镜像照着这一支写，
    于是"两边认得的名字、顺序、越界规则"都在一个地方看得见（`docs/182` 的消费方轴：
    内建表是宏体唯一看得见的接口，它漂了两边就编出不同的东西）。
    """
    if n == "ct_n":
        return ctx.n_rest() & _MAXU32
    if n == "ct_tok":
        return ctx.tok(a[0], a[1])
    if n == "ct_out":
        return ctx.out(a[0])
    if n == "ct_syn":
        return ctx.syn(a[0], a[1], a[2], a[3], a[4])
    return None


class Ctx:
    """一次展开的语境：游标（源 token 表里的下标）、源文本、输出流、以及跑它的解释器。

    `it` 与 `Ctx` 互为构造依赖（解释器要 ctx 才知道游标，游标要解释器才知道宿主内存），
    所以 `it` 由调用方在构造之后挂上 —— 挂之前这几个内建不能调，而它们只会在
    `call_fn` 里被调，那时 `it` 已经有值了。
    """

    def __init__(self, toks: list, text: str, cursor: int) -> None:
        self.toks = toks
        self.text = text
        self.cur = cursor
        self.it = None                 # 由 `_run` 在 `_MacroInterp` 造好之后挂上
        self.spans = _SpanIndex(toks)
        self.emit: list = []

    # ---------------------------------------------------------------- 读
    def n_rest(self) -> int:
        return max(0, len(self.toks) - self.cur)

    def tok(self, i: int, buf: int) -> int:
        """源 token（游标起第 `i` 个）的五个字段写进 `buf`。越界 -> 0。"""
        j = self.cur + i
        if i >= self.n_rest() or j < 0 or j >= len(self.toks):
            return 0
        t = self.toks[j]
        k = KINDS.index(t.kind) if t.kind in KINDS else 0
        self.it._chk(buf, REC)
        _put32(self.it.mem, buf + R_KIND, k)
        _put32(self.it.mem, buf + R_OFF, t.off)
        _put32(self.it.mem, buf + R_LEN, t.len)
        _put32(self.it.mem, buf + R_LINE, t.line)
        _put32(self.it.mem, buf + R_COL, t.col)
        return 1

    # ---------------------------------------------------------------- 吐
    def _rec(self, buf: int) -> tuple[int, int, int, int, int]:
        self.it._chk(buf, REC)
        m = self.it.mem
        return (_u32(m, buf + R_KIND), _u32(m, buf + R_OFF), _u32(m, buf + R_LEN),
                _u32(m, buf + R_LINE), _u32(m, buf + R_COL))

    def out(self, buf: int) -> int:
        """`buf` -> 输出流。

        **文本按跨度在源里查回来，位置取源里那个 token 的** —— 跨度定了，位置就定了
        （`docs/184` §3.2）。`kind` 取 `buf` 的：宏体可以把一个标识符变成关键字。
        跨度查不到任何源 token（宏体自己编的）-> **报错**，不用 `buf` 里的行列静默顶替。
        """
        k, off, ln, _line, _col = self._rec(buf)
        src = self.spans.get(off, ln)
        if src is None:
            raise ComeforError(0, 0,
                               f"`ct_out` 的跨度 ({off}, {ln}) 在源里没有对应的 token"
                               f" —— 要造新词请用 `ct_syn`（`docs/184` §3.2）")
        self.emit.append(lomc.Tok(KINDS[k] if k < len(KINDS) else "ident",
                                  src.val, src.line, src.col, off, ln))
        return 0

    def syn(self, k: int, txt: int, ln: int, line: int, col: int) -> int:
        """合成一个 token：文本取自**宏体自己的宿主内存**，位置由宏体填。"""
        self.it._chk(txt, ln)
        s = bytes(self.it.mem[txt:txt + ln]).decode("utf-8", "replace")
        # 合成 token **没有源跨度**（off/len 都 0）—— 它不在源里，所以 `ct_out` 的跨度
        # 查表找不到它。这是对的：合成本来就该走 `ct_syn`。
        self.emit.append(lomc.Tok(KINDS[k] if k < len(KINDS) else "ident",
                                  s, line, col, 0, 0))
        return 0


class _Expander:
    """一趟扫描：认定义、认终止、认使用。"""

    def __init__(self, toks: list, text: str) -> None:
        self.toks = toks
        self.text = text
        self.active: dict[str, Macro] = {}
        #: 定义过的方言 `{name, body}` —— 供 Potato v4 用（`docs/184` §9 S4.3）。
        #: 定义那一段会被抹掉，所以**只有在这里**它还存在；不进 Potato 的话，
        #: "这份产物用了哪些自定义语法"在**不读源码**的那一侧就看不见了。
        self.dialects: list[dict] = []

    def _body_text(self, lo: int, hi: int) -> str:
        """`[lo, hi)` 这段 token 在**源文本**里的那一片。

        空体给空串。取的是 `off`/`len`（源里的原始片段），不是解码后的值 ——
        与 §3.2 那条"记录圈的是原始片段"同一条纪律。
        """
        if hi <= lo:
            return ""
        a, b = self.toks[lo], self.toks[hi - 1]
        return self.text[a.off:b.off + b.len]

    # ---------------------------------------------------------------- 形状判定
    def _comefor_at(self, i: int):
        """`comefor let "词" to { … }` 的形状判定。不是这个形状就返回 `None`。

        **判形状而不是判词**（与 `_reject_nested_switch_decls` 同一条纪律）：
        `comefor` 不是保留字（`docs/158` §4 第 12 条），`let comefor: u32 = 1;` 合法，
        按词判会误伤。
        """
        t = self.toks
        if i + 4 >= len(t):
            return None
        if not (_is_ident(t[i], "comefor") and _is_ident(t[i + 1], "let")):
            return None
        if t[i + 2].kind != "string" or not _is_ident(t[i + 3], "to"):
            return None
        if t[i + 4].kind != "punct" or t[i + 4].val != "{":
            return None
        j = self._match_brace(i + 4)
        return t[i + 2].val, i + 5, j, j + 1

    def _byuse_at(self, i: int):
        """`byuse "词" done` 的形状判定。"""
        t = self.toks
        if i + 2 >= len(t):
            return None
        if not _is_ident(t[i], "byuse"):
            return None
        if t[i + 1].kind != "string" or not _is_ident(t[i + 2], "done"):
            return None
        return t[i + 1].val, i + 3

    def _match_brace(self, j: int) -> int:
        """`toks[j]` 是 `{`，返回配对的 `}` 的下标。"""
        d = 0
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == "punct" and t.val == "{":
                d += 1
            elif t.kind == "punct" and t.val == "}":
                d -= 1
                if d == 0:
                    return j
            j += 1
        raise ComeforError(self.toks[j - 1].line if j else 0, 0,
                           "`comefor` 的体没闭合（少一个 `}`）")

    # ---------------------------------------------------------------- 体 -> 模块
    def _body_module(self, lo: int, hi: int, line: int, col: int):
        """把 `[lo, hi)` 这段 token 当成**一支自足的程序**解析。

        **不支持 `use`**（这一档记在 `docs/184` §11）：宏体要帮手就**写在自己里面**
        —— 体是一支程序，可以有多个 `fn`，`fn main` 是入口。`use` 要的是依赖解析
        （相对路径 + 开关表），那是**文件级**的事，嵌进一段体里没有显然的语义。
        """
        import lomentc

        body = list(self.toks[lo:hi])
        if any(_is_ident(t, "use") for t in body):
            raise ComeforError(line, col,
                               "`comefor` 的体里暂时不支持 `use` —— 帮手 fn 写在体里面"
                               "（体是一支完整的程序）。见 `docs/184` §11")
        # `Parser` 要求文件以 `module` 开头，而体是**一段**、不是一份文件 —— 补一个
        # 合成头。名字固定（体是编译期的、不进任何产物），行号取自体的起点，于是
        # 万一它出现在诊断里也指得回 `comefor` 那一行。
        head = [lomc.Tok("ident", "module", line, col, 0, 0),
                lomc.Tok("ident", "_comefor_body", line, col, 0, 0)]
        body = head + body + [lomc.Tok("eof", "", line, col)]
        return lomentc.Parser(body, self.text).parse()

    # ---------------------------------------------------------------- 跑一个宏
    def _run(self, m: Macro, i: int):
        """在 `i`（方言词的下标）处展开。返回 `(吐出的 token, 吃到的下一个下标)`。"""
        entry = next((f for f in m.mod.funcs if f.name == "main" and not f.extern), None)
        if entry is None:
            raise ComeforError(m.line, m.col,
                               f"方言 {m.word!r} 的体里没有 `fn main() -> u64`"
                               f" —— 它是入口，返回值是**吃掉的 token 数**")
        # 入口**不收形参**：它要吃的东西全在游标上（`docs/184` §3.1）。
        # 不挡的话，症状是跑起来报 `undef`（形参没实参可绑），指在体里面 ——
        # 而真正的原因在签名上，指出来只要一句话。
        if entry.params:
            raise ComeforError(m.line, m.col,
                               f"方言 {m.word!r} 的 `fn main` 不收形参"
                               f"（实得 {len(entry.params)} 个）—— 要吃的东西在游标上，"
                               f"用 `ct_tok` 取")
        ctx = Ctx(self.toks, self.text, i + 1)
        it = _MacroInterp(m.mod, ctx)
        ctx.it = it
        try:
            n = it.call_fn(entry, [])
        except ComeforError:
            raise
        except Exception as e:  # noqa: BLE001
            import loment_interp

            if isinstance(e, loment_interp.InterpError):
                raise ComeforError(self.toks[i].line, self.toks[i].col,
                                   f"方言 {m.word!r} 的体没有跑成：{e.kind}") from e
            raise
        n = n & _MAXU32
        if n > ctx.n_rest():
            raise ComeforError(self.toks[i].line, self.toks[i].col,
                               f"方言 {m.word!r} 说它吃了 {n} 个 token，"
                               f"可游标后面只剩 {ctx.n_rest()} 个")
        return ctx.emit, i + 1 + n

    # ---------------------------------------------------------------- 主循环
    def run(self):
        out: list = []
        i = 0
        depth = 0
        n = len(self.toks)
        while i < n:
            t = self.toks[i]
            if t.kind == "punct" and t.val in "{[(":
                depth += 1
                out.append(t)
                i += 1
                continue
            if t.kind == "punct" and t.val in "}])":
                depth -= 1
                out.append(t)
                i += 1
                continue
            if t.kind == "ident":
                d = self._comefor_at(i)
                if d is not None:
                    # **定义必须在顶层**（`docs/184` §1："这套块不能嵌套"）。写在函数体里
                    # 定义出来的方言，作用域会从函数中间开始 —— 那不是"一个语法"，是
                    # 一个漏出来的全局开关，而 `byuse` 在文件后面收它，读的人看不出关系。
                    if depth != 0:
                        raise ComeforError(t.line, t.col,
                                           "`comefor` 只能写在**顶层** —— 写在块里定义出来的"
                                           "方言会从块中间开始生效，收它的 `byuse` 却在别处"
                                           "（`docs/184` §1）")
                    word, lo, hi, nxt = d
                    if word in self.active:
                        raise ComeforError(t.line, t.col,
                                           f"方言 {word!r} 已经定义过了"
                                           f"（上一处在第 {self.active[word].line} 行）"
                                           f" —— 这套块不能嵌套，也不能重定义")
                    self.active[word] = Macro(word, self._body_module(lo, hi, t.line, t.col),
                                              t.line, t.col)
                    self.dialects.append({"name": word, "body": self._body_text(lo, hi)})
                    i = nxt                  # 定义那一段整个抹掉
                    continue
                b = self._byuse_at(i)
                if b is not None:
                    word, nxt = b
                    if depth != 0:
                        raise ComeforError(t.line, t.col,
                                           "`byuse` 只能写在**顶层**（与 `comefor` 同层）")
                    if word not in self.active:
                        raise ComeforError(t.line, t.col,
                                           f"`byuse {word!r}` 收的不是任何在用的方言"
                                           f"（在用的：{sorted(self.active) or '没有'}）")
                    del self.active[word]
                    i = nxt                  # 终止那一段也整个抹掉
                    continue
                if t.val in self.active:
                    got, nxt = self._run(self.active[t.val], i)
                    out.extend(got)
                    i = nxt
                    continue
            out.append(t)
            i += 1
        return out


def expand(toks: list, text: str) -> tuple[list, list]:
    """`comefor`/`byuse` 的展开。

    返回 `(新的 token 表, 方言清单)` —— 与 `_apply_switches` 同形（在 token 层给一份新的），
    外加那份清单：定义那一段会被抹掉，**只有这一趟见过它**，而 Potato v4 要它
    （`docs/184` §9 S4.3）。清单**按名字排序**（确定性是判据，与 `SwitchTable.dump` 同）。

    没有 `comefor` 的文件要**逐 token 原样通过** —— 这条是"S4.1 不改变任何既有程序的
    产物"的保证，判据 `test_no_comefor_is_identity` 钉它。
    """
    ex = _Expander(toks, text)
    out = ex.run()
    return out, sorted(ex.dialects, key=lambda d: d["name"])
