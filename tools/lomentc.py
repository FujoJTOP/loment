#!/usr/bin/env python3
# lomentc.py — L1 Loment 编译器 v0 (docs/143)
#
# Loment = L0 接口层 (.lom 布局/契约) + 行为 (函数) + 能力声明。
# v0 策略 (docs/140 §4): 语法是 Rust 的严格子集 —— 不发明语法, 规避 LLM 零语料;
# 先转译到 Rust, 不写后端 —— 可行性判据 = 不写后端就能跑。
#
# 用法:
#   python tools/lomentc.py foo.lomt --emit-rust out.rs
#   python tools/lomentc.py foo.lomt --emit-potato out.json
#   python tools/lomentc.py foo.lomt --check          # 生成物与磁盘对账
#
# 退出码: 0 = 成功 / 1 = 语法或语义错误 / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402

Tok = lomc.Tok
LomError = lomc.LomError

INT_TYPES = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
TYPES = INT_TYPES + ("bool", "str")

# 内建函数: 名字 -> (参数类型, 返回类型) —— 由两后端各自降级 (M1/M2)
BUILTINS = {
    "str_len": (("str",), "u32"),
    "str_eq": (("str", "str"), "bool"),
    "str_byte": (("str", "u32"), "u32"),
}


def _rust_t(t: str) -> str:
    if t == "str":
        return "&'static str"
    if _is_slice(t):
        return f"&[{_slice_elem(t)}]"
    return t
BIN_OPS = (
    "||", "&&", "==", "!=", "<=", ">=", "<", ">",
    "|", "^", "&", "<<", ">>", "+", "-", "*", "/", "%",
)
UN_OPS = ("-", "!", "&")
# Rust 的运算符优先级 (数值越大结合越紧)
PRECEDENCE = {
    "||": 1, "&&": 2,
    "==": 3, "!=": 3,
    "<": 4, "<=": 4, ">": 4, ">=": 4,
    "|": 5, "^": 6, "&": 7,
    "<<": 8, ">>": 8,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
}

# ---------------------------------------------------------------- 语法树


def _is_array(t: str) -> bool:
    return t.startswith("[") and t.endswith("]") and ";" in t


def _is_slice(t: str) -> bool:
    """只读切片 `[T]` (M3): ptr + len 视图。"""
    return t.startswith("[") and t.endswith("]") and ";" not in t


def _slice_elem(t: str) -> str:
    return t[1:-1].strip()


def _array_elem(t: str) -> str:
    return t[1:t.rindex(";")].strip()


def _array_len(t: str) -> int:
    return int(t[t.rindex(";") + 1:-1].strip())


def _type_ok(t: str, known: set[str]) -> bool:
    """类型是否已声明: 基类型 / struct 名 / 由它们构成的数组或切片。"""
    if t in known:
        return True
    if _is_array(t):
        return _type_ok(_array_elem(t), known)
    if _is_slice(t):
        return _type_ok(_slice_elem(t), known)
    return False


@dataclass
class Capability:
    name: str
    space: str
    lo: int
    hi: int
    revocable: bool
    line: int


@dataclass
class Struct:
    name: str
    fields: list  # list[tuple[str, str]]
    line: int


@dataclass
class FieldAccess:
    obj: object
    name: str
    line: int


@dataclass
class StructLit:
    name: str
    inits: list  # list[tuple[str, object]]
    line: int


@dataclass
class Param:
    name: str
    type: str


@dataclass
class IntLit:
    value: int
    line: int


@dataclass
class BoolLit:
    value: bool
    line: int


@dataclass
class StrLit:
    value: str
    line: int


@dataclass
class Ident:
    name: str
    line: int


@dataclass
class Call:
    name: str
    args: list
    line: int


@dataclass
class Bin:
    op: str
    left: object
    right: object
    line: int


@dataclass
class Un:
    op: str
    expr: object
    line: int


@dataclass
class EnumDecl:
    name: str
    variants: list  # list[str]
    line: int
    payloads: dict = field(default_factory=dict)  # variant -> 载荷类型 (无载荷者不出现)


@dataclass
class EnumPath:
    enum: str
    variant: str
    line: int
    bind: str | None = None  # match 模式绑定名


@dataclass
class EnumCtor:
    enum: str
    variant: str
    arg: object
    line: int


@dataclass
class Match:
    subject: object
    arms: list  # list[tuple[Path | None, list]]  None = 通配 _
    line: int


@dataclass
class For:
    var: str
    lo: object
    hi: object
    body: list
    line: int


@dataclass
class ConstDecl:
    name: str
    type: str
    value: int
    line: int


@dataclass
class ArrayLit:
    items: list
    line: int


@dataclass
class Index:
    obj: object
    idx: object
    line: int


@dataclass
class Let:
    name: str
    type: str
    expr: object
    line: int


@dataclass
class Assign:
    target: object  # Ident | Index
    expr: object
    line: int


@dataclass
class If:
    cond: object
    then: list
    otherwise: list
    line: int


@dataclass
class While:
    cond: object
    body: list
    line: int


@dataclass
class Return:
    expr: object
    line: int


@dataclass
class ExprStmt:
    expr: object
    line: int


@dataclass
class Func:
    name: str
    params: list
    ret: str
    body: list
    line: int


@dataclass
class Module:
    name: str
    uses: list[str] = field(default_factory=list)      # use "*.lom"  (L0 布局)
    imports: list[str] = field(default_factory=list)   # use "*.lomt" (L1 模块)
    caps: list[Capability] = field(default_factory=list)
    structs: list[Struct] = field(default_factory=list)
    enums: list[EnumDecl] = field(default_factory=list)
    consts: list[ConstDecl] = field(default_factory=list)
    funcs: list[Func] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 语法分析

class Parser:
    """复用 lomc 的词法器; 只实现 L1 的顶层与语句/表达式。"""

    def __init__(self, toks: list[Tok], src: str):
        self.toks, self.src, self.i = toks, src, 0
        self.no_struct = 0  # >0 时禁止结构体字面量 (if/while 条件位置的歧义)

    # -- 基础
    def peek(self, k: int = 0) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind: str, val: str | None = None) -> bool:
        t = self.peek()
        return t.kind == kind and (val is None or t.val == val)

    def accept(self, kind: str, val: str | None = None) -> Tok | None:
        return self.next() if self.at(kind, val) else None

    def expect(self, kind: str, val: str | None = None, what: str = "") -> Tok:
        if not self.at(kind, val):
            t = self.peek()
            raise LomError(t.line, t.col, f"期望 {val or kind}{what}，得到 {t.val or '<eof>'!r}")
        return self.next()

    def ident(self, what: str = "标识符") -> str:
        return self.expect("ident", None, f"（{what}）").val

    def int_lit(self) -> int:
        neg = bool(self.accept("punct", "-"))
        t = self.expect("number", None, "（整数）")
        v = int(t.val, 16) if t.val[:2].lower() == "0x" else int(t.val, 10)
        return -v if neg else v

    def type_name(self) -> str:
        """类型名: 基类型 / 已声明 struct / 数组 [T; N] / 只读切片 [T]。"""
        if self.at("punct", "["):
            self.next()
            elem = self.type_name()
            if self.accept("punct", ";"):
                n = self.int_lit()
                self.expect("punct", "]")
                if n <= 0:
                    raise LomError(self.peek().line, self.peek().col, "数组长度必须为正")
                return f"[{elem}; {n}]"
            self.expect("punct", "]")
            return f"[{elem}]"
        return self.ident("类型名")

    # -- 顶层
    def parse(self) -> Module:
        self.expect("ident", "module", "（文件必须以 module 开头）")
        mod = Module(self.ident("模块名"))
        while not self.at("eof"):
            t = self.peek()
            if t.kind != "ident":
                raise LomError(t.line, t.col, f"顶层只允许 use/capability/fn，得到 {t.val!r}")
            if t.val == "use":
                self.next()
                p = self.expect("string", None, "（.lom 或 .lomt 路径）").val
                if p.endswith(".lomt"):
                    mod.imports.append(p)
                else:
                    mod.uses.append(p)
            elif t.val == "capability":
                mod.caps.append(self.parse_capability())
            elif t.val == "struct":
                mod.structs.append(self.parse_struct())
            elif t.val == "enum":
                mod.enums.append(self.parse_enum())
            elif t.val == "const":
                mod.consts.append(self.parse_const())
            elif t.val == "excluded":
                self.next()
                mod.excluded.append(self.expect("string", None, "（出界声明）").val)
            elif t.val == "fn":
                mod.funcs.append(self.parse_fn())
            else:
                raise LomError(t.line, t.col, f"未知顶层关键字 {t.val!r}")
        return mod

    def parse_struct(self) -> Struct:
        kw = self.expect("ident", "struct")
        name = self.ident("结构体名")
        self.expect("punct", "{")
        fields: list[tuple[str, str]] = []
        while not self.at("punct", "}"):
            fn = self.ident("字段名")
            self.expect("punct", ":")
            fields.append((fn, self.type_name()))
            if not self.accept("punct", ","):
                self.accept("punct", ";")
        self.expect("punct", "}")
        return Struct(name, fields, kw.line)

    def parse_enum(self) -> EnumDecl:
        """L1 枚举: 变体可带单载荷 (v1)。"""
        kw = self.expect("ident", "enum")
        name = self.ident("枚举名")
        self.expect("punct", "{")
        variants: list[str] = []
        payloads: dict[str, str] = {}
        while not self.at("punct", "}"):
            v = self.ident("变体名")
            variants.append(v)
            if self.accept("punct", "("):
                payloads[v] = self.type_name()
                self.expect("punct", ")")
            if not self.accept("punct", ","):
                self.accept("punct", ";")
        self.expect("punct", "}")
        return EnumDecl(name, variants, kw.line, payloads)

    def parse_const(self) -> ConstDecl:
        kw = self.expect("ident", "const")
        name = self.ident("常量名")
        self.expect("punct", ":")
        ty = self.type_name()
        self.expect("punct", "=")
        val = self.int_lit()
        self.expect("punct", ";")
        return ConstDecl(name, ty, val, kw.line)

    def parse_capability(self) -> Capability:
        kw = self.expect("ident", "capability")
        name = self.ident("能力名")
        self.expect("punct", ":")
        space = self.ident("能力空间")
        self.expect("punct", "[")
        lo = self.int_lit()
        self.expect("punct", ".")
        self.expect("punct", ".")
        hi = self.int_lit()
        self.expect("punct", "]")
        revocable = bool(self.accept("ident", "revocable"))
        return Capability(name, space, lo, hi, revocable, kw.line)

    def parse_fn(self) -> Func:
        kw = self.expect("ident", "fn")
        name = self.ident("函数名")
        self.expect("punct", "(")
        params: list[Param] = []
        while not self.at("punct", ")"):
            pn = self.ident("参数名")
            self.expect("punct", ":")
            params.append(Param(pn, self.type_name()))
            if not self.accept("punct", ","):
                break
        self.expect("punct", ")")
        self.expect("punct", "-")
        self.expect("punct", ">")
        ret = self.type_name()
        body = self.parse_block()
        return Func(name, params, ret, body, kw.line)

    def parse_block(self) -> list:
        self.expect("punct", "{")
        out = []
        while not self.at("punct", "}"):
            out.append(self.parse_stmt())
        self.expect("punct", "}")
        return out

    def parse_stmt(self):
        t = self.peek()
        if t.val == "let":
            self.next()
            name = self.ident("变量名")
            self.expect("punct", ":")
            ty = self.type_name()
            self.expect("punct", "=")
            e = self.parse_expr()
            self.expect("punct", ";")
            return Let(name, ty, e, t.line)
        if t.val == "if":
            self.next()
            self.no_struct += 1
            cond = self.parse_expr()
            self.no_struct -= 1
            then = self.parse_block()
            otherwise = []
            if self.at("ident", "else"):
                self.next()
                otherwise = self.parse_block()
            return If(cond, then, otherwise, t.line)
        if t.val == "while":
            self.next()
            self.no_struct += 1
            cond = self.parse_expr()
            self.no_struct -= 1
            return While(cond, self.parse_block(), t.line)
        if t.val == "for":
            self.next()
            var = self.ident("循环变量")
            self.expect("ident", "in")
            self.no_struct += 1
            lo = self.parse_expr()
            self.expect("punct", ".")
            self.expect("punct", ".")
            hi = self.parse_expr()
            self.no_struct -= 1
            return For(var, lo, hi, self.parse_block(), t.line)
        if t.val == "match":
            self.next()
            self.no_struct += 1
            subj = self.parse_expr()
            self.no_struct -= 1
            self.expect("punct", "{")
            arms = []
            while not self.at("punct", "}"):
                if self.at("ident", "_"):
                    self.next()
                    pat = None
                else:
                    en = self.ident("枚举名")
                    self.expect("punct", ":")
                    self.expect("punct", ":")
                    vn = self.ident("变体名")
                    bind = None
                    if self.accept("punct", "("):
                        bind = self.ident("绑定名")
                        self.expect("punct", ")")
                    pat = EnumPath(en, vn, t.line, bind)
                self.expect("punct", "=")
                self.expect("punct", ">")
                arms.append((pat, self.parse_block()))
            self.expect("punct", "}")
            return Match(subj, arms, t.line)
        if t.val == "return":
            self.next()
            e = self.parse_expr()
            self.expect("punct", ";")
            return Return(e, t.line)
        # 表达式语句或赋值 (左值: 标识符 / 下标)
        e = self.parse_expr()
        if self.at("punct", "="):
            self.next()
            rhs = self.parse_expr()
            self.expect("punct", ";")
            return Assign(e, rhs, t.line)
        self.expect("punct", ";")
        return ExprStmt(e, t.line)

    # -- 表达式 (优先级爬升)
    def parse_expr(self, min_prec: int = 0):
        left = self.parse_unary()
        while True:
            t = self.peek()
            if t.kind != "punct":
                break
            op = t.val
            nxt = self.peek(1)
            if nxt.kind == "punct" and nxt.val == "=" and t.val in ("=", "!", "<", ">"):
                op = t.val + "="
            elif nxt.kind == "punct" and nxt.val == t.val and t.val in ("&", "|", "<", ">"):
                op = t.val * 2
            if op not in PRECEDENCE or PRECEDENCE[op] < min_prec:
                break
            self.next()
            if len(op) == 2:
                self.next()
            right = self.parse_expr(PRECEDENCE[op] + 1)
            left = Bin(op, left, right, t.line)
        return left

    def parse_unary(self):
        t = self.peek()
        if t.kind == "punct" and t.val in UN_OPS:
            self.next()
            return Un(t.val, self.parse_unary(), t.line)
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.kind == "number":
            return IntLit(self.int_lit(), t.line)
        if t.kind == "string":
            return StrLit(self.next().val, t.line)
        if t.kind == "punct" and t.val == "(":
            self.next()
            e = self.parse_expr()
            self.expect("punct", ")")
            return self.parse_postfix(e)
        if t.kind == "punct" and t.val == "[":
            self.next()
            items = []
            while not self.at("punct", "]"):
                items.append(self.parse_expr())
                if not self.accept("punct", ","):
                    break
            self.expect("punct", "]")
            return self.parse_postfix(ArrayLit(items, t.line))
        if t.kind == "ident":
            if t.val == "true":
                self.next()
                return BoolLit(True, t.line)
            if t.val == "false":
                self.next()
                return BoolLit(False, t.line)
            name = self.next().val
            if self.at("punct", ":") and self.peek(1).kind == "punct" and self.peek(1).val == ":":
                self.next()
                self.next()
                vn = self.ident("变体名")
                if self.at("punct", "("):
                    self.next()
                    arg = self.parse_expr()
                    self.expect("punct", ")")
                    return EnumCtor(name, vn, arg, t.line)
                return EnumPath(name, vn, t.line)
            if self.at("punct", "("):
                self.next()
                args = []
                while not self.at("punct", ")"):
                    args.append(self.parse_expr())
                    if not self.accept("punct", ","):
                        break
                self.expect("punct", ")")
                return Call(name, args, t.line)
            if self.at("punct", "{") and self.no_struct == 0:
                self.next()
                inits: list[tuple[str, object]] = []
                while not self.at("punct", "}"):
                    fn = self.ident("字段名")
                    self.expect("punct", ":")
                    inits.append((fn, self.parse_expr()))
                    if not self.accept("punct", ","):
                        break
                self.expect("punct", "}")
                return StructLit(name, inits, t.line)
            return self.parse_postfix(Ident(name, t.line))
        raise LomError(t.line, t.col, f"期望表达式，得到 {t.val or '<eof>'!r}")

    def parse_postfix(self, e):
        """后缀: 字段访问 a.b / 下标 a[i]"""
        while True:
            if self.at("punct", ".") and self.peek(1).kind == "ident":
                t = self.next()
                e = FieldAccess(e, self.ident("字段名"), t.line)
            elif self.at("punct", "["):
                t = self.next()
                idx = self.parse_expr()
                self.expect("punct", "]")
                e = Index(e, idx, t.line)
            else:
                return e


# ---------------------------------------------------------------- 语义检查

def expr_type(e, scope: dict[str, str], funcs: dict[str, Func], structs: dict[str, Struct]) -> str | None:
    if isinstance(e, IntLit):
        return None  # 整型字面量: 由上下文定宽
    if isinstance(e, BoolLit):
        return "bool"
    if isinstance(e, StrLit):
        return "str"
    if isinstance(e, Ident):
        return scope.get(e.name)
    if isinstance(e, Call):
        if e.name in BUILTINS:
            return BUILTINS[e.name][1]
        if e.name == "slice_len":
            return "u32"
        return funcs[e.name].ret if e.name in funcs else None
    if isinstance(e, (EnumPath, EnumCtor)):
        return e.enum
    if isinstance(e, StructLit):
        return e.name if e.name in structs else None
    if isinstance(e, ArrayLit):
        if not e.items:
            return None
        et = None
        for it in e.items:
            t = expr_type(it, scope, funcs, structs)
            if t is not None:
                et = t
                break
        return f"[{et}; {len(e.items)}]" if et else None
    if isinstance(e, Index):
        ot = expr_type(e.obj, scope, funcs, structs)
        if ot and _is_array(ot):
            return _array_elem(ot)
        if ot and _is_slice(ot):
            return _slice_elem(ot)
        return None
    if isinstance(e, FieldAccess):
        ot = expr_type(e.obj, scope, funcs, structs)
        st = structs.get(ot) if ot else None
        if st is None:
            return None
        for fn, ft in st.fields:
            if fn == e.name:
                return ft
        return None
    if isinstance(e, Un):
        inner = expr_type(e.expr, scope, funcs, structs)
        if e.op == "&":  # M3: 数组 -> 只读切片
            return f"[{_array_elem(inner)}]" if inner and _is_array(inner) else None
        if e.op == "!":
            if inner == "bool":
                return "bool"
            if inner in INT_TYPES:
                return inner
            return None
        return inner
    if isinstance(e, Bin):
        lt = expr_type(e.left, scope, funcs, structs)
        rt = expr_type(e.right, scope, funcs, structs)
        if e.op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="):
            return "bool"
        return lt or rt
    return None


def _walk_expr(e, scope: dict[str, str], funcs: dict[str, Func], structs: dict[str, Struct],
               errs: list[str], enums: dict[str, EnumDecl] | None = None) -> None:
    """结构性问题 (未声明变量 / 未知函数 / 结构体字段 / 枚举变体) 的递归报告。"""
    enums = enums or {}
    if isinstance(e, (IntLit, BoolLit, StrLit)):
        return
    if isinstance(e, Ident):
        if e.name not in scope:
            errs.append(f"{e.line}: 使用未声明的变量 {e.name}")
        return
    if isinstance(e, EnumPath):
        if e.enum not in enums:
            errs.append(f"{e.line}: 未知枚举 {e.enum}")
        elif e.variant not in enums[e.enum].variants:
            errs.append(f"{e.line}: 枚举 {e.enum} 无变体 {e.variant}")
        return
    if isinstance(e, EnumCtor):
        ed = enums.get(e.enum)
        if ed is None:
            errs.append(f"{e.line}: 未知枚举 {e.enum}")
        elif e.variant not in ed.variants:
            errs.append(f"{e.line}: 枚举 {e.enum} 无变体 {e.variant}")
        elif e.variant not in ed.payloads:
            errs.append(f"{e.line}: 变体 {e.enum}::{e.variant} 无载荷，不能带参数")
        else:
            at = expr_type(e.arg, scope, funcs, structs)
            pt = ed.payloads[e.variant]
            if at is not None and at != pt:
                errs.append(f"{e.line}: 载荷类型 {at}，声明为 {pt}")
        _walk_expr(e.arg, scope, funcs, structs, errs, enums)
        return
    if isinstance(e, Call):
        if e.name == "slice_len":  # M3: 切片长度
            if len(e.args) != 1:
                errs.append(f"{e.line}: slice_len 需要 1 个实参，得到 {len(e.args)}")
            else:
                at = expr_type(e.args[0], scope, funcs, structs)
                if at is not None and not (_is_slice(at) or _is_array(at)):
                    errs.append(f"{e.line}: slice_len 实参 {at} 不是数组或切片")
            for a in e.args:
                _walk_expr(a, scope, funcs, structs, errs, enums)
            return
        if e.name in BUILTINS:
            want_t, _ret = BUILTINS[e.name]
            if len(e.args) != len(want_t):
                errs.append(f"{e.line}: 内建 {e.name} 需要 {len(want_t)} 个实参，得到 {len(e.args)}")
            else:
                for a, wt in zip(e.args, want_t):
                    at = expr_type(a, scope, funcs, structs)
                    if at is None:
                        if wt not in INT_TYPES:
                            errs.append(f"{e.line}: 内建 {e.name} 实参期望 {wt}，得到整型字面量")
                    elif at != wt:
                        errs.append(f"{e.line}: 内建 {e.name} 实参类型 {at}，期望 {wt}")
        elif e.name not in funcs:
            errs.append(f"{e.line}: 调用未定义的函数 {e.name}")
        else:
            want = len(funcs[e.name].params)
            if len(e.args) != want:
                errs.append(f"{e.line}: {e.name} 需要 {want} 个实参，得到 {len(e.args)}")
        for a in e.args:
            _walk_expr(a, scope, funcs, structs, errs, enums)
        return
    if isinstance(e, StructLit):
        st = structs.get(e.name)
        if st is None:
            errs.append(f"{e.line}: 未知结构体 {e.name}")
            return
        want = {n: t for n, t in st.fields}
        got: set[str] = set()
        for fn, fe in e.inits:
            if fn not in want:
                errs.append(f"{e.line}: 结构体 {e.name} 无字段 {fn}")
            elif fn in got:
                errs.append(f"{e.line}: 结构体 {e.name} 字段 {fn} 重复初始化")
            got.add(fn)
            _walk_expr(fe, scope, funcs, structs, errs, enums)
        for fn in want:
            if fn not in got:
                errs.append(f"{e.line}: 结构体 {e.name} 缺字段 {fn}")
        return
    if isinstance(e, FieldAccess):
        _walk_expr(e.obj, scope, funcs, structs, errs, enums)
        ot = expr_type(e.obj, scope, funcs, structs)
        st = structs.get(ot) if ot else None
        if st is None:
            errs.append(f"{e.line}: 对非结构体类型取字段 .{e.name}")
        elif not any(fn == e.name for fn, _ in st.fields):
            errs.append(f"{e.line}: 结构体 {ot} 无字段 {e.name}")
        return
    if isinstance(e, ArrayLit):
        if not e.items:
            errs.append(f"{e.line}: 数组字面量不能为空")
        ts = []
        for it in e.items:
            _walk_expr(it, scope, funcs, structs, errs, enums)
            ts.append(expr_type(it, scope, funcs, structs))
        concrete = {t for t in ts if t is not None}
        if len(concrete) > 1:
            errs.append(f"{e.line}: 数组字面量元素类型不一致: {sorted(concrete)}")
        elif concrete and any(t is None for t in ts) and not all(t in INT_TYPES for t in concrete):
            errs.append(f"{e.line}: 数组字面量元素类型不一致 (整型字面量混入 {sorted(concrete)[0]})")
        return
    if isinstance(e, Index):
        _walk_expr(e.obj, scope, funcs, structs, errs, enums)
        _walk_expr(e.idx, scope, funcs, structs, errs, enums)
        ot = expr_type(e.obj, scope, funcs, structs)
        if ot is None or not (_is_array(ot) or _is_slice(ot)):
            errs.append(f"{e.line}: 对非数组/切片类型取下标")
        it = expr_type(e.idx, scope, funcs, structs)
        if it is not None and it not in INT_TYPES:
            errs.append(f"{e.line}: 下标类型 {it}，应为整型")
        return
    if isinstance(e, Un):
        _walk_expr(e.expr, scope, funcs, structs, errs, enums)
        if e.op == "&":
            inner = expr_type(e.expr, scope, funcs, structs)
            if inner is None or not _is_array(inner):
                errs.append(f"{e.line}: & 只能作用于数组 (得到 {inner})")
        return
    if isinstance(e, Bin):
        _walk_expr(e.left, scope, funcs, structs, errs, enums)
        _walk_expr(e.right, scope, funcs, structs, errs, enums)
        return


def resolve_deps(mod: Module, root: Path, base: Path, entry: Path | None = None) -> list[Module]:
    """按依赖序返回导入的 L1 模块 (被依赖者在前), 去重 + 循环检测。"""
    order: list[Module] = []
    seen: set[Path] = set()
    stack: set[Path] = set()
    if entry is not None:
        rp = Path(entry).resolve()
        seen.add(rp)
        stack.add(rp)

    def visit(m: Module, cur_base: Path) -> None:
        for imp in m.imports:
            p = Path(imp)
            cand = p if p.is_absolute() else None
            if cand is None or not cand.exists():
                for base_try in (root, cur_base):
                    q = base_try / imp
                    if q.exists():
                        cand = q
                        break
            if cand is None or not cand.exists():
                raise LomError(1, 1, f"导入的 .lomt 不存在: {imp}")
            rp = cand.resolve()
            if rp in stack:
                raise LomError(1, 1, f"循环导入: {imp}")
            if rp in seen:
                continue
            seen.add(rp)
            stack.add(rp)
            sub = load(rp)
            visit(sub, rp.parent)
            order.append(sub)
            stack.discard(rp)

    visit(mod, base)
    return order


def check(mod: Module, ext_funcs: dict[str, Func] | None = None,
          deps: list[Module] | None = None) -> list[str]:
    errs: list[str] = []
    funcs = dict(ext_funcs or {})
    deps = deps or []

    # 依赖模块的导出符号: 先入符号表, 再检查本模块是否重名
    structs: dict[str, Struct] = {}
    enums: dict[str, EnumDecl] = {}
    const_scope: dict[str, str] = {}
    dep_names: set[str] = set()
    for d in deps:
        for f in d.funcs:
            funcs.setdefault(f.name, f)
            dep_names.add(f.name)
        for s in d.structs:
            structs.setdefault(s.name, s)
            dep_names.add(s.name)
        for e in d.enums:
            enums.setdefault(e.name, e)
            dep_names.add(e.name)
        for c in d.consts:
            const_scope.setdefault(c.name, c.type)
            dep_names.add(c.name)

    # 结构体: 名字/字段唯一, 类型已声明
    for s in mod.structs:
        if s.name in structs:
            errs.append(f"{s.line}: 结构体 {s.name} 重复定义")
        if s.name in TYPES:
            errs.append(f"{s.line}: 结构体 {s.name} 与基类型同名")
        seen_f: set[str] = set()
        for fn, _ft in s.fields:
            if fn in seen_f:
                errs.append(f"{s.line}: 结构体 {s.name} 字段 {fn} 重复")
            seen_f.add(fn)
        if not s.fields:
            errs.append(f"{s.line}: 结构体 {s.name} 为空")
        structs[s.name] = s
    # 枚举: 名字/变体唯一
    for e in mod.enums:
        if e.name in enums or e.name in structs:
            errs.append(f"{e.line}: 枚举 {e.name} 重复定义")
        if e.name in TYPES:
            errs.append(f"{e.line}: 枚举 {e.name} 与基类型同名")
        seen_v: set[str] = set()
        for v in e.variants:
            if v in seen_v:
                errs.append(f"{e.line}: 枚举 {e.name} 变体 {v} 重复")
            seen_v.add(v)
        if not e.variants:
            errs.append(f"{e.line}: 枚举 {e.name} 为空")
        enums[e.name] = e
    known = set(TYPES) | set(structs) | set(enums)
    for s in mod.structs:
        for fn, ft in s.fields:
            if not _type_ok(ft, known):
                errs.append(f"{s.line}: 结构体 {s.name}.{fn} 类型 {ft} 未声明")
    for e in mod.enums:
        for v, pt in e.payloads.items():
            if not _type_ok(pt, known):
                errs.append(f"{e.line}: 枚举 {e.name}::{v} 载荷类型 {pt} 未声明")

    # 常量 (仅整型)
    seen_const: set[str] = set()
    fnames = {f.name for f in mod.funcs}
    for c in mod.consts:
        if c.name in seen_const or c.name in structs or c.name in enums or c.name in fnames \
                or c.name in dep_names:
            errs.append(f"{c.line}: 常量 {c.name} 与既有声明重名")
        seen_const.add(c.name)
        if c.type not in INT_TYPES:
            errs.append(f"{c.line}: 常量 {c.name} 类型必须是整型，得到 {c.type}")
        const_scope[c.name] = c.type

    for f in mod.funcs:
        if f.name in funcs:
            errs.append(f"{f.line}: 函数 {f.name} 重复定义")
        funcs[f.name] = f
    seen_caps: set[str] = set()
    for c in mod.caps:
        if c.name in seen_caps:
            errs.append(f"{c.line}: 能力 {c.name} 重复声明")
        seen_caps.add(c.name)
        if c.lo > c.hi:
            errs.append(f"{c.line}: 能力 {c.name} 域下界 {c.lo} > 上界 {c.hi}")
        if c.lo < 0:
            errs.append(f"{c.line}: 能力 {c.name} 域下界为负")

    for f in mod.funcs:
        if not _type_ok(f.ret, known):
            errs.append(f"{f.line}: 函数 {f.name} 返回类型 {f.ret} 未声明")
        scope: dict[str, str] = dict(const_scope)  # 模块级常量在函数体内可见
        for p in f.params:
            if p.name in scope:
                errs.append(f"{f.line}: 函数 {f.name} 参数 {p.name} 重复")
            if not _type_ok(p.type, known):
                errs.append(f"{f.line}: 参数 {p.name} 类型 {p.type} 未声明")
            scope[p.name] = p.type

        def walk(stmts: list, fscope: dict[str, str]) -> None:
            for s in stmts:
                if isinstance(s, Let):
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    if not _type_ok(s.type, known):
                        errs.append(f"{s.line}: let {s.name} 类型 {s.type} 未声明")
                    if _is_array(s.type) and isinstance(s.expr, ArrayLit):
                        want = _array_len(s.type)
                        if want != len(s.expr.items):
                            errs.append(
                                f"{s.line}: 数组长度不符: 声明 {want}，字面量 {len(s.expr.items)}"
                            )
                    t = expr_type(s.expr, fscope, funcs, structs)
                    if t is not None and t != s.type:
                        errs.append(f"{s.line}: let {s.name}: {s.type} = 表达式类型 {t}")
                    fscope[s.name] = s.type
                elif isinstance(s, Assign):
                    _walk_expr(s.target, fscope, funcs, structs, errs, enums)
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    tt = expr_type(s.target, fscope, funcs, structs)
                    et = expr_type(s.expr, fscope, funcs, structs)
                    if isinstance(s.target, Ident) and s.target.name not in fscope:
                        errs.append(f"{s.line}: 赋值未声明的变量 {s.target.name}")
                    elif not isinstance(s.target, (Ident, Index)):
                        errs.append(f"{s.line}: 赋值目标不是左值")
                    if tt is not None and et is not None and tt != et:
                        errs.append(f"{s.line}: 赋值 {tt} = 表达式类型 {et}")
                elif isinstance(s, If):
                    _walk_expr(s.cond, fscope, funcs, structs, errs, enums)
                    ct = expr_type(s.cond, fscope, funcs, structs)
                    if ct is not None and ct != "bool":
                        errs.append(f"{s.line}: if 条件类型 {ct}，应为 bool")
                    walk(s.then, fscope)
                    walk(s.otherwise, fscope)
                elif isinstance(s, While):
                    _walk_expr(s.cond, fscope, funcs, structs, errs, enums)
                    ct = expr_type(s.cond, fscope, funcs, structs)
                    if ct is not None and ct != "bool":
                        errs.append(f"{s.line}: while 条件类型 {ct}，应为 bool")
                    walk(s.body, fscope)
                elif isinstance(s, For):
                    _walk_expr(s.lo, fscope, funcs, structs, errs, enums)
                    _walk_expr(s.hi, fscope, funcs, structs, errs, enums)
                    lt = expr_type(s.lo, fscope, funcs, structs)
                    ht = expr_type(s.hi, fscope, funcs, structs)
                    for bt, bw in ((lt, "下界"), (ht, "上界")):
                        if bt is not None and bt not in INT_TYPES:
                            errs.append(f"{s.line}: for {bw}类型 {bt}，应为整型")
                    body_scope = dict(fscope)
                    body_scope[s.var] = lt or ht or "u32"
                    walk(s.body, body_scope)
                elif isinstance(s, Match):
                    _walk_expr(s.subject, fscope, funcs, structs, errs, enums)
                    st = expr_type(s.subject, fscope, funcs, structs)
                    ed = enums.get(st) if st else None
                    if ed is None:
                        errs.append(f"{s.line}: match 主体类型 {st} 不是枚举")
                    seen_pat: set = set()
                    has_wild = False
                    for pat, body in s.arms:
                        arm_scope = dict(fscope)
                        if pat is None:
                            has_wild = True
                        else:
                            if ed is not None:
                                if pat.enum != ed.name:
                                    errs.append(
                                        f"{pat.line}: 模式 {pat.enum}::{pat.variant} 与主体枚举 {ed.name} 不符"
                                    )
                                elif pat.variant not in ed.variants:
                                    errs.append(f"{pat.line}: 枚举 {ed.name} 无变体 {pat.variant}")
                                elif pat.variant in ed.payloads and not pat.bind:
                                    errs.append(
                                        f"{pat.line}: 变体 {pat.enum}::{pat.variant} 有载荷，模式需绑定变量"
                                    )
                                elif pat.variant not in ed.payloads and pat.bind:
                                    errs.append(
                                        f"{pat.line}: 变体 {pat.enum}::{pat.variant} 无载荷，不能绑定"
                                    )
                                elif pat.bind:
                                    arm_scope[pat.bind] = ed.payloads[pat.variant]
                            key = (pat.enum, pat.variant)
                            if key in seen_pat:
                                errs.append(f"{pat.line}: 重复模式 {pat.enum}::{pat.variant}")
                            seen_pat.add(key)
                        walk(body, arm_scope)
                    if ed is not None and not has_wild:
                        missing = [v for v in ed.variants if (ed.name, v) not in seen_pat]
                        if missing:
                            errs.append(
                                f"{s.line}: match 不穷尽，缺 {', '.join(missing)}（或加 _ 通配）"
                            )
                elif isinstance(s, Return):
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    t = expr_type(s.expr, fscope, funcs, structs)
                    if t is not None and t != f.ret:
                        errs.append(f"{s.line}: return 类型 {t}，函数 {f.name} 声明 {f.ret}")
                elif isinstance(s, ExprStmt):
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)

        walk(f.body, scope)
    return errs


# ---------------------------------------------------------------- Rust 转译

BANNER = "// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。"


def _expr_rs(e) -> str:
    if isinstance(e, IntLit):
        return str(e.value)
    if isinstance(e, BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, Ident):
        return e.name
    if isinstance(e, StrLit):
        return json.dumps(e.value, ensure_ascii=False)  # Rust 字面量与 JSON 转义兼容
    if isinstance(e, Call) and (e.name in BUILTINS or e.name == "slice_len"):
        a = [_expr_rs(x) for x in e.args]
        if e.name == "slice_len":
            return f"({a[0]}.len() as u32)"
        if e.name == "str_len":
            return f"({a[0]}.len() as u32)"
        if e.name == "str_eq":
            return f"({a[0]} == {a[1]})"
        return f"({a[0]}.as_bytes()[({a[1]}) as usize] as u32)"
    if isinstance(e, Call):
        return f"{e.name}({', '.join(_expr_rs(a) for a in e.args)})"
    if isinstance(e, Un):
        return f"({e.op}{_expr_rs(e.expr)})"
    if isinstance(e, Bin):
        op = e.op
        if op in ("&&", "||"):
            op = op[0] * 2  # && / ||
        return f"({_expr_rs(e.left)} {op} {_expr_rs(e.right)})"
    if isinstance(e, EnumPath):
        return f"{e.enum}::{e.variant}"
    if isinstance(e, EnumCtor):
        return f"{e.enum}::{e.variant}({_expr_rs(e.arg)})"
    if isinstance(e, StructLit):
        inner = ", ".join(f"{k}: {_expr_rs(v)}" for k, v in e.inits)
        return f"{e.name} {{ {inner} }}"
    if isinstance(e, FieldAccess):
        return f"{_expr_rs(e.obj)}.{e.name}"
    if isinstance(e, ArrayLit):
        return "[" + ", ".join(_expr_rs(x) for x in e.items) + "]"
    if isinstance(e, Index):
        # Rust 要求下标为 usize; Loment 允许任意整型, 转译时显式降级。
        return f"{_expr_rs(e.obj)}[({_expr_rs(e.idx)}) as usize]"
    raise AssertionError(f"未知表达式 {e!r}")


def _stmts_rs(stmts: list, indent: int) -> list[str]:
    pad = "    " * indent
    out: list[str] = []
    for s in stmts:
        if isinstance(s, Let):
            out.append(f"{pad}let mut {s.name}: {_rust_t(s.type)} = {_expr_rs(s.expr)};")
        elif isinstance(s, Assign):
            out.append(f"{pad}{_expr_rs(s.target)} = {_expr_rs(s.expr)};")
        elif isinstance(s, If):
            out.append(f"{pad}if {_expr_rs(s.cond)} {{")
            out += _stmts_rs(s.then, indent + 1)
            if s.otherwise:
                out.append(f"{pad}}} else {{")
                out += _stmts_rs(s.otherwise, indent + 1)
            out.append(f"{pad}}}")
        elif isinstance(s, While):
            out.append(f"{pad}while {_expr_rs(s.cond)} {{")
            out += _stmts_rs(s.body, indent + 1)
            out.append(f"{pad}}}")
        elif isinstance(s, For):
            out.append(f"{pad}for {s.var} in {_expr_rs(s.lo)}..{_expr_rs(s.hi)} {{")
            out += _stmts_rs(s.body, indent + 1)
            out.append(f"{pad}}}")
        elif isinstance(s, Match):
            out.append(f"{pad}match {_expr_rs(s.subject)} {{")
            for pat, body in s.arms:
                head = "_" if pat is None else (
                    f"{pat.enum}::{pat.variant}({pat.bind})" if pat.bind
                    else f"{pat.enum}::{pat.variant}"
                )
                out.append(f"{pad}    {head} => {{")
                out += _stmts_rs(body, indent + 2)
                out.append(f"{pad}    }}")
            out.append(f"{pad}}}")
        elif isinstance(s, Return):
            out.append(f"{pad}return {_expr_rs(s.expr)};")
        elif isinstance(s, ExprStmt):
            out.append(f"{pad}{_expr_rs(s.expr)};")
        else:
            raise AssertionError(f"未知语句 {s!r}")
    return out


def _rust_body(mod: Module, lom_root: Path) -> list[str]:
    out: list[str] = []
    for u in mod.uses:
        p = (lom_root / u) if not Path(u).is_absolute() else Path(u)
        if not p.exists():
            raise LomError(1, 1, f"use 的 .lom 不存在: {u}")
        sub = lomc.load(p)
        errs = lomc.check(sub)
        if errs:
            raise LomError(1, 1, f"use 的 {u} 有语义错误: {errs[0]}")
        out.append(f"// ---- 来自 {u} (L0 布局单源) ----")
        out.append(lomc.emit_rust(sub).rstrip())
        out.append("")
    for c in mod.caps:
        up = c.name.upper()
        out.append(f"// capability {c.name}: {c.space}[{c.lo}..{c.hi}]" + (" revocable" if c.revocable else ""))
        out.append(f'pub const CAP_{up}_SPACE: &str = "{c.space}";')
        out.append(f"pub const CAP_{up}_LO: u64 = {c.lo};")
        out.append(f"pub const CAP_{up}_HI: u64 = {c.hi};")
        out.append(f"pub const CAP_{up}_REVOCABLE: bool = {'true' if c.revocable else 'false'};")
        out.append("")
    for c in mod.consts:
        out.append(f"pub const {c.name}: {c.type} = {c.value};")
    if mod.consts:
        out.append("")
    for e in mod.enums:
        out.append("#[derive(Clone, Copy, PartialEq)]")
        out.append(f"pub enum {e.name} {{")
        for v in e.variants:
            p = e.payloads.get(v)
            out.append(f"    {v}({p})," if p else f"    {v},")
        out.append("}")
        out.append("")
    for s in mod.structs:
        out.append("#[derive(Clone, Copy)]")
        out.append(f"pub struct {s.name} {{")
        for fn, ft in s.fields:
            out.append(f"    pub {fn}: {ft},")
        out.append("}")
        out.append("")
    for f in mod.funcs:
        args = ", ".join(f"{p.name}: {_rust_t(p.type)}" for p in f.params)
        out.append(f"pub fn {f.name}({args}) -> {_rust_t(f.ret)} {{")
        out += _stmts_rs(f.body, 1)
        out.append("}")
        out.append("")
    return out


def emit_rust(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    """依赖模块先出 (每个一次), 本模块在后 —— 单文件 Rust 产物。"""
    out = [BANNER, f"// module {mod.name} (loment v0 -> rust)"]
    for d in deps or []:
        out.append("")
        out.append(f"// ==== 导入模块 {d.name} ====")
        out += _rust_body(d, lom_root)
    out.append("")
    out.append(f"// ==== 本模块 {mod.name} ====")
    out += _rust_body(mod, lom_root)
    return "\n".join(out).rstrip() + "\n"


def emit_potato(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    layouts = []
    for u in mod.uses:
        p = (lom_root / u) if not Path(u).is_absolute() else Path(u)
        if not p.exists():
            continue
        sub = lomc.load(p)
        for r in sub.records:
            layouts.append(
                {
                    "name": r.name,
                    "size": r.size,
                    "endian": r.endian,
                    "packed": r.packed,
                    "fields": [
                        {"name": f.name, "type": f.type, "offset": f.offset}
                        for f in sorted(r.fields, key=lambda x: x.offset)
                    ],
                }
            )
    doc = {
        "potato": "v0",
        "unit": mod.name,
        "language": "loment",
        "imports": [d.name for d in (deps or [])],
        "capabilities": [
            {
                "name": c.name,
                "domain": {"space": c.space, "lo": c.lo, "hi": c.hi},
                "revocable": c.revocable,
            }
            for c in mod.caps
        ],
        "functions": [
            {
                "name": f.name,
                "params": [{"name": p.name, "type": p.type} for p in f.params],
                "ret": f.ret,
            }
            for f in mod.funcs
        ],
        "layouts": layouts,
        "consts": [{"name": c.name, "type": c.type, "value": c.value} for c in mod.consts],
        "enums": [
            {
                "name": e.name,
                "variants": list(e.variants),
                **({"payloads": dict(e.payloads)} if e.payloads else {}),
            }
            for e in mod.enums
        ],
        "types": [
            {"name": s.name, "fields": [{"name": fn, "type": ft} for fn, ft in s.fields]}
            for s in mod.structs
        ],
        "excluded": list(mod.excluded),
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------- LLVM IR 后端 (M0: 标量子集, docs/144)

IR_TYPES = {
    "u8": "i8", "u16": "i16", "u32": "i32", "u64": "i64",
    "i8": "i8", "i16": "i16", "i32": "i32", "i64": "i64", "bool": "i1",
}
_SIGNED = ("i8", "i16", "i32", "i64")


def _ir_t(t: str) -> str:
    if t not in IR_TYPES:
        raise LomError(1, 1, f"native M0 只支持标量类型，遇到 {t!r}")
    return IR_TYPES[t]


def _ll_type(t: str, structs: dict, enums: dict) -> str:
    """Loment 类型 -> LLVM 类型 (M23–M25: 标量 / struct / 定长数组 / 枚举; M1: str)。"""
    if t in IR_TYPES:
        return IR_TYPES[t]
    if t == "str":
        return "{ ptr, i64 }"  # 字节指针 + 长度 (UTF-8 视图)
    if _is_slice(t):
        et = _slice_elem(t)
        if et not in IR_TYPES:
            raise LomError(1, 1, f"native M3: 切片元素 {et} 暂只支持标量")
        return "{ ptr, i64 }"  # 元素指针 + 元素个数
    if t in structs:
        s = structs[t]
        for _, ft in s.fields:
            if ft not in IR_TYPES:
                raise LomError(s.line, 1, f"native M23: struct {t} 字段类型 {ft} 暂只支持标量")
        return "{ " + ", ".join(IR_TYPES[ft] for _, ft in s.fields) + " }"
    if t in enums:
        e = enums[t]
        if not e.payloads:
            return "i32"
        for pt in e.payloads.values():
            if pt not in IR_TYPES:
                raise LomError(e.line, 1, f"native M25: 枚举 {t} 载荷类型 {pt} 暂只支持标量")
        return "{ i32, i64 }"
    if _is_array(t):
        et = _array_elem(t)
        if et not in IR_TYPES:
            raise LomError(1, 1, f"native M24: 数组元素 {et} 暂只支持标量")
        return f"[{_array_len(t)} x {IR_TYPES[et]}]"
    raise LomError(1, 1, f"native 后端不支持类型 {t!r}")


def _collect_locals(f: Func, enums: dict | None = None) -> list[tuple[str, str]]:
    """按序收集需 alloca 的局部 (不含参数, 去重); For 变量与 match 绑定按上下文推断。"""
    enums = enums or {}
    out: list[tuple[str, str]] = []
    seen = {p.name for p in f.params}
    scope: dict[str, str] = {p.name: p.type for p in f.params}

    def walk(stmts: list) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.name not in seen:
                    out.append((s.name, s.type))
                    seen.add(s.name)
                scope[s.name] = s.type
            elif isinstance(s, If):
                walk(s.then)
                walk(s.otherwise)
            elif isinstance(s, While):
                walk(s.body)
            elif isinstance(s, For):
                vt = expr_type(s.lo, scope, {}, {}) or expr_type(s.hi, scope, {}, {}) or "u32"
                if s.var not in seen:
                    out.append((s.var, vt))
                    seen.add(s.var)
                scope[s.var] = vt
                walk(s.body)
            elif isinstance(s, Match):
                sty = expr_type(s.subject, scope, {}, {})
                ed = enums.get(sty) if sty else None
                for pat, body in s.arms:
                    if pat is not None and pat.bind and ed is not None:
                        pt = ed.payloads.get(pat.variant)
                        if pt and pat.bind not in seen:
                            out.append((pat.bind, pt))
                            seen.add(pat.bind)
                        if pt:
                            scope[pat.bind] = pt
                    walk(body)

    walk(f.body)
    return out


class _Ir:
    """结构化发射: alloca/load/store + 基本块; 优化交给 clang (docs/144 §3)。"""

    def __init__(self, funcs: dict, consts: dict, f: Func,
                 structs: dict | None = None, enums: dict | None = None):
        self.funcs, self.consts, self.f = funcs, consts, f
        self.structs = structs or {}
        self.enums = enums or {}
        self.out: list[str] = []
        self.tmp = 0
        self.lbl = 0
        self.vars: dict[str, tuple[str, str]] = {}  # 名 -> (loment 类型, alloca)
        self.globals: list[str] = []                # 字符串常量等模块级声明
        self.terminated = False

    def ll(self, t: str) -> str:
        return _ll_type(t, self.structs, self.enums)

    def t(self) -> str:
        self.tmp += 1
        return f"%t{self.tmp}"

    def l(self, tag: str) -> str:
        self.lbl += 1
        return f"L{self.lbl}_{tag}"

    def w(self, s: str) -> None:
        self.out.append("  " + s)

    def label(self, name: str) -> None:
        self.out.append(f"{name}:")
        self.terminated = False

    def jump(self, name: str) -> None:
        if not self.terminated:
            self.w(f"br label %{name}")
        self.terminated = True

    def type_scope(self) -> dict[str, str]:
        return {k: v[0] for k, v in self.vars.items()}

    # -- 表达式 -> (loment 类型, 值)
    def expr(self, e, want: str | None = None) -> tuple[str, str]:
        if isinstance(e, IntLit):
            return (want or "i32"), str(e.value)
        if isinstance(e, BoolLit):
            return "i1", ("1" if e.value else "0")
        if isinstance(e, Ident):
            if e.name in self.consts:  # 常量在生成期内联
                ty, val = self.consts[e.name]
                return ty, str(val)
            if e.name not in self.vars:
                raise LomError(e.line, 1, f"native M0: 未解析的变量 {e.name}")
            ty, ptr = self.vars[e.name]
            r = self.t()
            self.w(f"{r} = load {self.ll(ty)}, ptr {ptr}")
            return ty, r
        if isinstance(e, Un):
            if e.op == "&":  # M3: 数组 -> 只读切片值
                if not isinstance(e.expr, Ident):
                    raise LomError(e.line, 1, "native M3: & 只支持数组变量")
                ty, ptr = self.vars[e.expr.name]
                if not _is_array(ty):
                    raise LomError(e.line, 1, f"native M3: {ty} 不是数组")
                p0 = self.t()
                self.w(f"{p0} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 0")
                v1 = self.t()
                self.w(f"{v1} = insertvalue {{ ptr, i64 }} undef, ptr {p0}, 0")
                v2 = self.t()
                self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v1}, i64 {_array_len(ty)}, 1")
                return f"[{_array_elem(ty)}]", v2
            ty, v = self.expr(e.expr, want)
            it = _ir_t(ty)
            r = self.t()
            if e.op == "-":
                self.w(f"{r} = sub {it} 0, {v}")
            elif it == "i1":
                self.w(f"{r} = xor i1 {v}, true")
            else:
                self.w(f"{r} = xor {it} {v}, -1")
            return ty, r
        if isinstance(e, Bin):
            return self.binop(e, want)
        if isinstance(e, StrLit):
            b = e.value.encode("utf-8")
            name = f"@.str.{self.f.name}.{len(self.globals)}"
            esc = "".join(f"\\{x:02X}" for x in b)
            self.globals.append(
                f'{name} = private unnamed_addr constant [{len(b)} x i8] c"{esc}"'
            )
            p = self.t()
            self.w(f"{p} = getelementptr inbounds [{len(b)} x i8], ptr {name}, i64 0, i64 0")
            v = self.t()
            self.w(f"{v} = insertvalue {{ ptr, i64 }} undef, ptr {p}, 0")
            v2 = self.t()
            self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v}, i64 {len(b)}, 1")
            return "str", v2
        if isinstance(e, Call) and (e.name in BUILTINS or e.name == "slice_len"):
            return self.builtin(e)
        if isinstance(e, Call):
            fn = self.funcs.get(e.name)
            if fn is None:
                raise LomError(e.line, 1, f"native M0: 未知函数 {e.name}")
            if len(e.args) != len(fn.params):
                raise LomError(e.line, 1, f"native M0: {e.name} 实参个数不符")
            args = []
            for a, p in zip(e.args, fn.params):
                _, av = self.expr(a, p.type)
                args.append(f"{self.ll(p.type)} {av}")
            r = self.t()
            self.w(f"{r} = call {self.ll(fn.ret)} @{fn.name}({', '.join(args)})")
            return fn.ret, r
        if isinstance(e, FieldAccess):
            if not isinstance(e.obj, Ident):
                raise LomError(e.line, 1, "native M23: 只支持变量取字段")
            ty, ptr = self.vars[e.obj.name]
            st = self.structs.get(ty)
            if st is None:
                raise LomError(e.line, 1, f"native M23: {ty} 不是 struct")
            idx = next((i for i, (fn, _) in enumerate(st.fields) if fn == e.name), None)
            if idx is None:
                raise LomError(e.line, 1, f"native M23: struct {ty} 无字段 {e.name}")
            ft = st.fields[idx][1]
            gp = self.t()
            self.w(f"{gp} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 {idx}")
            r = self.t()
            self.w(f"{r} = load {self.ll(ft)}, ptr {gp}")
            return ft, r
        if isinstance(e, Index):
            if not isinstance(e.obj, Ident):
                raise LomError(e.line, 1, "native: 只支持变量取下标")
            ty, ptr = self.vars[e.obj.name]
            if _is_slice(ty):
                et = _slice_elem(ty)
                _, sv = self.expr(e.obj, ty)
                p = self.t()
                self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
                _, iv = self.expr(e.idx, "u32")
                g = self.t()
                self.w(f"{g} = getelementptr inbounds {self.ll(et)}, ptr {p}, i32 {iv}")
                r = self.t()
                self.w(f"{r} = load {self.ll(et)}, ptr {g}")
                return et, r
            if not _is_array(ty):
                raise LomError(e.line, 1, f"native M24: {ty} 不是数组")
            et = _array_elem(ty)
            _, iv = self.expr(e.idx, "u32")
            gp = self.t()
            self.w(f"{gp} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 {iv}")
            r = self.t()
            self.w(f"{r} = load {self.ll(et)}, ptr {gp}")
            return et, r
        if isinstance(e, EnumPath):
            ed = self.enums.get(e.enum)
            if ed is None or e.variant not in ed.variants:
                raise LomError(e.line, 1, f"native M25: 未知枚举变体 {e.enum}::{e.variant}")
            idx = ed.variants.index(e.variant)
            if not ed.payloads:
                return e.enum, str(idx)
            et = self.ll(e.enum)  # 带载荷枚举的无载荷变体: 只写 tag
            a = self.t()
            self.w(f"{a} = insertvalue {et} undef, i32 {idx}, 0")
            return e.enum, a
        if isinstance(e, EnumCtor):
            ed = self.enums.get(e.enum)
            if ed is None or e.variant not in ed.variants:
                raise LomError(e.line, 1, f"native M25: 未知枚举变体 {e.enum}::{e.variant}")
            pt = ed.payloads.get(e.variant)
            if pt is None:
                raise LomError(e.line, 1, f"native M25: {e.enum}::{e.variant} 无载荷")
            et = self.ll(e.enum)
            _, pv = self.expr(e.arg, pt)
            a = self.t()
            self.w(f"{a} = insertvalue {et} undef, i32 {ed.variants.index(e.variant)}, 0")
            if self.ll(pt) == "i64":
                b = self.t()
                self.w(f"{b} = insertvalue {et} {a}, i64 {pv}, 1")
            else:
                ext = "sext" if pt in _SIGNED else "zext"
                c = self.t()
                self.w(f"{c} = {ext} {self.ll(pt)} {pv} to i64")
                b = self.t()
                self.w(f"{b} = insertvalue {et} {a}, i64 {c}, 1")
            return e.enum, b
        raise LomError(getattr(e, "line", 1), 1, f"native 后端不支持该表达式: {type(e).__name__}")

    def binop(self, e: Bin, want: str | None) -> tuple[str, str]:
        scope = self.type_scope()
        ty = (expr_type(e.left, scope, self.funcs, {})
              or expr_type(e.right, scope, self.funcs, {}) or want or "u32")
        it = _ir_t(ty)
        signed = ty in _SIGNED
        op = e.op
        if op in ("&&", "||"):
            # M27: 短路 (phi) —— 右操作数仅在需要时求值
            _, a = self.expr(e.left, "bool")
            rhs_l, short_l, end_l = self.l("sc_rhs"), self.l("sc_short"), self.l("sc_end")
            if op == "&&":
                self.w(f"br i1 {a}, label %{rhs_l}, label %{short_l}")
            else:
                self.w(f"br i1 {a}, label %{short_l}, label %{rhs_l}")
            self.terminated = True
            self.label(rhs_l)
            _, b = self.expr(e.right, "bool")
            self.jump(end_l)
            self.label(short_l)
            self.jump(end_l)
            self.label(end_l)
            short_v = "false" if op == "&&" else "true"
            r = self.t()
            self.w(f"{r} = phi i1 [ {b}, %{rhs_l} ], [ {short_v}, %{short_l} ]")
            return "bool", r
        _, a = self.expr(e.left, ty)
        _, b = self.expr(e.right, ty)
        r = self.t()
        if op in ("==", "!="):
            self.w(f"{r} = icmp {'eq' if op == '==' else 'ne'} {it} {a}, {b}")
            return "bool", r
        if op in ("<", "<=", ">", ">="):
            m = {("<", False): "ult", ("<=", False): "ule", (">", False): "ugt", (">=", False): "uge",
                 ("<", True): "slt", ("<=", True): "sle", (">", True): "sgt", (">=", True): "sge"}[op, signed]
            self.w(f"{r} = icmp {m} {it} {a}, {b}")
            return "bool", r
        ins = {"+": "add", "-": "sub", "*": "mul", "&": "and", "|": "or", "^": "xor"}.get(op)
        if ins:
            self.w(f"{r} = {ins} {it} {a}, {b}")
            return ty, r
        if op in ("/", "%"):
            ins = ("sdiv" if signed else "udiv") if op == "/" else ("srem" if signed else "urem")
            self.w(f"{r} = {ins} {it} {a}, {b}")
            return ty, r
        if op in ("<<", ">>"):
            ins = "shl" if op == "<<" else ("ashr" if signed else "lshr")
            self.w(f"{r} = {ins} {it} {a}, {b}")
            return ty, r
        raise LomError(e.line, 1, f"native M0 不支持的运算符 {op}")

    # -- 内建 (M1/M2)
    def builtin(self, e: Call) -> tuple[str, str]:
        """str_len / str_eq / str_byte / slice_len 的 IR 降级。"""
        if e.name == "slice_len":
            at = expr_type(e.args[0], self.type_scope(), self.funcs, {})
            if at and _is_slice(at):
                _, sv = self.expr(e.args[0], at)
                p = self.t()
                self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 1")
            else:
                p = str(_array_len(at)) if at and _is_array(at) else "0"
            r = self.t()
            self.w(f"{r} = trunc i64 {p} to i32")
            return "u32", r
        if e.name == "str_len":
            _, sv = self.expr(e.args[0], "str")
            p = self.t()
            self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 1")
            r = self.t()
            self.w(f"{r} = trunc i64 {p} to i32")
            return "u32", r
        if e.name == "str_eq":
            _, a = self.expr(e.args[0], "str")
            _, b = self.expr(e.args[1], "str")
            ap, al = self.t(), self.t()
            self.w(f"{ap} = extractvalue {{ ptr, i64 }} {a}, 0")
            self.w(f"{al} = extractvalue {{ ptr, i64 }} {a}, 1")
            bp, bl = self.t(), self.t()
            self.w(f"{bp} = extractvalue {{ ptr, i64 }} {b}, 0")
            self.w(f"{bl} = extractvalue {{ ptr, i64 }} {b}, 1")
            le = self.t()
            self.w(f"{le} = icmp eq i64 {al}, {bl}")
            chk, neq, end = self.l("seq"), self.l("sneq"), self.l("send")
            self.w(f"br i1 {le}, label %{chk}, label %{neq}")
            self.terminated = True
            self.label(chk)
            c = self.t()
            self.w(f"{c} = call i32 @memcmp(ptr {ap}, ptr {bp}, i64 {al})")
            z = self.t()
            self.w(f"{z} = icmp eq i32 {c}, 0")
            self.jump(end)
            self.label(neq)
            self.jump(end)
            self.label(end)
            r = self.t()
            self.w(f"{r} = phi i1 [ {z}, %{chk} ], [ false, %{neq} ]")
            return "bool", r
        # str_byte
        _, sv = self.expr(e.args[0], "str")
        p = self.t()
        self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
        _, iv = self.expr(e.args[1], "u32")
        g = self.t()
        self.w(f"{g} = getelementptr inbounds i8, ptr {p}, i32 {iv}")
        b8 = self.t()
        self.w(f"{b8} = load i8, ptr {g}")
        r = self.t()
        self.w(f"{r} = zext i8 {b8} to i32")
        return "u32", r

    # -- 语句
    def block(self, stmts: list) -> None:
        for s in stmts:
            self.stmt(s)

    def stmt(self, s) -> None:
        if isinstance(s, Let) and isinstance(s.expr, StructLit):
            st = self.structs.get(s.type)
            if st is None:
                raise LomError(s.line, 1, f"native M23: {s.type} 不是 struct")
            ptr = self.vars[s.name][1]
            inits = dict(s.expr.inits)
            for i, (fn, ft) in enumerate(st.fields):
                gp = self.t()
                self.w(f"{gp} = getelementptr inbounds {self.ll(s.type)}, ptr {ptr}, i32 0, i32 {i}")
                _, v = self.expr(inits[fn], ft)
                self.w(f"store {self.ll(ft)} {v}, ptr {gp}")
            return
        if isinstance(s, Let) and isinstance(s.expr, ArrayLit):
            if not _is_array(s.type):
                raise LomError(s.line, 1, f"native M24: {s.type} 不是数组")
            et = _array_elem(s.type)
            ptr = self.vars[s.name][1]
            for i, it in enumerate(s.expr.items):
                gp = self.t()
                self.w(f"{gp} = getelementptr inbounds {self.ll(s.type)}, ptr {ptr}, i32 0, i32 {i}")
                _, v = self.expr(it, et)
                self.w(f"store {self.ll(et)} {v}, ptr {gp}")
            return
        if isinstance(s, Assign) and isinstance(s.target, Index):
            base = s.target.obj
            if not isinstance(base, Ident):
                raise LomError(s.line, 1, "native M24: 只支持数组变量取下标赋值")
            ty, ptr = self.vars[base.name]
            if not _is_array(ty):
                raise LomError(s.line, 1, f"native M24: {ty} 不是数组")
            et = _array_elem(ty)
            _, iv = self.expr(s.target.idx, "u32")
            gp = self.t()
            self.w(f"{gp} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 {iv}")
            _, v = self.expr(s.expr, et)
            self.w(f"store {self.ll(et)} {v}, ptr {gp}")
            return
        if isinstance(s, Match):
            sty, sv = self.expr(s.subject, None)
            ed = self.enums.get(sty)
            if ed is None:
                raise LomError(s.line, 1, f"native M26: match 主体 {sty} 不是枚举")
            if ed.payloads:
                tag = self.t()
                self.w(f"{tag} = extractvalue {self.ll(sty)} {sv}, 0")
            else:
                tag = sv
            end_l = self.l("mend")
            wild_l = end_l
            cases = []
            for pat, _ in s.arms:
                if pat is None:
                    wild_l = self.l("mwild")
                else:
                    cases.append((ed.variants.index(pat.variant), self.l(f"m{pat.variant}")))
            self.w(f"switch i32 {tag}, label %{wild_l} [")
            for idx, lbl in cases:
                self.w(f"    i32 {idx}, label %{lbl}")
            self.w("  ]")
            self.terminated = True
            for (pat, body), (_idx, lbl) in zip([a for a in s.arms if a[0] is not None], cases):
                self.label(lbl)
                if pat.bind:
                    pt = ed.payloads[pat.variant]
                    raw = self.t()
                    self.w(f"{raw} = extractvalue {self.ll(sty)} {sv}, 1")
                    bptr = self.vars[pat.bind][1]
                    if self.ll(pt) == "i64":
                        self.w(f"store i64 {raw}, ptr {bptr}")
                    else:
                        c = self.t()
                        self.w(f"{c} = trunc i64 {raw} to {self.ll(pt)}")
                        self.w(f"store {self.ll(pt)} {c}, ptr {bptr}")
                self.block(body)
                self.jump(end_l)
            if wild_l != end_l:
                self.label(wild_l)
                self.block(next(b for p, b in s.arms if p is None))
                self.jump(end_l)
            self.label(end_l)
            return
        if isinstance(s, Let):
            _, v = self.expr(s.expr, s.type)
            self.w(f"store {self.ll(s.type)} {v}, ptr {self.vars[s.name][1]}")
            return
        if isinstance(s, Assign):
            if not isinstance(s.target, Ident):
                raise LomError(s.line, 1, "native M0 只支持变量赋值（数组元素待 M1）")
            ty, ptr = self.vars[s.target.name]
            _, v = self.expr(s.expr, ty)
            self.w(f"store {self.ll(ty)} {v}, ptr {ptr}")
            return
        if isinstance(s, If):
            _, c = self.expr(s.cond, "bool")
            then_l, else_l, end_l = self.l("then"), self.l("else"), self.l("end")
            self.w(f"br i1 {c}, label %{then_l}, label %{else_l}")
            self.terminated = True
            self.label(then_l)
            self.block(s.then)
            self.jump(end_l)
            self.label(else_l)
            self.block(s.otherwise)
            self.jump(end_l)
            self.label(end_l)
            return
        if isinstance(s, While):
            cond_l, body_l, end_l = self.l("wcond"), self.l("wbody"), self.l("wend")
            self.jump(cond_l)
            self.label(cond_l)
            _, c = self.expr(s.cond, "bool")
            self.w(f"br i1 {c}, label %{body_l}, label %{end_l}")
            self.terminated = True
            self.label(body_l)
            self.block(s.body)
            self.jump(cond_l)
            self.label(end_l)
            return
        if isinstance(s, For):
            ty, ptr = self.vars[s.var]
            it = _ir_t(ty)
            _, lo = self.expr(s.lo, ty)
            self.w(f"store {it} {lo}, ptr {ptr}")
            cond_l, body_l, end_l = self.l("fcond"), self.l("fbody"), self.l("fend")
            self.jump(cond_l)
            self.label(cond_l)
            _, cv = self.expr(Ident(s.var, s.line), ty)
            _, hv = self.expr(s.hi, ty)
            r = self.t()
            self.w(f"{r} = icmp {'slt' if ty in _SIGNED else 'ult'} {it} {cv}, {hv}")
            self.w(f"br i1 {r}, label %{body_l}, label %{end_l}")
            self.terminated = True
            self.label(body_l)
            self.block(s.body)
            _, cur = self.expr(Ident(s.var, s.line), ty)
            nxt = self.t()
            self.w(f"{nxt} = add {it} {cur}, 1")
            self.w(f"store {it} {nxt}, ptr {ptr}")
            self.jump(cond_l)
            self.label(end_l)
            return
        if isinstance(s, Return):
            _, v = self.expr(s.expr, self.f.ret)
            self.w(f"ret {self.ll(self.f.ret)} {v}")
            self.terminated = True
            return
        if isinstance(s, ExprStmt):
            self.expr(s.expr, None)
            return
        raise LomError(getattr(s, "line", 1), 1, f"native M0 不支持该语句: {type(s).__name__}")


def _emit_ir_func(f: Func, funcs: dict, consts: dict,
                  structs: dict | None = None, enums: dict | None = None) -> tuple[list[str], str]:
    ir = _Ir(funcs, consts, f, structs, enums)
    args = ", ".join(f"{ir.ll(p.type)} %{p.name}" for p in f.params)
    ir.out.append(f"; {f.name} -> {f.ret}")
    ir.out.append(f"define {ir.ll(f.ret)} @{f.name}({args}) {{")
    for p in f.params:  # 参数与局部统一提升到入口块, 避免循环内反复分配
        ir.w(f"%{p.name}.addr = alloca {ir.ll(p.type)}")
        ir.vars[p.name] = (p.type, f"%{p.name}.addr")
    for name, ty in _collect_locals(f, enums):
        ir.w(f"%{name}.addr = alloca {ir.ll(ty)}")
        ir.vars[name] = (ty, f"%{name}.addr")
    for p in f.params:
        ir.w(f"store {ir.ll(p.type)} %{p.name}, ptr %{p.name}.addr")
    ir.block(f.body)
    if not ir.terminated:
        ir.w("unreachable")  # ponytail: 语言不强制全路径 return (docs/144 §3)
    ir.out.append("}")
    return ir.globals, "\n".join(ir.out)


def emit_llvm(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    """原生后端: LLVM IR。M0 标量 + M23–M26 聚合 (docs/144/145)。"""
    mods = list(deps or []) + [mod]
    funcs: dict[str, Func] = {}
    consts: dict[str, tuple[str, int]] = {}
    structs: dict[str, Struct] = {}
    enums: dict[str, EnumDecl] = {}
    for m in mods:
        for s in m.structs:
            structs[s.name] = s
        for e in m.enums:
            enums[e.name] = e
        for c in m.consts:
            consts[c.name] = (c.type, c.value)
        for f in m.funcs:
            funcs[f.name] = f
    for m in mods:
        for f in m.funcs:
            if f.ret not in IR_TYPES and f.ret != "str" and not _is_slice(f.ret):
                raise LomError(f.line, 1, f"native: 暂不支持聚合返回值 ({f.name}: {f.ret})")
            for p in f.params:
                if p.type not in IR_TYPES and p.type != "str" and not _is_slice(p.type):
                    raise LomError(f.line, 1, f"native: 暂不支持聚合参数 ({f.name}.{p.name}: {p.type})")
            for _, ty in _collect_locals(f, enums):
                _ll_type(ty, structs, enums)
    out = [
        "; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)",
        "; clang -O1 driver.c this.ll -o exe",
        "",
    ]
    globals_: list[str] = []
    body: list[str] = []
    for m in mods:
        for f in m.funcs:
            g, text = _emit_ir_func(f, funcs, consts, structs, enums)
            globals_ += g
            body.append(text)
    text_all = "\n".join(body)
    if "@memcmp" in text_all:  # 仅在实际用到时声明 (M2)
        out.append("declare i32 @memcmp(ptr, ptr, i64)")
        out.append("")
    out += globals_
    if globals_:
        out.append("")
    out.append(text_all)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- CLI

def load(path: Path) -> Module:
    text = path.read_text(encoding="utf-8")
    return Parser(lomc.lex(text), text).parse()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomentc", description="L1 Loment 编译器 v0")
    ap.add_argument("file", help=".lomt 源文件")
    ap.add_argument("--emit-rust", metavar="PATH")
    ap.add_argument("--emit-potato", metavar="PATH")
    ap.add_argument("--emit-llvm", metavar="PATH", help="LLVM IR (native M0: 标量子集, docs/144)")
    ap.add_argument("--print", dest="print_target", choices=("rust", "potato", "llvm"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lom-root", default=None, help="use 的 .lom 搜索根 (默认仓库根)")
    args = ap.parse_args(argv)

    root = Path(args.lom_root) if args.lom_root else Path(__file__).resolve().parent.parent
    path = Path(args.file)
    try:
        mod = load(path)
    except LomError as e:
        print(f"[ERR] {path}: {e}", file=sys.stderr)
        return 1

    try:
        deps = resolve_deps(mod, root, path.parent, entry=path)
    except LomError as e:
        print(f"[ERR] {path}: {e}", file=sys.stderr)
        return 1

    errs = check(mod, deps=deps)
    if errs:
        print(f"[ERR] {path}: {len(errs)} 项语义错误:", file=sys.stderr)
        for e in errs:
            print("  " + e, file=sys.stderr)
        return 1

    try:
        rust = emit_rust(mod, root, deps)
        potato = emit_potato(mod, root, deps)
        llvm = emit_llvm(mod, root, deps) if (args.emit_llvm or args.print_target == "llvm") else ""
    except LomError as e:
        print(f"[ERR] {path}: {e}", file=sys.stderr)
        return 1

    if args.print_target:
        sys.stdout.write({"rust": rust, "potato": potato, "llvm": llvm}[args.print_target])
        return 0

    targets = {
        "rust": (args.emit_rust, rust),
        "potato": (args.emit_potato, potato),
        "llvm": (args.emit_llvm, llvm),
    }
    if not any(dest for dest, _ in targets.values()) and not args.check:
        print("[ERR] 未指定输出 (--emit-rust / --emit-potato / --print / --check)", file=sys.stderr)
        return 2

    diffs: list[str] = []
    for kind, (dest, want) in targets.items():
        if not dest:
            continue
        p = Path(dest)
        if args.check:
            if not p.exists():
                diffs.append(f"{dest}: 缺失")
            elif p.read_text(encoding="utf-8") != want:
                diffs.append(f"{dest}: 与转译结果不一致")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(want, encoding="utf-8", newline="\n")
            print(f"[OK] {path} -> {dest} ({len(want)}B)")
    if args.check:
        if diffs:
            print(f"[DIFF] {path}: {len(diffs)} 项漂移:", file=sys.stderr)
            for d in diffs:
                print("  " + d, file=sys.stderr)
            return 1
        print(f"[OK] {path}: 生成物与磁盘一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
