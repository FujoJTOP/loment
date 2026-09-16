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
TYPES = INT_TYPES + ("bool", "str", "ptr", "()")

# 内建函数: 名字 -> (参数类型, 返回类型) —— 由两后端各自降级 (M1/M2)
BUILTINS = {
    "str_len": (("str",), "u32"),
    "str_eq": (("str", "str"), "bool"),
    "str_concat": (("str", "str"), "str"),   # M2
    "str_byte": (("str", "u32"), "u32"),
    "panic": (("u32",), "u32"),  # M19: 不可返回 (类型仅占位)
    "alloc": (("u32",), "ptr"),  # M15
    "free": (("ptr",), "u32"),
    "load8": (("ptr", "u32"), "u32"),
    "store8": (("ptr", "u32", "u8"), "u32"),
    "atomic_add": (("ptr", "u32"), "u32"),  # M21
    "inb": (("u16",), "u32"),               # M20 (仅 Rust 路径)
    "outb": (("u16", "u8"), "u32"),
    "get_bits": (("u8", "u32", "u32"), "u8"),        # M22: 位域读
    "set_bits": (("u8", "u32", "u32", "u8"), "u8"),  # M22: 位域写
    "str_ptr": (("str",), "ptr"),                    # M67: str 数据指针 (syscall 用)
    "ptr_add": (("ptr", "u32"), "ptr"),              # M73: 指针字节偏移
    "ptr_sub": (("ptr", "u32"), "ptr"),
    "syscall4": (("u64", "u64", "u64", "u64"), "i64"),  # M67: nr, a0..a2 (rax/rdi/rsi/rdx)
    "syscall6": (("u64", "u64", "u64", "u64", "u64", "u64"), "i64"),  # M72: nr, a0..a4
}
BUILTIN_DIVERGES = ("panic",)  # 求值后不可继续 (M19)

_IR_RUNTIME = '''
; ---- Loment freestanding 运行时 (M31: 无 libc) ----
define internal i32 @__loment_memcmp(ptr %a, ptr %b, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %cont ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %eq, label %body
body:
  %pa = getelementptr i8, ptr %a, i64 %i
  %pb = getelementptr i8, ptr %b, i64 %i
  %ca = load i8, ptr %pa
  %cb = load i8, ptr %pb
  %ne = icmp ne i8 %ca, %cb
  br i1 %ne, label %diff, label %cont
cont:
  %i1 = add i64 %i, 1
  br label %loop
diff:
  %da = zext i8 %ca to i32
  %db = zext i8 %cb to i32
  %r = sub i32 %da, %db
  ret i32 %r
eq:
  ret i32 0
}

define internal void @__loment_memset(ptr %p, i8 %v, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %q = getelementptr i8, ptr %p, i64 %i
  store i8 %v, ptr %q
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret void
}

define internal void @__loment_memcpy(ptr %d, ptr %s, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %sp = getelementptr i8, ptr %s, i64 %i
  %b = load i8, ptr %sp
  %dp = getelementptr i8, ptr %d, i64 %i
  store i8 %b, ptr %dp
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret void
}

define internal void @__loment_abort() {
  call void @llvm.trap()
  unreachable
}

declare void @llvm.trap()
'''

_RUST_RUNTIME = '''
// ---- Loment 运行时 (M15 堆分配) ----
#[allow(static_mut_refs)]
static mut __LOMENT_HEAP: [u8; 65536] = [0; 65536];
#[allow(static_mut_refs)]
static mut __LOMENT_OFF: usize = 0;

fn __loment_alloc(size: u32) -> *mut u8 {
    unsafe {
        let off = __LOMENT_OFF;
        let end = off + size as usize;
        if end > 65536 {
            panic!("loment: heap oom");
        }
        __LOMENT_OFF = end;
        __LOMENT_HEAP.as_mut_ptr().add(off)
    }
}
fn __loment_load8(p: *mut u8, off: u32) -> u8 {
    unsafe { *p.add(off as usize) }
}
fn __loment_store8(p: *mut u8, off: u32, v: u8) {
    unsafe { *p.add(off as usize) = v; }
}

// ---- P4: 能力域运行时 ----
#[allow(static_mut_refs)]
static mut __LOMENT_AUDIT: [u64; 16] = [0; 16];

fn __loment_guard(cap: usize, idx: u64, lo: u64, hi: u64) {
    unsafe {
        __LOMENT_AUDIT[cap] += 1;
    }
    if idx < lo || idx > hi {
        panic!("loment: capability {} violation at {}", cap, idx);
    }
}
'''


def _rust_t(t: str) -> str:
    if t == "str":
        return "&'static str"
    if t == "ptr":  # M15
        return "*mut u8"
    if t == "()":  # M16: unit
        return "()"
    if _is_mut_slice(t):
        return f"&mut [{_slice_elem(t)}]"
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
    """只读切片 `[T]` 或可变切片 `mut [T]` (M3/M4): ptr + len 视图。"""
    return (t.startswith("[") and t.endswith("]") and ";" not in t) or (
        t.startswith("mut [") and t.endswith("]")
    )


def _is_mut_slice(t: str) -> bool:
    return t.startswith("mut [")


def _slice_elem(t: str) -> str:
    return t[5:-1].strip() if _is_mut_slice(t) else t[1:-1].strip()


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
    pub: bool = False
    tparams: list = field(default_factory=list)  # M7
    from_generic: str = ""                       # M45: 由哪个泛型声明单态化而来
    generic_args: list = field(default_factory=list)  # M45: 单态化实参


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
    pub: bool = False
    tparams: list = field(default_factory=list)  # M7
    from_generic: str = ""                       # M45
    generic_args: list = field(default_factory=list)  # M45
    from_prelude: bool = False   # M10 预置 Option/Result: 由 load() 注入**每个**模块,
                                 # 单元级唯一性检查必须排除它 (否则多模块单元必报假重名)


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
    pub: bool = False


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
class Guard:
    cap: str
    expr: object
    line: int
    cap_id: int = 0   # 解析后填充
    lo: int = 0
    hi: int = 0


@dataclass
class Let:
    name: str
    type: str
    expr: object  # None = 未初始化声明 (M9: let x: T;)
    line: int


@dataclass
class Cast:
    expr: object
    type: str
    line: int


@dataclass
class Try:
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
    pub: bool = False            # M12: 跨模块可见
    tparams: list = field(default_factory=list)  # M6: 泛型参数
    interrupt: bool = False      # M33: x86 中断处理函数
    from_generic: str = ""       # M45: 由哪个泛型声明单态化而来
    generic_args: list = field(default_factory=list)  # M45: 单态化实参


def _rename_self(stmts: list) -> None:
    """impl 方法里 self -> __self (Rust 参数名不能是 self)。"""
    def rx(e) -> None:
        if isinstance(e, Ident) and e.name == "self":
            e.name = "__self"
        elif isinstance(e, Bin):
            rx(e.left)
            rx(e.right)
        elif isinstance(e, Un):
            rx(e.expr)
        elif isinstance(e, Call):
            for a in e.args:
                rx(a)
        elif isinstance(e, MethodCall):
            rx(e.obj)
            for a in e.args:
                rx(a)
        elif isinstance(e, Index):
            rx(e.obj)
            rx(e.idx)
        elif isinstance(e, FieldAccess):
            rx(e.obj)
        elif isinstance(e, ArrayLit):
            for it in e.items:
                rx(it)
        elif isinstance(e, StructLit):
            for _, fe in e.inits:
                rx(fe)
        elif isinstance(e, EnumCtor):
            rx(e.arg)
        elif isinstance(e, Cast):
            rx(e.expr)

    for s in stmts:
        if isinstance(s, Let):
            rx(s.expr)
        elif isinstance(s, Assign):
            rx(s.expr)
            rx(s.target)
        elif isinstance(s, If):
            rx(s.cond)
            _rename_self(s.then)
            _rename_self(s.otherwise)
        elif isinstance(s, While):
            rx(s.cond)
            _rename_self(s.body)
        elif isinstance(s, For):
            rx(s.lo)
            rx(s.hi)
            _rename_self(s.body)
        elif isinstance(s, Match):
            rx(s.subject)
            for _, b in s.arms:
                _rename_self(b)
        elif isinstance(s, Return):
            rx(s.expr)
        elif isinstance(s, ExprStmt):
            rx(s.expr)


def _resolve_guards(mods, caps_map: dict) -> None:
    """P4: guard 语句按能力表填充 id/lo/hi (供后端发射, 避免再传上下文)。"""
    def walk(stmts) -> None:
        for s in stmts:
            if isinstance(s, Guard):
                info = caps_map.get(s.cap)
                if info:
                    s.cap_id, s.lo, s.hi = info
            elif isinstance(s, If):
                walk(s.then)
                walk(s.otherwise)
            elif isinstance(s, While):
                walk(s.body)
            elif isinstance(s, For):
                walk(s.body)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk(b)

    for m in mods:
        for f in m.funcs:
            walk(f.body)


def _resolve_methods(mods, funcs_map: dict, structs: dict, enums: dict) -> None:
    """给 MethodCall 打上具体函数名 (静态派发): obj.m(...) -> Type_m(obj, ...)。"""
    def rx(e, scope) -> None:
        if isinstance(e, MethodCall):
            rx(e.obj, scope)
            for a in e.args:
                rx(a, scope)
            ot = expr_type(e.obj, scope, funcs_map, structs)
            if ot:
                e.mangled = f"{ot}_{e.name}"
            return
        if isinstance(e, Bin):
            rx(e.left, scope)
            rx(e.right, scope)
        elif isinstance(e, Un):
            rx(e.expr, scope)
        elif isinstance(e, Cast):
            rx(e.expr, scope)
        elif isinstance(e, Call):
            for a in e.args:
                rx(a, scope)
        elif isinstance(e, Index):
            rx(e.obj, scope)
            rx(e.idx, scope)
        elif isinstance(e, FieldAccess):
            rx(e.obj, scope)
        elif isinstance(e, ArrayLit):
            for it in e.items:
                rx(it, scope)
        elif isinstance(e, StructLit):
            for _, fe in e.inits:
                rx(fe, scope)
        elif isinstance(e, EnumCtor):
            rx(e.arg, scope)

    def walk(stmts, scope) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.expr is not None:
                    rx(s.expr, scope)
                scope[s.name] = s.type
            elif isinstance(s, Assign):
                rx(s.expr, scope)
                rx(s.target, scope)
            elif isinstance(s, If):
                rx(s.cond, scope)
                walk(s.then, dict(scope))
                walk(s.otherwise, dict(scope))
            elif isinstance(s, While):
                rx(s.cond, scope)
                walk(s.body, dict(scope))
            elif isinstance(s, For):
                rx(s.lo, scope)
                rx(s.hi, scope)
                walk(s.body, dict(scope))
            elif isinstance(s, Match):
                rx(s.subject, scope)
                for _, b in s.arms:
                    walk(b, dict(scope))
            elif isinstance(s, Return):
                rx(s.expr, scope)
            elif isinstance(s, ExprStmt):
                rx(s.expr, scope)
            elif isinstance(s, Guard):
                rx(s.expr, scope)

    for m in mods:
        for f in m.funcs:
            walk(f.body, {p.name: p.type for p in f.params})


@dataclass
class Trait:
    name: str
    methods: list  # list[tuple[str, str]]  (方法名, 返回类型)
    line: int
    pub: bool = False


@dataclass
class Impl:
    trait: str
    type: str
    funcs: list  # list[Func], 第 0 个参数是 self
    line: int


@dataclass
class MethodCall:
    obj: object
    name: str
    args: list
    line: int
    mangled: str = ""  # 解析后的具体函数名 (静态派发)


@dataclass
class Module:
    name: str
    uses: list[str] = field(default_factory=list)      # use "*.lom"  (L0 布局; 只有路径形式)
    imports: list[str] = field(default_factory=list)   # use "*.lomt" (L1 模块; 路径形式)
    name_imports: list[str] = field(default_factory=list)   # use <名字>  (L1 模块; 名字形式)
    caps: list[Capability] = field(default_factory=list)
    structs: list[Struct] = field(default_factory=list)
    enums: list[EnumDecl] = field(default_factory=list)
    consts: list[ConstDecl] = field(default_factory=list)
    traits: list[Trait] = field(default_factory=list)
    impls: list[Impl] = field(default_factory=list)
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
        """类型名: 基类型 / struct / 数组 [T; N] / 只读切片 [T] / 可变切片 mut [T]。"""
        if self.at("ident", "mut") and self.peek(1).kind == "punct" and self.peek(1).val == "[":
            self.next()
            self.next()
            elem = self.type_name()
            self.expect("punct", "]")
            return f"mut [{elem}]"
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
        name = self.ident("类型名")
        if self.at("punct", "<"):  # M7: 泛型实参 Pair<u32>
            self.next()
            args: list[str] = []
            while not self.at("punct", ">"):
                args.append(self.type_name())
                if not self.accept("punct", ","):
                    break
            self.expect("punct", ">")
            return f"{name}<{', '.join(args)}>"
        return name

    def parse_tparams(self) -> list[str]:
        out: list[str] = []
        if self.at("punct", "<"):
            self.next()
            while not self.at("punct", ">"):
                out.append(self.ident("类型参数"))
                if not self.accept("punct", ","):
                    break
            self.expect("punct", ">")
        return out

    # -- 顶层
    def parse(self) -> Module:
        self.expect("ident", "module", "（文件必须以 module 开头）")
        mod = Module(self.ident("模块名"))
        while not self.at("eof"):
            t = self.peek()
            if t.kind != "ident":
                raise LomError(t.line, t.col, f"顶层只允许 use/capability/fn，得到 {t.val!r}")
            is_pub = False
            if t.val == "pub":  # M12
                self.next()
                is_pub = True
                t = self.peek()
                if t.kind != "ident":
                    raise LomError(t.line, t.col, "pub 之后需要一项声明")
            if t.val == "use":
                self.next()
                # 两种写法并存:
                #   use "loment/examples/bytes.lomt"   路径形式 (L0 布局只能是这一种)
                #   use bytes                          名字形式 (只解析 .lomt, 见 NAME_ROOTS)
                nxt = self.peek()
                if nxt.kind == "ident":
                    mod.name_imports.append(self.next().val)
                else:
                    p = self.expect("string", None, "（.lom 或 .lomt 路径）").val
                    if p.endswith(".lomt"):
                        mod.imports.append(p)
                    else:
                        mod.uses.append(p)
            elif t.val == "capability":
                mod.caps.append(self.parse_capability())
            elif t.val == "struct":
                s = self.parse_struct()
                s.pub = is_pub
                mod.structs.append(s)
            elif t.val == "enum":
                e = self.parse_enum()
                e.pub = is_pub
                mod.enums.append(e)
            elif t.val == "const":
                c = self.parse_const()
                c.pub = is_pub
                mod.consts.append(c)
            elif t.val == "excluded":
                self.next()
                mod.excluded.append(self.expect("string", None, "（出界声明）").val)
            elif t.val == "trait":
                mod.traits.append(self.parse_trait())
            elif t.val == "impl":
                im = self.parse_impl()
                mod.impls.append(im)
                for f in im.funcs:
                    f.name = f"{im.type}_{f.name}"  # 静态派发: 名字按接收者类型混淆
                    mod.funcs.append(f)
            elif t.val == "interrupt":  # M33: 中断处理函数
                self.next()
                f = self.parse_fn()
                f.interrupt = True
                f.pub = is_pub
                mod.funcs.append(f)
            elif t.val == "fn":
                f = self.parse_fn()
                f.pub = is_pub
                mod.funcs.append(f)
            else:
                raise LomError(t.line, t.col, f"未知顶层关键字 {t.val!r}")
        return mod

    def parse_struct(self) -> Struct:
        kw = self.expect("ident", "struct")
        name = self.ident("结构体名")
        tparams = self.parse_tparams()
        self.expect("punct", "{")
        fields: list[tuple[str, str]] = []
        while not self.at("punct", "}"):
            fn = self.ident("字段名")
            self.expect("punct", ":")
            fields.append((fn, self.type_name()))
            if not self.accept("punct", ","):
                self.accept("punct", ";")
        self.expect("punct", "}")
        return Struct(name, fields, kw.line, False, tparams)

    def parse_enum(self) -> EnumDecl:
        """L1 枚举: 变体可带单载荷 (v1)。"""
        kw = self.expect("ident", "enum")
        name = self.ident("枚举名")
        tparams = self.parse_tparams()
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
        return EnumDecl(name, variants, kw.line, payloads, False, tparams)

    def parse_trait(self) -> Trait:
        """trait Name { fn m(self, ...) -> R; ... }"""
        kw = self.expect("ident", "trait")
        name = self.ident("trait 名")
        self.expect("punct", "{")
        methods: list[tuple[str, str]] = []
        while not self.at("punct", "}"):
            self.expect("ident", "fn", "（trait 内只允许 fn）")
            mname = self.ident("方法名")
            self.expect("punct", "(")
            self.expect("ident", "self", "（方法第一个参数必须是 self）")
            while not self.at("punct", ")"):
                self.expect("punct", ",")
                if self.at("punct", ")"):
                    break
                self.ident("参数名")
                self.expect("punct", ":")
                self.type_name()
            self.expect("punct", ")")
            self.expect("punct", "-")
            self.expect("punct", ">")
            ret = self.type_name()
            self.expect("punct", ";")
            methods.append((mname, ret))
        self.expect("punct", "}")
        return Trait(name, methods, kw.line)

    def parse_impl(self) -> Impl:
        """impl Trait for Type { fn m(self, ...) -> R { ... } }"""
        kw = self.expect("ident", "impl")
        tr = self.ident("trait 名")
        self.expect("ident", "for")
        ty = self.type_name()
        self.expect("punct", "{")
        funcs: list[Func] = []
        while not self.at("punct", "}"):
            f = self.parse_fn()
            if not f.params or f.params[0].name != "self":
                raise LomError(f.line, 1, f"impl 方法 {f.name} 第一个参数必须是 self")
            f.params[0].name = "__self"
            f.params[0].type = ty  # 接收者类型
            _rename_self(f.body)
            funcs.append(f)
        self.expect("punct", "}")
        return Impl(tr, ty, funcs, kw.line)

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
        tparams: list[str] = []
        if self.at("punct", "<"):  # M6: 泛型参数
            self.next()
            while not self.at("punct", ">"):
                tparams.append(self.ident("类型参数"))
                if not self.accept("punct", ","):
                    break
            self.expect("punct", ">")
        self.expect("punct", "(")
        params: list[Param] = []
        while not self.at("punct", ")"):
            pn = self.ident("参数名")
            if pn == "self" and not self.at("punct", ":"):
                params.append(Param("self", ""))  # 类型由 impl 填 (M8)
            else:
                self.expect("punct", ":")
                params.append(Param(pn, self.type_name()))
            if not self.accept("punct", ","):
                break
        self.expect("punct", ")")
        if self.at("punct", "{"):  # M16: 无返回类型 = ()
            ret = "()"
        else:
            self.expect("punct", "-")
            self.expect("punct", ">")
            ret = self.type_name()
        body = self.parse_block()
        return Func(name, params, ret, body, kw.line, False, tparams)

    def parse_block(self) -> list:
        self.expect("punct", "{")
        out = []
        while not self.at("punct", "}"):
            out.append(self.parse_stmt())
        self.expect("punct", "}")
        return out

    def parse_stmt(self):
        t = self.peek()
        if t.val == "guard":  # P4: 能力域守卫
            self.next()
            cap = self.ident("能力名")
            self.expect("punct", "(")
            e = self.parse_expr()
            self.expect("punct", ")")
            self.expect("punct", ";")
            return Guard(cap, e, t.line)
        if t.val == "let":
            self.next()
            name = self.ident("变量名")
            self.expect("punct", ":")
            ty = self.type_name()
            if self.accept("punct", ";"):  # M9: 未初始化声明
                return Let(name, ty, None, t.line)
            self.expect("punct", "=")
            e = self.parse_expr()
            self.expect("punct", ";")
            return Let(name, ty, e, t.line)
        if t.val == "if":
            self.next()
            if self.at("ident", "let"):  # M10: if let 语法糖 -> match
                self.next()
                en = self.ident("枚举名")
                self.expect("punct", ":")
                self.expect("punct", ":")
                vn = self.ident("变体名")
                bind = None
                if self.accept("punct", "("):
                    bind = self.ident("绑定名")
                    self.expect("punct", ")")
                self.expect("punct", "=")
                self.no_struct += 1
                subj = self.parse_expr()
                self.no_struct -= 1
                then = self.parse_block()
                otherwise = []
                if self.at("ident", "else"):
                    self.next()
                    otherwise = self.parse_block()
                return Match(subj, [(EnumPath(en, vn, t.line, bind), then), (None, otherwise)], t.line)
            self.no_struct += 1
            cond = self.parse_expr()
            self.no_struct -= 1
            then = self.parse_block()
            otherwise = []
            if self.at("ident", "else"):
                self.next()
                if self.at("ident", "if"):  # else if 链
                    otherwise = [self.parse_stmt()]
                else:
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
        if t.kind == "punct" and t.val == "&" and self.peek(1).kind == "ident" \
                and self.peek(1).val == "mut":
            self.next()
            self.next()
            return Un("&mut", self.parse_unary(), t.line)
        if t.kind == "punct" and t.val in UN_OPS:
            self.next()
            return Un(t.val, self.parse_unary(), t.line)
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.kind == "number":
            return self.parse_postfix(IntLit(self.int_lit(), t.line))
        if t.kind == "string":
            return self.parse_postfix(StrLit(self.next().val, t.line))
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
                return self.parse_postfix(BoolLit(True, t.line))
            if t.val == "false":
                self.next()
                return self.parse_postfix(BoolLit(False, t.line))
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
                return self.parse_postfix(Call(name, args, t.line))
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
                fname = self.ident("字段/方法名")
                if self.at("punct", "("):  # M8: 方法调用
                    self.next()
                    args = []
                    while not self.at("punct", ")"):
                        args.append(self.parse_expr())
                        if not self.accept("punct", ","):
                            break
                    self.expect("punct", ")")
                    e = MethodCall(e, fname, args, t.line)
                else:
                    e = FieldAccess(e, fname, t.line)
            elif self.at("punct", "["):
                t = self.next()
                idx = self.parse_expr()
                self.expect("punct", "]")
                e = Index(e, idx, t.line)
            elif self.at("ident", "as"):  # 类型转换 (M16 起需要)
                t = self.next()
                e = Cast(e, self.type_name(), t.line)
            elif self.at("punct", "?"):  # M9: 错误传播
                t = self.next()
                e = Try(e, t.line)
            else:
                return e


# ---------------------------------------------------------------- 单态化 (M6)

def _fnv1a32(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _bit_width(t: str) -> int:
    if t == "bool":
        return 1
    if t in INT_TYPES:
        return int(t[1:])
    return 64  # ptr 等


def _is_generic_type(t: str) -> bool:
    return "<" in t and t.endswith(">")


def _generic_parts(t: str) -> tuple[str, list[str]]:
    base, rest = t.split("<", 1)
    inner = rest[:-1]
    args, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return base.strip(), args


def _replace_type(t: str, mapping: dict[str, str]) -> str:
    """按映射重写类型串 (含泛型实参递归); mapping 可含 "Pair<u32>" -> "Pair_u32"。"""
    if t in mapping:
        return mapping[t]
    if _is_generic_type(t):
        base, args = _generic_parts(t)
        return f"{base}<{', '.join(_replace_type(a, mapping) for a in args)}>"
    if _is_array(t):
        return f"[{_replace_type(_array_elem(t), mapping)}; {_array_len(t)}]"
    if _is_slice(t):
        inner = _replace_type(_slice_elem(t), mapping)
        return f"mut [{inner}]" if _is_mut_slice(t) else f"[{inner}]"
    return t


def _subst_type(t: str, m: dict[str, str]) -> str:
    if t in m:
        return m[t]
    if _is_array(t):
        return f"[{_subst_type(_array_elem(t), m)}; {_array_len(t)}]"
    if _is_slice(t):
        inner = _subst_type(_slice_elem(t), m)
        return f"mut [{inner}]" if _is_mut_slice(t) else f"[{inner}]"
    return t


def _subst_stmts(stmts: list, m: dict[str, str]) -> None:
    for s in stmts:
        if isinstance(s, Let):
            s.type = _subst_type(s.type, m)
        elif isinstance(s, If):
            _subst_stmts(s.then, m)
            _subst_stmts(s.otherwise, m)
        elif isinstance(s, While):
            _subst_stmts(s.body, m)
        elif isinstance(s, For):
            _subst_stmts(s.body, m)
        elif isinstance(s, Match):
            for _, b in s.arms:
                _subst_stmts(b, m)


def _instantiate(gf: Func, m: dict[str, str], name: str) -> Func:
    import copy as _c

    g = _c.deepcopy(gf)
    g.name, g.tparams, g.pub = name, [], True
    for p in g.params:
        p.type = _subst_type(p.type, m)
    g.ret = _subst_type(g.ret, m)
    _subst_stmts(g.body, m)
    return g


def _infer_targs(gf: Func, argtypes: list) -> dict[str, str] | None:
    m: dict[str, str] = {}
    for p, at in zip(gf.params, argtypes):
        if p.type in gf.tparams:
            if at is None:
                continue
            if p.type in m and m[p.type] != at:
                return None
            m[p.type] = at
        elif _is_slice(p.type) and _slice_elem(p.type) in gf.tparams and at is not None:
            if _is_slice(at):
                m[_slice_elem(p.type)] = _slice_elem(at)
            elif _is_array(at):
                m[_slice_elem(p.type)] = _array_elem(at)
    return m if all(t in m for t in gf.tparams) else None


def _mono_expr(e, scope, generics, insts, structs, enums) -> None:
    if isinstance(e, Call):
        for a in e.args:
            _mono_expr(a, scope, generics, insts, structs, enums)
        gf = generics.get(e.name)
        if gf is not None:
            argt = [expr_type(a, scope, {}, structs) for a in e.args]
            m = _infer_targs(gf, argt)
            if m is not None:
                key = (gf.name,) + tuple(m[t] for t in gf.tparams)
                if key not in insts:
                    inst = _instantiate(gf, m, gf.name + "_" + "_".join(m[t] for t in gf.tparams))
                    inst.from_generic = gf.name
                    inst.generic_args = [m[t] for t in gf.tparams]
                    insts[key] = inst
                e.name = insts[key].name
        return
    if isinstance(e, Bin):
        _mono_expr(e.left, scope, generics, insts, structs, enums)
        _mono_expr(e.right, scope, generics, insts, structs, enums)
    elif isinstance(e, Un):
        _mono_expr(e.expr, scope, generics, insts, structs, enums)
    elif isinstance(e, Cast):
        _mono_expr(e.expr, scope, generics, insts, structs, enums)
    elif isinstance(e, Index):
        _mono_expr(e.obj, scope, generics, insts, structs, enums)
        _mono_expr(e.idx, scope, generics, insts, structs, enums)
    elif isinstance(e, FieldAccess):
        _mono_expr(e.obj, scope, generics, insts, structs, enums)
    elif isinstance(e, ArrayLit):
        for it in e.items:
            _mono_expr(it, scope, generics, insts, structs, enums)
    elif isinstance(e, StructLit):
        for _, fe in e.inits:
            _mono_expr(fe, scope, generics, insts, structs, enums)
    elif isinstance(e, EnumCtor):
        _mono_expr(e.arg, scope, generics, insts, structs, enums)


def _mono_stmts(stmts, scope, generics, insts, structs, enums) -> None:
    for s in stmts:
        if isinstance(s, Let):
            if s.expr is not None:
                _mono_expr(s.expr, scope, generics, insts, structs, enums)
            scope[s.name] = s.type
        elif isinstance(s, Assign):
            _mono_expr(s.target, scope, generics, insts, structs, enums)
            _mono_expr(s.expr, scope, generics, insts, structs, enums)
        elif isinstance(s, If):
            _mono_expr(s.cond, scope, generics, insts, structs, enums)
            _mono_stmts(s.then, dict(scope), generics, insts, structs, enums)
            _mono_stmts(s.otherwise, dict(scope), generics, insts, structs, enums)
        elif isinstance(s, While):
            _mono_expr(s.cond, scope, generics, insts, structs, enums)
            _mono_stmts(s.body, dict(scope), generics, insts, structs, enums)
        elif isinstance(s, For):
            _mono_expr(s.lo, scope, generics, insts, structs, enums)
            _mono_expr(s.hi, scope, generics, insts, structs, enums)
            bs = dict(scope)
            bs[s.var] = expr_type(s.lo, scope, {}, structs) \
                or expr_type(s.hi, scope, {}, structs) or "u32"
            _mono_stmts(s.body, bs, generics, insts, structs, enums)
        elif isinstance(s, Match):
            _mono_expr(s.subject, scope, generics, insts, structs, enums)
            for _, b in s.arms:
                _mono_stmts(b, dict(scope), generics, insts, structs, enums)
        elif isinstance(s, Return):
            _mono_expr(s.expr, scope, generics, insts, structs, enums)
        elif isinstance(s, ExprStmt):
            _mono_expr(s.expr, scope, generics, insts, structs, enums)
        elif isinstance(s, Guard):
            _mono_expr(s.expr, scope, generics, insts, structs, enums)


def _collect_types(mods) -> set[str]:
    refs: set[str] = set()

    def walk_stmts(stmts) -> None:
        for s in stmts:
            if isinstance(s, Let):
                refs.add(s.type)
            elif isinstance(s, If):
                walk_stmts(s.then)
                walk_stmts(s.otherwise)
            elif isinstance(s, While):
                walk_stmts(s.body)
            elif isinstance(s, For):
                walk_stmts(s.body)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk_stmts(b)

    for m in mods:
        for f in m.funcs:
            refs.add(f.ret)
            for p in f.params:
                refs.add(p.type)
            walk_stmts(f.body)
        for s in m.structs:
            for _, ft in s.fields:
                refs.add(ft)
        for e in m.enums:
            for _, pt in e.payloads.items():
                refs.add(pt)
    return refs


def _rewrite_types(mods, mapping: dict[str, str]) -> None:
    def walk_stmts(stmts) -> None:
        for s in stmts:
            if isinstance(s, Let):
                s.type = _replace_type(s.type, mapping)
            elif isinstance(s, If):
                walk_stmts(s.then)
                walk_stmts(s.otherwise)
            elif isinstance(s, While):
                walk_stmts(s.body)
            elif isinstance(s, For):
                walk_stmts(s.body)
            elif isinstance(s, Match):
                for _, b in s.arms:
                    walk_stmts(b)

    for m in mods:
        for f in m.funcs:
            f.ret = _replace_type(f.ret, mapping)
            for p in f.params:
                p.type = _replace_type(p.type, mapping)
            walk_stmts(f.body)
        for s in m.structs:
            s.fields = [(fn, _replace_type(ft, mapping)) for fn, ft in s.fields]
        for e in m.enums:
            e.payloads = {v: _replace_type(pt, mapping) for v, pt in e.payloads.items()}


def _fix_generic_literals(mods, bases: set[str]) -> None:
    """字面量名随 let 注解的实例名改写: Pair { .. } -> Pair_u32 { .. }。"""
    def walk(stmts, scope: dict) -> None:
        for s in stmts:
            if isinstance(s, Let):
                e = s.expr
                if e is None:
                    scope[s.name] = s.type
                    continue
                if isinstance(e, StructLit) and e.name in bases:
                    e.name = s.type
                elif isinstance(e, EnumCtor) and e.enum in bases:
                    e.enum = s.type
                elif isinstance(e, EnumPath) and e.enum in bases:
                    e.enum = s.type
                scope[s.name] = s.type
            elif isinstance(s, If):
                walk(s.then, dict(scope))
                walk(s.otherwise, dict(scope))
            elif isinstance(s, While):
                walk(s.body, dict(scope))
            elif isinstance(s, For):
                walk(s.body, dict(scope))
            elif isinstance(s, Return):
                if isinstance(s.expr, (EnumCtor, EnumPath)) and s.expr.enum in bases:
                    s.expr.enum = f.ret  # 按函数返回类型解析
            elif isinstance(s, Assign):
                if isinstance(s.target, Ident) and isinstance(s.expr, (EnumCtor, EnumPath)) \
                        and s.expr.enum in bases and s.target.name in scope:
                    s.expr.enum = scope[s.target.name]
            elif isinstance(s, Match):
                st = expr_type(s.subject, scope, {}, {})
                for pat, b in s.arms:
                    if pat is not None and pat.enum in bases and st:
                        pat.enum = st  # 模式枚举名随主体实例名改写
                    walk(b, dict(scope))

    for m in mods:
        for f in m.funcs:
            walk(f.body, {p.name: p.type for p in f.params})


def _ds_stmts(stmts, scope, f, funcs_map, structs):
    out = []
    for s in stmts:
        if isinstance(s, Let) and isinstance(s.expr, Try):
            inner = s.expr.expr
            rt = expr_type(inner, scope, funcs_map, structs)
            if rt is None or not rt.startswith("Result_"):
                out.append(s)
                continue
            tmp, v, er = f"__t{s.line}", f"__v{s.line}", f"__e{s.line}"
            out.append(Let(tmp, rt, inner, s.line))
            out.append(Let(s.name, s.type, None, s.line))
            arms = [
                (EnumPath(rt, "Ok", s.line, v),
                 [Assign(Ident(s.name, s.line), Ident(v, s.line), s.line)]),
                (EnumPath(rt, "Err", s.line, er),
                 [Return(EnumCtor(rt, "Err", Ident(er, s.line), s.line), s.line)]),
            ]
            out.append(Match(Ident(tmp, s.line), arms, s.line))
            scope[s.name] = s.type
            continue
        if isinstance(s, If):
            s.then = _ds_stmts(s.then, dict(scope), f, funcs_map, structs)
            s.otherwise = _ds_stmts(s.otherwise, dict(scope), f, funcs_map, structs)
        elif isinstance(s, While):
            s.body = _ds_stmts(s.body, dict(scope), f, funcs_map, structs)
        elif isinstance(s, For):
            s.body = _ds_stmts(s.body, dict(scope), f, funcs_map, structs)
        elif isinstance(s, Match):
            s.arms = [(p, _ds_stmts(b, dict(scope), f, funcs_map, structs)) for p, b in s.arms]
        out.append(s)
    return out


def _desugar_try(mods, funcs_map, structs) -> None:
    """M9: `let x: T = e?;` -> 临时绑定 + match (Err 早退)。"""
    for m in mods:
        for f in m.funcs:
            f.body = _ds_stmts(f.body, {p.name: p.type for p in f.params}, f, funcs_map, structs)


def _has_try(mods) -> bool:
    def scan_e(e) -> bool:
        if isinstance(e, Try):
            return True
        for child in (getattr(e, "left", None), getattr(e, "right", None), getattr(e, "expr", None),
                      getattr(e, "obj", None), getattr(e, "idx", None), getattr(e, "arg", None)):
            if child is not None and scan_e(child):
                return True
        return any(scan_e(a) for a in getattr(e, "args", []) or [])

    def scan_s(stmts) -> bool:
        for s in stmts:
            for e in (getattr(s, "expr", None), getattr(s, "cond", None), getattr(s, "subject", None)):
                if e is not None and scan_e(e):
                    return True
            for key in ("then", "otherwise", "body"):
                sub = getattr(s, key, None)
                if sub and scan_s(sub):
                    return True
            for _, b in getattr(s, "arms", []) or []:
                if scan_s(b):
                    return True
        return False

    return any(scan_s(f.body) for m in mods for f in m.funcs)


def _has_guard(mods) -> bool:
    def scan(stmts) -> bool:
        for s in stmts:
            if isinstance(s, Guard):
                return True
            for key in ("then", "otherwise", "body"):
                sub = getattr(s, key, None)
                if sub and scan(sub):
                    return True
            for _, b in getattr(s, "arms", []) or []:
                if scan(b):
                    return True
        return False

    return any(scan(f.body) for m in mods for f in m.funcs)


def prepare(mod: Module, deps: list[Module] | None = None) -> tuple[Module, list[Module]]:
    """M6/M7: 单态化 —— 泛型函数与泛型类型展开为具体副本 (幂等)。"""
    import copy as _c

    deps = list(deps or [])
    allmods = deps + [mod]
    if not (any(f.tparams for m in allmods for f in m.funcs)
            or any(s.tparams for m in allmods for s in m.structs)
            or any(e.tparams for m in allmods for e in m.enums)
            or any(m.impls for m in allmods)
            or _has_try(allmods)
            or _has_guard(allmods)):
        return mod, deps
    mod, deps = _c.deepcopy(mod), _c.deepcopy(deps)
    allmods = deps + [mod]

    # ---- M7: 泛型类型实例化
    gs = {s.name: s for m in allmods for s in m.structs if s.tparams}
    ge = {e.name: e for m in allmods for e in m.enums if e.tparams}
    if gs or ge:
        made: dict[str, str] = {}
        for _ in range(8):
            refs = sorted(t for t in _collect_types(allmods) if _is_generic_type(t) and t not in made)
            if not refs:
                break
            for ref in refs:
                base, args = _generic_parts(ref)
                name = base + "_" + "_".join(args)
                made[ref] = name
                mp = {t: a for t, a in zip(gs.get(base, ge.get(base)).tparams, args)} if (
                    base in gs or base in ge) else None
                if mp is None:
                    continue
                mp[ref] = name
                if base in gs:
                    g = gs[base]
                    s = Struct(
                        name, [(fn, _replace_type(ft, mp)) for fn, ft in g.fields], g.line, True, [])
                    s.from_generic, s.generic_args = base, list(args)
                    mod.structs.append(s)
                else:
                    g = ge[base]
                    e = EnumDecl(
                        name, list(g.variants), g.line,
                        {v: _replace_type(pt, mp) for v, pt in g.payloads.items()}, True, [])
                    e.from_generic, e.generic_args = base, list(args)
                    mod.enums.append(e)
        _rewrite_types(allmods, made)
        _fix_generic_literals(allmods, set(gs) | set(ge))
        mod.structs = [s for s in mod.structs if not s.tparams]
        mod.enums = [e for e in mod.enums if not e.tparams]
        for m in deps:
            m.structs = [s for s in m.structs if not s.tparams]
            m.enums = [e for e in m.enums if not e.tparams]

    # ---- M6: 泛型函数单态化
    structs_map = {s.name: s for m in allmods for s in m.structs}
    enums_map = {e.name: e for m in allmods for e in m.enums}
    generics = {f.name: f for m in allmods for f in m.funcs if f.tparams}
    if generics:
        insts: dict[tuple, Func] = {}
        walked: set[str] = set()
        for _ in range(8):
            before = len(insts)
            pool = [x for m in allmods for x in m.funcs] + list(insts.values())
            for f in pool:
                if f.tparams or f.name in walked:
                    continue
                walked.add(f.name)
                _mono_stmts(f.body, {p.name: p.type for p in f.params}, generics, insts,
                            structs_map, enums_map)
            if len(insts) == before:
                break
        mod.funcs = [f for f in mod.funcs if not f.tparams] + list(insts.values())
        for m in deps:
            m.funcs = [f for f in m.funcs if not f.tparams]

    funcs_map = {f.name: f for m in allmods for f in m.funcs}
    if any(m.impls for m in allmods):
        _resolve_methods(allmods, funcs_map, structs_map, enums_map)
    if _has_try(allmods):  # M9: ? 语法糖
        _desugar_try(allmods, funcs_map, structs_map)
    if _has_guard(allmods):  # P4: guard 解析
        _resolve_guards(allmods, {c.name: (i, c.lo, c.hi) for i, c in enumerate(mod.caps)})
    return mod, deps


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
    if isinstance(e, Cast):
        st = expr_type(e.expr, scope, funcs, structs)
        if st == "ptr" and e.type in ("u64", "i64"):  # M67
            return e.type
        if e.type == "ptr" and st in INT_TYPES:  # M83: 整数 -> 指针 (brk/mmap 取内存)
            return "ptr"
        if st is None and isinstance(e.expr, IntLit) and e.type in INT_TYPES:
            return e.type
        if st is not None and (st in INT_TYPES or st == "bool") \
                and (e.type in INT_TYPES or e.type == "bool"):
            return e.type
        return None
    if isinstance(e, MethodCall):
        ot = expr_type(e.obj, scope, funcs, structs)
        fn = funcs.get(f"{ot}_{e.name}") if ot else None
        return fn.ret if fn else None
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
        if e.op == "&mut":  # M4: 数组 -> 可变切片
            return f"mut [{_array_elem(inner)}]" if inner and _is_array(inner) else None
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


def _borrows_of(e, out: dict[str, set[str]]) -> None:
    """M5: 收集表达式里的借用 (变量 -> {"&","&mut"})。"""
    if isinstance(e, Un) and e.op in ("&", "&mut") and isinstance(e.expr, Ident):
        out.setdefault(e.expr.name, set()).add(e.op)
        return
    if isinstance(e, Call):
        for a in e.args:
            _borrows_of(a, out)
    elif isinstance(e, Bin):
        _borrows_of(e.left, out)
        _borrows_of(e.right, out)
    elif isinstance(e, Un):
        _borrows_of(e.expr, out)
    elif isinstance(e, Index):
        _borrows_of(e.obj, out)
        _borrows_of(e.idx, out)
    elif isinstance(e, FieldAccess):
        _borrows_of(e.obj, out)
    elif isinstance(e, ArrayLit):
        for it in e.items:
            _borrows_of(it, out)


def _check_borrows(args: list, line: int, errs: list[str]) -> None:
    """M5 借用检查 v0: 同一次调用里同一变量不得既借又可变借, 或可变借两次。"""
    b: dict[str, set[str]] = {}
    for a in args:
        _borrows_of(a, b)
    for name, kinds in b.items():
        if len(kinds) > 1:
            errs.append(f"{line}: 变量 {name} 在同一次调用里既被可变借用又被借用")
        elif kinds == {"&mut"}:
            n_mut = sum(1 for a in args if isinstance(a, Un) and a.op == "&mut"
                        and isinstance(a.expr, Ident) and a.expr.name == name)
            if n_mut > 1:
                errs.append(f"{line}: 变量 {name} 被可变借用两次 (不允许别名)")


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
    if isinstance(e, Cast):
        _walk_expr(e.expr, scope, funcs, structs, errs, enums)
        st = expr_type(e.expr, scope, funcs, structs)
        if st == "ptr" and e.type in ("u64", "i64"):  # M67: 指针转整数
            return
        if e.type == "ptr" and st in INT_TYPES:  # M83: 整数 -> 指针 (brk/mmap 取内存)
            return
        if st is None and isinstance(e.expr, IntLit):  # 整型字面量按目标定宽
            # `ptr` 也算: 字面量的类型是 None, 所以上面那条 `e.type == "ptr" and st in
            # INT_TYPES` 够不着它 —— 于是 `0 as ptr`(空指针的惯用写法) 被当成非法目标。
            # 镜那边 (selfhost/checker.lomt, 同处按 `chk_ty_is_int`) 一直是放行的, 于是
            # 同一份源码**参考报错、打包版能编**。2026-09-15 由 lompi 的 `0 as ptr` 实测抓到。
            if e.type not in INT_TYPES and e.type != "ptr":
                errs.append(f"{e.line}: as 目标类型非法 {e.type}")
            return
        if st is None or not (st in INT_TYPES or st == "bool"):
            errs.append(f"{e.line}: as 只能作用于整型/布尔 (得到 {st})")
        elif e.type not in INT_TYPES and e.type != "bool":
            errs.append(f"{e.line}: as 目标类型非法 {e.type}")
        return
    if isinstance(e, Try):
        errs.append(f"{e.line}: ? 只能用于 let 绑定 (let x: T = e?;)")
        return
    if isinstance(e, MethodCall):
        _walk_expr(e.obj, scope, funcs, structs, errs, enums)
        ot = expr_type(e.obj, scope, funcs, structs)
        fn = funcs.get(f"{ot}_{e.name}") if ot else None
        if fn is None:
            errs.append(f"{e.line}: 类型 {ot} 没有方法 {e.name}（需要 impl）")
        elif len(e.args) + 1 != len(fn.params):
            errs.append(f"{e.line}: 方法 {e.name} 需要 {len(fn.params) - 1} 个实参，得到 {len(e.args)}")
        for a in e.args:
            _walk_expr(a, scope, funcs, structs, errs, enums)
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
            errs.append(f"{e.line}: 调用未定义的函数 {e.name}（跨模块调用需要 pub）")
        else:
            params = funcs[e.name].params
            _check_borrows(e.args, e.line, errs)  # M5
            if len(e.args) != len(params):
                errs.append(f"{e.line}: {e.name} 需要 {len(params)} 个实参，得到 {len(e.args)}")
            else:
                for a, p in zip(e.args, params):
                    at = expr_type(a, scope, funcs, structs)
                    if at is None:
                        if p.type not in INT_TYPES and not _is_slice(p.type):
                            errs.append(f"{e.line}: {e.name} 实参期望 {p.type}，得到整型字面量")
                    elif at == p.type:
                        pass
                    elif _is_slice(p.type) and _is_array(at) and _slice_elem(p.type) == _array_elem(at):
                        pass  # 数组 -> 切片 协变 (M3)
                    elif _is_slice(p.type) and _is_slice(at) and _slice_elem(p.type) == _slice_elem(at):
                        if _is_mut_slice(p.type) and not _is_mut_slice(at):
                            errs.append(f"{e.line}: {e.name} 形参要求可变切片，实参是只读切片")
                        # 可变 -> 只读 允许
                    else:
                        errs.append(f"{e.line}: {e.name} 实参类型 {at}，期望 {p.type}")
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
        if e.op in ("&", "&mut"):
            inner = expr_type(e.expr, scope, funcs, structs)
            if inner is None or not _is_array(inner):
                errs.append(f"{e.line}: {e.op} 只能作用于数组 (得到 {inner})")
        return
    if isinstance(e, Bin):
        _walk_expr(e.left, scope, funcs, structs, errs, enums)
        _walk_expr(e.right, scope, funcs, structs, errs, enums)
        return


#: `use <名字>` 的搜索根 (相对仓库根, 按序找 `<名字>.lomt`)。
#: **只解析 .lomt** —— 名字形式是给 L1 模块用的; L0 布局 (`*.lom`) 继续走路径形式,
#: 于是不存在"这个名字算 L0 还是 L1"的歧义。顺序: 标准库在前, 后面三个覆盖仓库自身。
#: 这四个根是**仓库自己的纪律** (名字不许重), 见 `resolve_name` 第 3 层。
NAME_ROOTS = ("loment/lib", "loment/examples", "loment/selfhost", "loment/tools")
#: 项目本地的依赖目录 —— lompi `plan/install --into deps` 的落点:
#: `<项目根>/deps/<名字>/<名字>.lomt`。项目根 = 入口文件所在目录 (docs/168 §4.1)。
LOCAL_DEPS = "deps"
#: 工具链**随包自带**的库, 按 lompi 的 store 布局放: `<工具目录>/../share/lompi/store/`。
#: 有它, 装在用户机器上 (没有仓库、也没有 `deps/`) 时 `use std` 才命中得了 ——
#: 用户 2026-09-16 定: "像 python/java 那样, 简单方便"。
TOOLCHAIN_STORE = ("share", "lompi", "store")
#: 单个文件的 `use` 条数上限 (路径形式 + 名字形式合起来算)。
#: **超限报错, 绝不静默丢** —— 自举镜的暂存区原先按 8 条布局, 超出的直接丢掉, 而参考
#: 实现无上限: 同一份源码两个实现给出**不同的单元** (2026-09-16 发现, 语料里没有超过
#: 3 条的用例, 所以一直没暴露)。抬到 300 是让 std 那种多模块门面装得下。
MAX_USE = 300


def _store_pick(store: Path, name: str) -> Path | None:
    """自带 store 里取 `<store>/<名字>/<版本>/<名字>.lomt`; 没有这个包返回 None。

    版本那一层**只允许一个**: 编译器不做版本选择 (docs/168 §4.2 —— 多版本是"物化"那层
    解决的, 编译器完全不需要知道版本存在)。有多个就明说并指向 `deps/`, 不猜。
    """
    pkg = store / name
    if not pkg.is_dir():
        return None
    vers = sorted(d.name for d in pkg.iterdir() if d.is_dir())
    if not vers:
        return None
    if len(vers) > 1:
        raise LomError(1, 1, f"自带的库里 {name} 有 {len(vers)} 个版本 ({', '.join(vers)})"
                             f" —— 编译器不做版本选择, 要指定版本就用 deps/{name}/")
    return pkg / vers[0] / f"{name}.lomt"


def resolve_name(name: str, root: Path, proj: Path | None = None,
                 tool_dir: Path | None = None) -> Path:
    """`use <名字>` -> 真实文件。**按层搜, 先命中先用** (像 PYTHONPATH):

      1. `<项目根>/deps/<名字>/<名字>.lomt`  —— 项目本地 (lompi materialize 的落点)
      2. `<工具目录>/../share/lompi/store/<名字>/<版本>/<名字>.lomt`  —— 随包自带
      3. 内置四根 `<仓根>/<根>/<名字>.lomt`  —— **这一层命中必须唯一**

    唯一性只管第 3 层, 而且"找不到"要四层全空才报。这样: 项目本地有 `deps/std`、仓库里
    也有 `lib/std` 时不会假报歧义 (前两层先命中就停), 而仓库自己的四根仍保留"名字不许
    重"那条纪律 —— 它抓的是**作者写错**, 不是使用者选错。
    """
    if proj is not None:
        d = proj / LOCAL_DEPS / name
        if d.is_dir():
            f = d / f"{name}.lomt"
            if not f.exists():
                raise LomError(1, 1, f"依赖 {name} 里没有同名模块 {name}.lomt —— "
                                     f"`use <名字>` 指的是**包内与包同名的那个模块**")
            return f
    if tool_dir is not None:
        f = _store_pick(tool_dir.parent.joinpath(*TOOLCHAIN_STORE), name)
        if f is not None:
            if not f.exists():
                raise LomError(1, 1, f"自带的库里 {name} 缺同名模块 {name}.lomt: {f}")
            return f
    hits = [root / rel / f"{name}.lomt" for rel in NAME_ROOTS
            if (root / rel / f"{name}.lomt").exists()]
    if not hits:
        where = (f"项目本地 {proj / LOCAL_DEPS}、工具链自带的库、以及内置根 "
                 f"{', '.join(NAME_ROOTS)}" if proj is not None
                 else f"内置根 {', '.join(NAME_ROOTS)}")
        raise LomError(1, 1, f"名字导入找不到模块 {name}: {where} 下都没有 {name}.lomt")
    if len(hits) > 1:
        rel = ", ".join(str(h.relative_to(root)).replace("\\", "/") for h in hits)
        raise LomError(1, 1, f"名字导入有歧义 {name}: 命中 {rel} 多处")
    return hits[0]


def resolve_deps(mod: Module, root: Path, base: Path, entry: Path | None = None) -> list[Module]:
    """按依赖序返回导入的 L1 模块 (被依赖者在前), 去重 + 循环检测。

    `proj` 是**项目根** (= 入口文件所在目录), 名字形式的第 1 层 `deps/` 相对**它** ——
    不是相对每个文件: 依赖包里的 `use std` 也得找到**项目根**的 `deps/std/`, 因为 lompi
    把整个闭包摊平在项目根的 `deps/` 下 (照每个文件找会变成 `<包>/deps/std`, 永远找不到)。
    """
    order: list[Module] = []
    seen: set[Path] = set()
    stack: set[Path] = set()
    proj = Path(entry).resolve().parent if entry is not None else base
    tool_dir = Path(__file__).resolve().parent
    if entry is not None:
        rp = Path(entry).resolve()
        seen.add(rp)
        stack.add(rp)

    def visit(m: Module, cur_base: Path) -> None:
        n_use = len(m.imports) + len(m.name_imports)
        if n_use > MAX_USE:
            raise LomError(1, 1, f"模块 {m.name} 的 use 有 {n_use} 条, 超过上限 "
                                 f"{MAX_USE} —— 门面拆小, 别把整库塞进一个文件")
        # 名字形式先落到绝对路径, 之后与路径形式走同一条流水线 (去重/循环/先序)
        paths = list(m.imports) + [str(resolve_name(n, root, proj, tool_dir))
                                   for n in m.name_imports]
        for imp in paths:
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
    mod, deps = prepare(mod, deps)  # M6 单态化
    errs: list[str] = []
    funcs = dict(ext_funcs or {})
    deps = deps or []

    # 依赖模块的导出符号: 先入符号表, 再检查本模块是否重名 (M12: 仅 pub 可见)
    structs: dict[str, Struct] = {}
    enums: dict[str, EnumDecl] = {}
    const_scope: dict[str, str] = {}
    dep_names: set[str] = set()
    dep_private: set[str] = set()
    for d in deps:
        for f in d.funcs:
            (dep_names if f.pub else dep_private).add(f.name)
            if f.pub:
                funcs.setdefault(f.name, f)
        for s in d.structs:
            (dep_names if s.pub else dep_private).add(s.name)
            if s.pub:
                structs.setdefault(s.name, s)
        for e in d.enums:
            (dep_names if e.pub else dep_private).add(e.name)
            if e.pub:
                enums.setdefault(e.name, e)
        for c in d.consts:
            (dep_names if c.pub else dep_private).add(c.name)
            if c.pub:
                const_scope.setdefault(c.name, c.type)

    # ---- 单元级唯一性 (2026-09-11 补): 发射出来的符号名是**平的**。
    # 内核线按名字找入口 (`_start` / `timer_isr` / syscall 包装, 见 docs/155 §3), 所以
    # 私有符号不能靠 mangling 变成模块限定名 —— 平的名字就是 ABI。代价: 同一单元里两个
    # 模块声明同名顶层符号时, 后端会发出**两条 `define @helper`** (非法 IR), 调用点还会
    # 解析到同一个函数 (静默错编)。以前只有"入口模块 vs 依赖的 pub"会报, 依赖之间的私有
    # 重名一路静默 —— 这里补齐。预置枚举 (Option/Result) 由 load() 注入每个模块, 排除。
    # 同一模块内部的重名由下面各自的规则报, 这里只管跨模块。
    seen_decl: dict[str, str] = {}          # name -> 先声明它的模块名
    for m0 in [*deps, mod]:
        decls = [(f.name, f.line, "函数") for f in m0.funcs]
        decls += [(s.name, s.line, "结构体") for s in m0.structs]
        decls += [(e.name, e.line, "枚举") for e in m0.enums if not e.from_prelude]
        decls += [(c.name, c.line, "常量") for c in m0.consts]
        for nm, ln, kind in decls:
            prev = seen_decl.get(nm)
            if prev is None:
                seen_decl[nm] = m0.name
            elif prev != m0.name:
                # 措辞用"重名"—— 与既有的 E-DUP 口径一致 (lomentc_test 的 PY_RULES 按词分类)
                errs.append(f"{ln}: {kind} {nm} 与模块 {prev} 重名 —— "
                            f"单元的发射符号是平的 (ABI), 请改名")


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
        for ex in mod.excluded:  # M41: 出界声明的空间不得被能力使用
            space = ex.split(":")[0].strip()
            if space and space == c.space:
                errs.append(f"{c.line}: 能力 {c.name} 使用了 excluded 的空间 {space}")

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
                    if isinstance(s.target, Index):
                        ot = expr_type(s.target.obj, fscope, funcs, structs)
                        if ot and _is_slice(ot) and not _is_mut_slice(ot):
                            errs.append(f"{s.line}: 只读切片不能写 (形参需声明 mut [T])")
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
                elif isinstance(s, Guard):  # P4: 能力域检查
                    cap = next((c for c in mod.caps if c.name == s.cap), None)
                    _walk_expr(s.expr, fscope, funcs, structs, errs, enums)
                    if cap is None:
                        errs.append(f"{s.line}: guard 引用了未声明的能力 {s.cap}")
                    elif isinstance(s.expr, IntLit) and not (cap.lo <= s.expr.value <= cap.hi):
                        errs.append(
                            f"{s.line}: 能力 {s.cap} 域 [{cap.lo}..{cap.hi}]，索引 {s.expr.value} 越界"
                        )
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
        errs.extend(_move_check(f, funcs, structs, enums))  # M13 移动检查
    return errs


def _is_copy_type(t: str, structs: dict, enums: dict) -> bool:
    """M13: 标量/枚举/切片/str 为 Copy; struct 与数组为非 Copy (移动语义)。"""
    return not (t in structs or _is_array(t))


def _move_check(f: Func, funcs: dict, structs: dict, enums: dict) -> list[str]:
    errs: list[str] = []
    scope: dict[str, str] = {p.name: p.type for p in f.params}
    params = {p.name for p in f.params}
    moved: set[str] = set()

    def use(e) -> None:
        if isinstance(e, Ident):
            if e.name in moved:
                errs.append(f"{e.line}: 变量 {e.name} 已被移动, 不能再用")
        elif isinstance(e, Bin):
            use(e.left)
            use(e.right)
        elif isinstance(e, Un):
            if e.op not in ("&", "&mut"):
                use(e.expr)
        elif isinstance(e, Cast):
            use(e.expr)
        elif isinstance(e, MethodCall):
            use(e.obj)
            for a in e.args:
                use(a)
        elif isinstance(e, Call):
            gf = funcs.get(e.name)
            for i, a in enumerate(e.args):
                pt = gf.params[i].type if gf and i < len(gf.params) else None
                if pt and _is_slice(pt):
                    use(a)  # 切片参数是借用
                elif isinstance(a, Ident) and a.name in scope \
                        and not _is_copy_type(scope[a.name], structs, enums):
                    use(a)
                    moved.add(a.name)  # 非 Copy 传参 = 移动
                else:
                    use(a)
        elif isinstance(e, Index):
            use(e.obj)
            use(e.idx)
        elif isinstance(e, FieldAccess):
            use(e.obj)
        elif isinstance(e, ArrayLit):
            for it in e.items:
                use(it)
        elif isinstance(e, StructLit):
            for _, fe in e.inits:
                use(fe)
        elif isinstance(e, EnumCtor):
            use(e.arg)

    def walk(stmts, sc: dict) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.expr is not None:
                    use(s.expr)
                if isinstance(s.expr, Ident) and s.expr.name in sc \
                        and not _is_copy_type(sc[s.expr.name], structs, enums):
                    moved.add(s.expr.name)
                sc[s.name] = s.type
            elif isinstance(s, Assign):
                use(s.expr)
                if isinstance(s.target, Ident):
                    if s.target.name in moved:
                        errs.append(f"{s.line}: 变量 {s.target.name} 已被移动, 不能赋值")
                    if isinstance(s.expr, Ident) and s.expr.name in sc \
                            and not _is_copy_type(sc[s.expr.name], structs, enums):
                        moved.add(s.expr.name)
                else:
                    use(s.target)
            elif isinstance(s, If):
                use(s.cond)
                walk(s.then, dict(sc))
                walk(s.otherwise, dict(sc))
            elif isinstance(s, While):
                use(s.cond)
                walk(s.body, dict(sc))
            elif isinstance(s, For):
                use(s.lo)
                use(s.hi)
                bs = dict(sc)
                bs[s.var] = "u32"
                walk(s.body, bs)
            elif isinstance(s, Match):
                use(s.subject)
                for _, b in s.arms:
                    walk(b, dict(sc))
            elif isinstance(s, Return):
                use(s.expr)
                # M17: 不得返回局部变量的借用 (悬垂)
                if isinstance(s.expr, Un) and s.expr.op in ("&", "&mut") \
                        and isinstance(s.expr.expr, Ident) and s.expr.expr.name not in params:
                    errs.append(f"{s.line}: 返回局部变量 {s.expr.expr.name} 的借用（悬垂）")
            elif isinstance(s, ExprStmt):
                use(s.expr)
            elif isinstance(s, Guard):
                use(s.expr)

    walk(f.body, scope)
    return errs


# ---------------------------------------------------------------- 无分配审计 (M14)

ALLOC_BUILTINS: tuple = ()  # 语言当前无堆分配; 未来引入时在此登记


def alloc_audit(mod: Module, deps: list[Module] | None = None) -> list[str]:
    """M14: 列出分配点。当前语言按构造即 no-alloc, 恒为空。"""
    hits: list[str] = []
    for m in list(deps or []) + [mod]:
        for f in m.funcs:
            def scan(e) -> None:
                if isinstance(e, Call) and e.name in ALLOC_BUILTINS:
                    hits.append(f"{f.name}:{e.line} {e.name}")
                for child in (
                    getattr(e, "left", None), getattr(e, "right", None), getattr(e, "expr", None),
                    getattr(e, "obj", None), getattr(e, "idx", None), getattr(e, "arg", None),
                ):
                    if child is not None:
                        scan(child)
                for a in getattr(e, "args", []) or []:
                    scan(a)
                for it in getattr(e, "items", []) or []:
                    scan(it)
                for _, fe in getattr(e, "inits", []) or []:
                    scan(fe)

            def scan_stmts(stmts) -> None:
                for s in stmts:
                    for e in (getattr(s, "expr", None), getattr(s, "cond", None),
                              getattr(s, "subject", None), getattr(s, "lo", None),
                              getattr(s, "hi", None), getattr(s, "target", None)):
                        if e is not None:
                            scan(e)
                    for key in ("then", "otherwise", "body"):
                        sub = getattr(s, key, None)
                        if sub:
                            scan_stmts(sub)
                    for _, b in getattr(s, "arms", []) or []:
                        scan_stmts(b)

            scan_stmts(f.body)
    return hits


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
        if e.name == "panic":  # M19: no_std 友好 (宿主路径用 panic!)
            return f'panic!("loment panic {{}}", {a[0]})'
        if e.name == "alloc":  # M15
            return f"__loment_alloc({a[0]})"
        if e.name == "free":
            return "{ let _ = " + a[0] + "; 0u32 }"
        if e.name == "load8":
            return f"(__loment_load8({a[0]}, {a[1]}) as u32)"
        if e.name == "store8":
            return "{ __loment_store8(" + ", ".join(a) + "); 0u32 }"
        if e.name == "atomic_add":  # M21
            return ("unsafe { (*((" + a[0] + ") as *const core::sync::atomic::AtomicU32))"
                    ".fetch_add(" + a[1] + ", core::sync::atomic::Ordering::SeqCst) }")
        if e.name == "get_bits":  # M22
            return f"((({a[0]}) >> ({a[1]})) & (((1u16 << ({a[2]})) - 1) as u8))"
        if e.name == "set_bits":
            return ("{ let __m: u8 = ((1u16 << (" + a[2] + ")) - 1) as u8; "
                    "(((" + a[0] + ") & !(__m << (" + a[1] + ")))"
                    " | ((((" + a[3] + ") & __m) << (" + a[1] + ")))) }")
        if e.name == "inb":  # M20: 端口 I/O (x86, 仅 Rust 路径)
            return ('{ let mut __v: u8 = 0; unsafe { core::arch::asm!("in al, dx", '
                    'in("dx") (' + a[0] + ') as u16, out("al") __v); } __v as u32 }')
        if e.name == "outb":
            return ('{ unsafe { core::arch::asm!("out dx, al", '
                    'in("dx") (' + a[0] + ') as u16, in("al") (' + a[1] + ') as u8); } 0u32 }')
        if e.name == "str_ptr":  # M67
            return f"(({a[0]}).as_ptr() as *mut u8)"
        if e.name == "ptr_add":  # M73
            return f"(({a[0]}) as *mut u8).add(({a[1]}) as usize)"
        if e.name == "ptr_sub":
            return f"(({a[0]}) as *mut u8).sub(({a[1]}) as usize)"
        if e.name == "syscall4":  # M67: Linux 系统调用 (rax/rdi/rsi/rdx)
            return ("{ let mut __r: i64; unsafe { core::arch::asm!(\"syscall\", "
                    "inlateout(\"rax\") (" + a[0] + ") as i64 => __r, "
                    "in(\"rdi\") (" + a[1] + ") as i64, "
                    "in(\"rsi\") (" + a[2] + ") as i64, "
                    "in(\"rdx\") (" + a[3] + ") as i64, "
                    "lateout(\"rcx\") _, lateout(\"r11\") _); } __r }")
        if e.name == "syscall6":  # M72: nr + a0..a4
            return ("{ let mut __r: i64; unsafe { core::arch::asm!(\"syscall\", "
                    "inlateout(\"rax\") (" + a[0] + ") as i64 => __r, "
                    "in(\"rdi\") (" + a[1] + ") as i64, "
                    "in(\"rsi\") (" + a[2] + ") as i64, "
                    "in(\"rdx\") (" + a[3] + ") as i64, "
                    "in(\"r10\") (" + a[4] + ") as i64, "
                    "in(\"r8\") (" + a[5] + ") as i64, "
                    "lateout(\"rcx\") _, lateout(\"r11\") _); } __r }")
        if e.name == "str_len":
            return f"({a[0]}.len() as u32)"
        if e.name == "str_eq":
            return f"({a[0]} == {a[1]})"
        if e.name == "str_concat":  # M2: v0 的 str 是 &'static str -> 只能漏内存
            return (f"(Box::leak(format!(\"{{}}{{}}\", {a[0]}, {a[1]}).into_boxed_str()) "
                    f"as &'static str)")
        return f"({a[0]}.as_bytes()[({a[1]}) as usize] as u32)"
    if isinstance(e, Cast):
        return f"(({_expr_rs(e.expr)}) as {_rust_t(e.type)})"
    if isinstance(e, MethodCall):
        args = [_expr_rs(e.obj)] + [_expr_rs(a) for a in e.args]
        return f"{e.mangled}({', '.join(args)})"
    if isinstance(e, Call):
        return f"{e.name}({', '.join(_expr_rs(a) for a in e.args)})"
    if isinstance(e, Un):
        if e.op == "&mut":
            return f"(&mut {_expr_rs(e.expr)})"
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
            if s.expr is None:  # M9: 未初始化声明
                out.append(f"{pad}let mut {s.name}: {_rust_t(s.type)};")
            else:
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
        elif isinstance(s, Guard):  # P4: 能力域守卫 (编译期已查字面量)
            out.append(
                f"{pad}{{ let __i: u64 = ({_expr_rs(s.expr)}) as u64; "
                f"__loment_guard({s.cap_id}, __i, {s.lo}, {s.hi}); }}"
            )
        else:
            raise AssertionError(f"未知语句 {s!r}")
    return out


def _rust_peq(ty: str) -> bool:
    """这个类型在**生成的 Rust 里**到底有没有 `PartialEq`。

    用在枚举的 derive 上: Rust 为枚举生成的 `impl PartialEq` 会要求载荷类型也可比较,
    而我们对 struct 只发 `Clone, Copy`(见下面 struct 那一段) —— 于是载荷是 struct 时,
    Rust 会把源码里根本没写过的 `Span == Span` 编出来, 报 E0369 (2026-09-15 用户实测)。

    **判据必须和"实际发了什么 derive"一致**, 不能是"理论上能不能派生" —— 我第一版就
    写成了后者(拿 struct 的字段递归算), 结果说"Span 可以", 于是一边发 PartialEq 一边
    不发, 照旧 E0369。这里只认我们真的会发 PartialEq 的那些类型。
    """
    if ty in ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "bool", "str"):
        return True           # str 在 Rust 侧是 &'static str, 有 PartialEq
    if _is_slice(ty):
        return _rust_peq(_slice_elem(ty))
    if ty.startswith("[") and ty.endswith("]"):
        return _rust_peq(ty[1:ty.rindex(";")].strip())
    return False              # ptr / struct / 未知 —— 我们都不发 PartialEq


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
    if mod.caps:  # P4/M35: 域描述表 (内核可迭代)
        out.append("#[derive(Clone, Copy)]")
        out.append("pub struct CapDomain { pub space: &'static str, pub lo: u64, pub hi: u64, pub revocable: bool }")
        out.append("pub static CAP_DOMAINS: &[CapDomain] = &[")
        for c in mod.caps:
            rev = "true" if c.revocable else "false"
            out.append(f'    CapDomain {{ space: "{c.space}", lo: {c.lo}, hi: {c.hi}, revocable: {rev} }},')
        out.append("];")
        out.append("")
    for c in mod.consts:
        out.append(f"pub const {c.name}: {c.type} = {c.value};")
    if mod.consts:
        out.append("")
    for e in mod.enums:
        # `PartialEq` 不能无条件发: Rust 为枚举生成的 `impl PartialEq` 会**要求载荷类型也
        # PartialEq** —— 载荷是 struct 时, 而 struct 只有 `Clone, Copy`(见下), 于是报
        # E0369 `&Span` 不能比较, 可源码里根本没写过那次比较 (2026-09-15 用户实测)。
        # 所以先算"载荷全都能派生吗", 能才发 —— 对现有程序零变化。
        peq = all(_rust_peq(p) for p in e.payloads.values())
        out.append(f"#[derive(Clone, Copy{', PartialEq' if peq else ''})]")
        out.append(f"pub enum {e.name} {{")
        for v in e.variants:
            p = e.payloads.get(v)
            out.append(f"    {v}({p})," if p else f"    {v},")
        out.append("}")
        out.append("")
    drop_types = {im.type for im in mod.impls if im.trait == "Drop"}  # M16
    for s in mod.structs:
        if s.name not in drop_types:
            out.append("#[derive(Clone, Copy)]")
        out.append(f"pub struct {s.name} {{")
        for fn, ft in s.fields:
            out.append(f"    pub {fn}: {ft},")
        out.append("}")
        out.append("")
    # M16: Drop 走 Rust 原生 impl (不按普通函数发射)
    drop_names: set[str] = set()
    for im in mod.impls:
        if im.trait != "Drop":
            continue
        df = next((x for x in im.funcs if x.name.endswith("_drop")), None)
        if df is None:
            continue
        drop_names.add(df.name)
        out.append(f"impl Drop for {im.type} {{")
        out.append("    fn drop(&mut self) {")
        out += [ln.replace("__self", "self") for ln in _stmts_rs(df.body, 2)]
        out.append("    }")
        out.append("}")
        out.append("")
    for f in mod.funcs:
        if f.name in drop_names:
            continue
        if f.interrupt:  # M33: Rust 稳定版无 x86-interrupt, 仅注释标记
            out.append(f"// M33: {f.name} 是中断处理函数 (IR 路径用 x86_intrcc; Rust 路径需 nightly)")
        args = ", ".join(f"{p.name}: {_rust_t(p.type)}" for p in f.params)
        out.append(f"pub fn {f.name}({args}) -> {_rust_t(f.ret)} {{")
        out += _stmts_rs(f.body, 1)
        out.append("}")
        out.append("")
    return out


def emit_rust(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    """依赖模块先出 (每个一次), 本模块在后 —— 单文件 Rust 产物。"""
    mod, deps = prepare(mod, deps)  # M6
    out = [BANNER, f"// module {mod.name} (loment v0 -> rust)", _RUST_RUNTIME]
    for d in deps or []:
        out.append("")
        out.append(f"// ==== 导入模块 {d.name} ====")
        out += _rust_body(d, lom_root)
    out.append("")
    out.append(f"// ==== 本模块 {mod.name} ====")
    out += _rust_body(mod, lom_root)
    return "\n".join(out).rstrip() + "\n"


def _count_guards(mod: Module) -> int:
    """M50: 审计站点数 —— 形式对象必须自描述 guard 数量 (不读源码即可断言 A2)。"""
    def walk(stmts: list) -> int:
        n = 0
        for s in stmts:
            if isinstance(s, Guard):
                n += 1
            elif isinstance(s, If):
                n += walk(s.then) + walk(s.otherwise)
            elif isinstance(s, (While, For)):
                n += walk(s.body)
            elif isinstance(s, Match):
                n += sum(walk(b) for _, b in s.arms)
        return n

    return sum(walk(f.body) for f in mod.funcs)


def emit_potato(mod: Module, lom_root: Path, deps: list[Module] | None = None) -> str:
    """M45: 形式对象 v1 —— 覆盖泛型/切片/字符串, 并由独立校验器自检 (M46)。"""
    import copy as _c

    raw_mod, raw_deps = _c.deepcopy(mod), _c.deepcopy(list(deps or []))
    mod, deps = prepare(mod, deps)  # M6
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
    # M45: 泛型声明 (单态化前的本单元视图) + 单态化实例 (prepare 标记)
    generics = [{"kind": "fn", "name": f.name, "params": list(f.tparams)}
                for f in raw_mod.funcs if f.tparams]
    generics += [{"kind": "type", "name": s.name, "params": list(s.tparams)}
                 for s in raw_mod.structs if s.tparams]
    generics += [{"kind": "type", "name": e.name, "params": list(e.tparams)}
                 for e in raw_mod.enums if e.tparams]
    instances = [{"kind": "fn", "name": f.name, "of": f.from_generic,
                  "args": list(f.generic_args)} for f in mod.funcs if f.from_generic]
    instances += [{"kind": "type", "name": s.name, "of": s.from_generic,
                   "args": list(s.generic_args)} for s in mod.structs if s.from_generic]
    instances += [{"kind": "type", "name": e.name, "of": e.from_generic,
                   "args": list(e.generic_args)} for e in mod.enums if e.from_generic]
    doc = {
        "potato": "v1",
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
        "traits": [{"name": t.name, "methods": [m[0] for m in t.methods]}
                   for t in raw_mod.traits],
        "impls": [{"trait": i.trait, "for": i.type,
                   "methods": [f.name[len(i.type) + 1:]
                               if f.name.startswith(i.type + "_") else f.name
                               for f in i.funcs]}
                  for i in raw_mod.impls],
        "generics": generics,
        "instances": instances,
        "guards": _count_guards(mod),
        "excluded": list(mod.excluded),
    }
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    # M46: 编译器强制导出 —— 形式对象必须通过独立校验器, 否则编译失败。
    import potato as _potato
    errs = _potato.validate(doc)
    if errs:
        raise LomError(1, 1, "形式对象自检失败 (Potato v1): " + "; ".join(errs[:5]))
    return text


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
    if t == "ptr":  # M15: 不透明指针
        return "ptr"
    if t == "()":  # M16: unit
        return "void"
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


def _collect_locals(f: Func, enums: dict | None = None,
                    funcs: dict | None = None,
                    structs: dict | None = None) -> list[tuple[str, str, int]]:
    """按序收集需 alloca 的局部 (不含参数, 去重) 与它们的**声明行**。

    行号是 M59 的变量信息要的 (`DILocalVariable.line`); For 变量与 match 绑定按上下文推断。

    **funcs / structs 必须传进来**(2026-09-15 修): 这里要推 `match` 主体与 `for` 上下界的
    类型, 而被匹配值可能是**函数调用**、上下界可能是**字段访问** —— 给 `expr_type` 传空字典
    会让它推不出类型, 于是枚举查不到、绑定变量根本不会被收集, 后面发射期 `self.vars[bind]`
    直接 KeyError 崩掉(Python 栈回溯, 不是诊断)。当时只有 `use g()` 这种"主体是内联调用"
    的写法会踩到, 所以 `let r = g(); if let ... = r` 看着没事。
    """
    enums = enums or {}
    funcs = funcs or {}
    structs = structs or {}
    out: list[tuple[str, str, int]] = []
    seen = {p.name for p in f.params}
    scope: dict[str, str] = {p.name: p.type for p in f.params}

    def walk(stmts: list) -> None:
        for s in stmts:
            if isinstance(s, Let):
                if s.name not in seen:
                    out.append((s.name, s.type, s.line))
                    seen.add(s.name)
                scope[s.name] = s.type
            elif isinstance(s, If):
                walk(s.then)
                walk(s.otherwise)
            elif isinstance(s, While):
                walk(s.body)
            elif isinstance(s, For):
                vt = (expr_type(s.lo, scope, funcs, structs)
                      or expr_type(s.hi, scope, funcs, structs) or "u32")
                if s.var not in seen:
                    out.append((s.var, vt, s.line))
                    seen.add(s.var)
                scope[s.var] = vt
                walk(s.body)
            elif isinstance(s, Match):
                sty = expr_type(s.subject, scope, funcs, structs)
                ed = enums.get(sty) if sty else None
                for pat, body in s.arms:
                    if pat is not None and pat.bind and ed is not None:
                        pt = ed.payloads.get(pat.variant)
                        if pt and pat.bind not in seen:
                            out.append((pat.bind, pt, pat.line))
                            seen.add(pat.bind)
                        if pt:
                            scope[pat.bind] = pt
                    walk(body)

    walk(f.body)
    return out


class _Ir:
    """结构化发射: alloca/load/store + 基本块; 优化交给 clang (docs/144 §3)。"""

    def __init__(self, funcs: dict, consts: dict, f: Func,
                 structs: dict | None = None, enums: dict | None = None,
                 coverage: bool = False, cov_counter: list | None = None,
                 dbg_scope: int | None = None, dbg_lines: dict | None = None,
                 dbg_meta: list | None = None, dbg_types: dict | None = None):
        self.funcs, self.consts, self.f = funcs, consts, f
        self.structs = structs or {}
        self.enums = enums or {}
        self.out: list[str] = []
        self.tmp = 0
        self.lbl = 0
        self.vars: dict[str, tuple[str, str]] = {}  # 名 -> (loment 类型, alloca)
        self.globals: list[str] = []                # 字符串常量等模块级声明
        self.terminated = False
        self.coverage = coverage                    # M63: IR 级块覆盖
        self.cov_counter = cov_counter if cov_counter is not None else [0]
        self.cov_ids: list[int] = []
        self.dbg_scope = dbg_scope                  # M59: 本函数的 DISubprogram
        self.dbg_lines = dbg_lines if dbg_lines is not None else {}
        self.dbg_meta = dbg_meta                    # M59: 共享元数据行
        self.dbg_types = dbg_types if dbg_types is not None else {}  # M59: 类型 -> DIBasicType
        self.dbg_loc: int | None = None             # 当前语句的 DILocation
        self.cur_label: str | None = None           # 当前基本块标签 (phi 前驱用)

    def dbg_for(self, line: int) -> int | None:
        """M59: 行号 -> DILocation id (每函数一份, 由 emit_llvm 分配)。"""
        if self.dbg_scope is None or self.dbg_meta is None:
            return None
        if line not in self.dbg_lines:
            i = len(self.dbg_meta)
            self.dbg_meta.append(
                f"!{i} = !DILocation(line: {line}, column: 1, scope: !{self.dbg_scope})")
            self.dbg_lines[line] = i
        return self.dbg_lines[line]

    def dbg_ty(self, ty: str) -> int:
        """M59: 局部变量类型 -> DIBasicType (每类型一份, 跨函数共享)。"""
        if ty in self.dbg_types:
            return self.dbg_types[ty]
        if ty == "bool":
            size, enc = 8, "DW_ATE_boolean"
        elif ty == "ptr":
            size, enc = 64, "DW_ATE_address"
        elif ty in ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"):
            size = int(ty[1:])
            enc = "DW_ATE_signed" if ty[0] == "i" else "DW_ATE_unsigned"
        else:  # str / 切片 / 数组 / 结构体 / 枚举: 只记名字, 尺寸交给 LLVM (size: 0)
            size, enc = 0, "DW_ATE_unsigned"
        i = len(self.dbg_meta)
        self.dbg_meta.append(
            f'!{i} = !DIBasicType(name: "{ty}", size: {size}, encoding: {enc})')
        self.dbg_types[ty] = i
        return i

    def dbg_declare(self, name: str, ty: str, ptr: str, line: int, arg: int = 0) -> None:
        """M59: 变量声明点 —— DILocalVariable 元数据 + 一条 `#dbg_declare` 记录。

        用**新式调试记录**而不是旧内建 `llvm.dbg.declare`: 本工具链的 clang 22 自己发射的
        就是记录形态 (探针实测), 记录不需内建声明, 且附着于**下一条指令** —— 序言里每个
        alloca 后面总还有指令 (至少函数体或收尾的 ret), 所以位置安全。
        记录**不能**带 `!dbg` 后缀 (`self.w` 会加), 所以直接写进 self.out。
        """
        if self.dbg_scope is None or self.dbg_meta is None:
            return
        loc = self.dbg_for(line)
        if loc is None:
            return
        vt = self.dbg_ty(ty)
        i = len(self.dbg_meta)
        a = f", arg: {arg}" if arg > 0 else ""
        self.dbg_meta.append(
            f'!{i} = !DILocalVariable(name: "{name}"{a}, scope: !{self.dbg_scope}, '
            f'file: !1, line: {line}, type: !{vt})')
        self.out.append(f"  #dbg_declare(ptr {ptr}, !{i}, !DIExpression(), !{loc})")

    def cov_hit(self) -> None:
        """M63: 在每个基本块开头对 @__loment_cov[块号] 加一。"""
        if not self.coverage:
            return
        idx = self.cov_counter[0]
        self.cov_counter[0] += 1
        self.cov_ids.append(idx)
        if idx >= 256:
            raise LomError(self.f.line, 1, "覆盖计数块数超过 256 (M63 上限)")
        g, v, n = self.t(), self.t(), self.t()
        self.w(f"{g} = getelementptr inbounds [256 x i64], ptr @__loment_cov, i32 0, i32 {idx}")
        self.w(f"{v} = load i64, ptr {g}")
        self.w(f"{n} = add i64 {v}, 1")
        self.w(f"store i64 {n}, ptr {g}")

    def ll(self, t: str) -> str:
        return _ll_type(t, self.structs, self.enums)

    def t(self) -> str:
        self.tmp += 1
        return f"%t{self.tmp}"

    def l(self, tag: str) -> str:
        self.lbl += 1
        return f"L{self.lbl}_{tag}"

    def w(self, s: str) -> None:
        if self.dbg_loc is not None:
            s += f", !dbg !{self.dbg_loc}"
        self.out.append("  " + s)

    def w_raw(self, s: str) -> None:
        """追加一行**不加** `!dbg` 后缀的文本 —— 给**多行指令的续行**用。

        `!dbg` 必须挂在整条指令的末尾 (对 `switch` 就是 `]` 那一行); 逐行都挂会写出
        `switch i32 %x, label %L [, !dbg !7` 这种非法 IR —— 这是 M59 收口时抓到的既有 bug
        (旧用例只喂没有 `match` 的语料, 从未触发)。
        """
        self.out.append("  " + s)

    def label(self, name: str) -> None:
        self.out.append(f"{name}:")
        self.terminated = False
        self.cur_label = name
        self.cov_hit()

    def jump(self, name: str) -> None:
        if not self.terminated:
            self.w(f"br label %{name}")
        self.terminated = True

    def type_scope(self) -> dict[str, str]:
        return {k: v[0] for k, v in self.vars.items()}

    # -- 表达式 -> (loment 类型, 值)
    def expr(self, e, want: str | None = None) -> tuple[str, str]:
        if isinstance(e, IntLit):
            if want == "ptr":  # M73: 空指针字面量
                return "ptr", "null"
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
            if e.op in ("&", "&mut"):  # M3/M4: 数组 -> 切片值
                if not isinstance(e.expr, Ident):
                    raise LomError(e.line, 1, f"native: {e.op} 只支持数组变量")
                ty, ptr = self.vars[e.expr.name]
                if not _is_array(ty):
                    raise LomError(e.line, 1, f"native: {ty} 不是数组")
                p0 = self.t()
                self.w(f"{p0} = getelementptr inbounds {self.ll(ty)}, ptr {ptr}, i32 0, i32 0")
                v1 = self.t()
                self.w(f"{v1} = insertvalue {{ ptr, i64 }} undef, ptr {p0}, 0")
                v2 = self.t()
                self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v1}, i64 {_array_len(ty)}, 1")
                st = f"mut [{_array_elem(ty)}]" if e.op == "&mut" else f"[{_array_elem(ty)}]"
                return st, v2
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
        if isinstance(e, Cast):  # 整数/布尔转换
            st = expr_type(e.expr, self.type_scope(), self.funcs, {})
            _, v = self.expr(e.expr, None)
            if st == e.type:
                return e.type, v
            if st == "ptr" and e.type in ("u64", "i64"):  # M67: 指针转整数
                r = self.t()
                self.w(f"{r} = ptrtoint ptr {v} to {self.ll(e.type)}")
                return e.type, r
            # M83: 整数 -> 指针 (brk/mmap 取内存)。**整型字面量也算** —— 它的类型是 None,
            # 不特判就会落到下面那条通用分支发成 `zext i32 0 to ptr`, 而 zext 产不出指针,
            # 那是**非法 LLVM**。镜发的是 `inttoptr i32 0 to ptr` (字面量按默认宽度 u32),
            # 这条对齐它。2026-09-15 由 lompi 的 `0 as ptr` 实测抓到。
            if e.type == "ptr" and (st in INT_TYPES or (st is None and isinstance(e.expr, IntLit))):
                r = self.t()
                self.w(f"{r} = inttoptr {self.ll(st or 'u32')} {v} to ptr")
                return "ptr", r
            si, di = self.ll(st or "u32"), self.ll(e.type)
            if si == di:  # 同宽异名 (u64 <-> i64): LLVM 里是同一个类型, 再 cast 是非法 IR
                return e.type, v
            sw, dw = _bit_width(st or "u32"), _bit_width(e.type)
            op = "trunc" if dw < sw else ("sext" if (st in _SIGNED) else "zext")
            r = self.t()
            self.w(f"{r} = {op} {si} {v} to {di}")
            return e.type, r
        if isinstance(e, MethodCall):  # M8: 静态派发
            fn = self.funcs.get(e.mangled)
            if fn is None:
                raise LomError(e.line, 1, f"native: 未解析的方法 {e.name}")
            _, ov = self.expr(e.obj, fn.params[0].type)
            args = [f"{self.ll(fn.params[0].type)} {ov}"]
            for a, p in zip(e.args, fn.params[1:]):
                _, av = self.expr(a, p.type)
                args.append(f"{self.ll(p.type)} {av}")
            if fn.ret == "()":
                self.w(f"call void @{fn.name}({', '.join(args)})")
                return "()", ""
            r = self.t()
            self.w(f"{r} = call {self.ll(fn.ret)} @{fn.name}({', '.join(args)})")
            return fn.ret, r
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
            if fn.ret == "()":  # void 调用不能带名字
                self.w(f"call void @{fn.name}({', '.join(args)})")
                return "()", ""
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
        lt = expr_type(e.left, scope, self.funcs, {})
        rt = expr_type(e.right, scope, self.funcs, {})
        if e.op in ("==", "!=") and (lt == "ptr" or rt == "ptr"):  # M73: 指针比较
            _, a = self.expr(e.left, "ptr")
            _, b = self.expr(e.right, "ptr")
            r = self.t()
            self.w(f"{r} = icmp {e.op} ptr {a}, {b}")
            return "bool", r
        if lt == "ptr" and e.op in ("+", "-") and rt != "ptr":  # M73: 指针算术 (字节)
            _, a = self.expr(e.left, "ptr")
            _, n = self.expr(e.right, "u64")
            if e.op == "-":
                nn = self.t()
                self.w(f"{nn} = sub i64 0, {n}")
                n = nn
            r = self.t()
            self.w(f"{r} = getelementptr inbounds i8, ptr {a}, i64 {n}")
            return "ptr", r
        ty = (lt or rt or want or "u32")
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
            rhs_pred = self.cur_label or "entry"
            self.jump(end_l)
            self.label(short_l)
            self.jump(end_l)
            self.label(end_l)
            short_v = "false" if op == "&&" else "true"
            r = self.t()
            self.w(f"{r} = phi i1 [ {b}, %{rhs_pred} ], [ {short_v}, %{short_l} ]")
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
            # M18: 除零 = 运行时 trap (与 Rust 路径的 panic 对齐)
            z = self.t()
            self.w(f"{z} = icmp eq {it} {b}, 0")
            ok_l, trap_l, end_l = self.l("dok"), self.l("dtrap"), self.l("dend")
            self.w(f"br i1 {z}, label %{trap_l}, label %{ok_l}")
            self.terminated = True
            self.label(trap_l)
            self.w("call void @__loment_abort()")
            self.w("unreachable")
            self.terminated = True
            self.label(ok_l)
            self.w(f"{r} = {ins} {it} {a}, {b}")
            self.jump(end_l)
            self.label(end_l)
            return ty, r
        if op in ("<<", ">>"):
            ins = "shl" if op == "<<" else ("ashr" if signed else "lshr")
            self.w(f"{r} = {ins} {it} {a}, {b}")
            return ty, r
        raise LomError(e.line, 1, f"native M0 不支持的运算符 {op}")

    # -- 内建 (M1/M2)
    def alloc_ir(self, size_var: str) -> str:
        """bump 堆分配 `size_var` 字节, 返回指针 (alloc 与 str_concat 共用, M15/M2)。"""
        off = self.t()
        self.w(f"{off} = load i32, ptr @__loment_off")
        nxt = self.t()
        self.w(f"{nxt} = add i32 {off}, {size_var}")
        ok = self.t()
        self.w(f"{ok} = icmp ule i32 {nxt}, 65536")
        aok, aovf = self.l("aok"), self.l("aovf")
        self.w(f"br i1 {ok}, label %{aok}, label %{aovf}")
        self.terminated = True
        self.label(aovf)
        self.w("call void @__loment_abort()")
        self.w("unreachable")
        self.terminated = True
        self.label(aok)
        self.w(f"store i32 {nxt}, ptr @__loment_off")
        p = self.t()
        self.w(f"{p} = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 {off}")
        return p

    def builtin(self, e: Call) -> tuple[str, str]:
        """str_len / str_eq / str_concat / str_byte / slice_len 的 IR 降级。"""
        if e.name == "alloc":  # M15: bump 分配器
            _, sz = self.expr(e.args[0], "u32")
            return "ptr", self.alloc_ir(sz)
        if e.name == "free":
            self.expr(e.args[0], "ptr")
            return "u32", "0"
        if e.name == "load8":
            _, pv = self.expr(e.args[0], "ptr")
            _, ov = self.expr(e.args[1], "u32")
            g = self.t()
            self.w(f"{g} = getelementptr i8, ptr {pv}, i32 {ov}")
            b8 = self.t()
            self.w(f"{b8} = load i8, ptr {g}")
            r = self.t()
            self.w(f"{r} = zext i8 {b8} to i32")
            return "u32", r
        if e.name == "store8":
            _, pv = self.expr(e.args[0], "ptr")
            _, ov = self.expr(e.args[1], "u32")
            _, vv = self.expr(e.args[2], "u8")
            g = self.t()
            self.w(f"{g} = getelementptr i8, ptr {pv}, i32 {ov}")
            self.w(f"store i8 {vv}, ptr {g}")
            return "u32", "0"
        if e.name == "atomic_add":  # M21
            _, pv = self.expr(e.args[0], "ptr")
            _, vv = self.expr(e.args[1], "u32")
            r = self.t()
            self.w(f"{r} = atomicrmw add ptr {pv}, i32 {vv} seq_cst")
            return "u32", r
        if e.name in ("get_bits", "set_bits"):  # M22: 位域
            _, bv = self.expr(e.args[0], "u8")
            _, sv = self.expr(e.args[1], "u32")
            _, wv = self.expr(e.args[2], "u32")
            one = self.t()
            self.w(f"{one} = zext i8 1 to i16")
            w16 = self.t()
            self.w(f"{w16} = trunc i32 {wv} to i16")
            shl = self.t()
            self.w(f"{shl} = shl i16 {one}, {w16}")
            sub = self.t()
            self.w(f"{sub} = sub i16 {shl}, 1")
            mask = self.t()
            self.w(f"{mask} = trunc i16 {sub} to i8")
            if e.name == "get_bits":
                s8 = self.t()
                self.w(f"{s8} = trunc i32 {sv} to i8")
                shr = self.t()
                self.w(f"{shr} = lshr i8 {bv}, {s8}")
                r = self.t()
                self.w(f"{r} = and i8 {shr}, {mask}")
                return "u8", r
            _, vv = self.expr(e.args[3], "u8")
            nsh = self.t()
            self.w(f"{nsh} = trunc i32 {sv} to i8")
            shm = self.t()
            self.w(f"{shm} = shl i8 {mask}, {nsh}")
            nm = self.t()
            self.w(f"{nm} = xor i8 {shm}, -1")
            cleared = self.t()
            self.w(f"{cleared} = and i8 {bv}, {nm}")
            masked = self.t()
            self.w(f"{masked} = and i8 {vv}, {mask}")
            shifted = self.t()
            self.w(f"{shifted} = shl i8 {masked}, {nsh}")
            r = self.t()
            self.w(f"{r} = or i8 {cleared}, {shifted}")
            return "u8", r
        if e.name in ("inb", "outb"):  # M20: 仅 Rust 路径
            raise LomError(e.line, 1, f"native: {e.name} 暂未在 IR 后端实现 (M20 仅 Rust 路径)")
        if e.name == "str_ptr":  # M67
            _, sv = self.expr(e.args[0], "str")
            p = self.t()
            self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
            return "ptr", p
        if e.name in ("ptr_add", "ptr_sub"):  # M73
            _, pv = self.expr(e.args[0], "ptr")
            _, nv = self.expr(e.args[1], "u32")
            n64 = self.t()
            self.w(f"{n64} = zext i32 {nv} to i64")
            if e.name == "ptr_sub":
                nn = self.t()
                self.w(f"{nn} = sub i64 0, {n64}")
                n64 = nn
            r = self.t()
            self.w(f"{r} = getelementptr inbounds i8, ptr {pv}, i64 {n64}")
            return "ptr", r
        if e.name == "syscall4":  # M67: Linux 系统调用
            vals = [self.expr(a, "u64")[1] for a in e.args]
            r = self.t()
            self.w(f'{r} = call i64 asm sideeffect "syscall", '
                   f'"={{ax}},{{ax}},{{di}},{{si}},{{dx}},~{{cx}},~{{r11}},~{{memory}}"'
                   f"(i64 {vals[0]}, i64 {vals[1]}, i64 {vals[2]}, i64 {vals[3]})")
            return "i64", r
        if e.name == "syscall6":  # M72: nr + a0..a4 (r10/r8)
            vals = [self.expr(a, "u64")[1] for a in e.args]
            r = self.t()
            self.w(f'{r} = call i64 asm sideeffect "syscall", '
                   f'"={{ax}},{{ax}},{{di}},{{si}},{{dx}},{{r10}},{{r8}},~{{cx}},~{{r11}},'
                   f'~{{memory}}"'
                   f"(i64 {vals[0]}, i64 {vals[1]}, i64 {vals[2]}, i64 {vals[3]}, "
                   f"i64 {vals[4]}, i64 {vals[5]})")
            return "i64", r
        if e.name == "panic":  # M19
            self.expr(e.args[0], "u32")
            self.w("call void @__loment_abort()")
            self.w("unreachable")
            self.terminated = True
            self.label(self.l("dead"))
            return "u32", "0"
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
            self.w(f"{c} = call i32 @__loment_memcmp(ptr {ap}, ptr {bp}, i64 {al})")
            z = self.t()
            self.w(f"{z} = icmp eq i32 {c}, 0")
            self.jump(end)
            self.label(neq)
            self.jump(end)
            self.label(end)
            r = self.t()
            self.w(f"{r} = phi i1 [ {z}, %{chk} ], [ false, %{neq} ]")
            return "bool", r
        # M2: str_concat —— 拼到 bump 堆上 (v0 不做回收, 与 Rust 路径的 leak 同语义)
        if e.name == "str_concat":
            _, a = self.expr(e.args[0], "str")
            _, b = self.expr(e.args[1], "str")
            ap, al = self.t(), self.t()
            self.w(f"{ap} = extractvalue {{ ptr, i64 }} {a}, 0")
            self.w(f"{al} = extractvalue {{ ptr, i64 }} {a}, 1")
            bp, bl = self.t(), self.t()
            self.w(f"{bp} = extractvalue {{ ptr, i64 }} {b}, 0")
            self.w(f"{bl} = extractvalue {{ ptr, i64 }} {b}, 1")
            n = self.t()
            self.w(f"{n} = add i64 {al}, {bl}")
            nu = self.t()
            self.w(f"{nu} = trunc i64 {n} to i32")   # 堆计数是 u32
            p = self.alloc_ir(nu)
            self.w(f"call void @__loment_memcpy(ptr {p}, ptr {ap}, i64 {al})")
            g = self.t()
            self.w(f"{g} = getelementptr i8, ptr {p}, i64 {al}")
            self.w(f"call void @__loment_memcpy(ptr {g}, ptr {bp}, i64 {bl})")
            v1 = self.t()
            self.w(f"{v1} = insertvalue {{ ptr, i64 }} undef, ptr {p}, 0")
            v2 = self.t()
            self.w(f"{v2} = insertvalue {{ ptr, i64 }} {v1}, i64 {n}, 1")
            return "str", v2
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
        line = getattr(s, "line", None)
        if line:
            loc = self.dbg_for(line)
            if loc is not None:
                self.dbg_loc = loc
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
                raise LomError(s.line, 1, "native: 只支持数组/切片变量取下标赋值")
            ty, ptr = self.vars[base.name]
            if _is_slice(ty):
                if not _is_mut_slice(ty):
                    raise LomError(s.line, 1, f"native M4: {ty} 是只读切片，不能写")
                et = _slice_elem(ty)
                _, sv = self.expr(base, ty)
                p = self.t()
                self.w(f"{p} = extractvalue {{ ptr, i64 }} {sv}, 0")
                _, iv = self.expr(s.target.idx, "u32")
                gp = self.t()
                self.w(f"{gp} = getelementptr inbounds {self.ll(et)}, ptr {p}, i32 {iv}")
                _, v = self.expr(s.expr, et)
                self.w(f"store {self.ll(et)} {v}, ptr {gp}")
                return
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
            self.w_raw(f"switch i32 {tag}, label %{wild_l} [")
            for idx, lbl in cases:
                self.w_raw(f"    i32 {idx}, label %{lbl}")
            self.w("  ]")   # !dbg 挂整条指令末尾 (多行 switch 的最后一行)
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
            if s.expr is None:  # M9: 未初始化 (alloca 已在入口块)
                return
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
        if isinstance(s, Guard):  # P4/M36+M38: 域检查 + 审计
            _, iv = self.expr(s.expr, "u32")
            v64 = self.t()
            self.w(f"{v64} = zext i32 {iv} to i64")
            ge = self.t()
            self.w(f"{ge} = icmp uge i64 {v64}, {s.lo}")
            le = self.t()
            self.w(f"{le} = icmp ule i64 {v64}, {s.hi}")
            ok = self.t()
            self.w(f"{ok} = and i1 {ge}, {le}")
            gok, gtrap = self.l("gok"), self.l("gtrap")
            self.w(f"br i1 {ok}, label %{gok}, label %{gtrap}")
            self.terminated = True
            self.label(gtrap)
            self.w("call void @__loment_abort()")
            self.w("unreachable")
            self.terminated = True
            self.label(gok)
            p = self.t()
            self.w(f"{p} = getelementptr [16 x i64], ptr @__loment_audit, i32 0, i32 {s.cap_id}")
            old = self.t()
            self.w(f"{old} = load i64, ptr {p}")
            new = self.t()
            self.w(f"{new} = add i64 {old}, 1")
            self.w(f"store i64 {new}, ptr {p}")
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
                  structs: dict | None = None, enums: dict | None = None,
                  coverage: bool = False, cov_counter: list | None = None,
                  dbg_scope: int | None = None, dbg_lines: dict | None = None,
                  dbg_meta: list | None = None, dbg_types: dict | None = None
                  ) -> tuple[list[str], str]:
    ir = _Ir(funcs, consts, f, structs, enums, coverage, cov_counter,
             dbg_scope, dbg_lines, dbg_meta, dbg_types)
    if f.interrupt:  # M33: x86_intrcc 需要中断帧指针
        ir.out.append(f"; {f.name} -> interrupt (x86_intrcc)")
        ir.out.append(f"define x86_intrcc void @{f.name}(ptr byval([8 x i8]) %__frame)"
                      f"{f' !dbg !{dbg_scope}' if dbg_scope is not None else ''} {{")
        ir.out.append("entry:")
        ir.cur_label = "entry"
        ir.cov_hit()
        for name, ty, ln in _collect_locals(f, enums, funcs, structs):
            ir.w(f"%{name}.addr = alloca {ir.ll(ty)}")
            ir.vars[name] = (ty, f"%{name}.addr")
            ir.dbg_declare(name, ty, f"%{name}.addr", ln)
        ir.block(f.body)
        if not ir.terminated:
            ir.w("ret void")
        ir.out.append("}")
        return ir.globals, "\n".join(ir.out)
    args = ", ".join(f"{ir.ll(p.type)} %{p.name}" for p in f.params)
    ir.out.append(f"; {f.name} -> {f.ret}")
    ir.out.append(f"define {ir.ll(f.ret)} @{f.name}({args})"
                  f"{f' !dbg !{dbg_scope}' if dbg_scope is not None else ''} {{")
    ir.out.append("entry:")
    ir.cur_label = "entry"
    ir.cov_hit()
    for p in f.params:  # 参数与局部统一提升到入口块, 避免循环内反复分配
        ir.w(f"%{p.name}.addr = alloca {ir.ll(p.type)}")
        ir.vars[p.name] = (p.type, f"%{p.name}.addr")
    for j, p in enumerate(f.params):  # M59: 形参也声明, arg 从 1 起 (DWARF 约定)
        ir.dbg_declare(p.name, p.type, f"%{p.name}.addr", f.line, j + 1)
    for name, ty, ln in _collect_locals(f, enums, funcs, structs):
        ir.w(f"%{name}.addr = alloca {ir.ll(ty)}")
        ir.vars[name] = (ty, f"%{name}.addr")
        ir.dbg_declare(name, ty, f"%{name}.addr", ln)
    for p in f.params:
        ir.w(f"store {ir.ll(p.type)} %{p.name}, ptr %{p.name}.addr")
    ir.block(f.body)
    if not ir.terminated:
        if f.ret == "()":
            ir.w("ret void")
        else:
            ir.w("unreachable")  # ponytail: 语言不强制全路径 return (docs/144 §3)
    ir.out.append("}")
    return ir.globals, "\n".join(ir.out)


def emit_llvm(mod: Module, lom_root: Path, deps: list[Module] | None = None,
              coverage: bool = False, debug: bool = False) -> str:
    """原生后端: LLVM IR。M0 标量 + M23–M26 聚合 + M1/M2 str + M3/M4 切片。"""
    mod, deps = prepare(mod, deps)  # M6
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
            # 聚合参数/返回值在原生路径可用 (LLVM 结构体按值), 但不保证 C ABI — 勿从 C 直接调用
            _ll_type(f.ret, structs, enums)
            for p in f.params:
                _ll_type(p.type, structs, enums)
            for _, ty, _ln in _collect_locals(f, enums, funcs, structs):
                _ll_type(ty, structs, enums)
    out = [
        "; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)",
        "; clang -O1 driver.c this.ll -o exe",
        "",
    ]
    globals_: list[str] = []
    body: list[str] = []
    cov_counter = [0] if coverage else None
    meta: list[str] = []
    dbg_types: dict = {}  # M59: 局部变量类型 -> DIBasicType (全模块共享一份)
    if debug:  # M59: DWARF 最小元数据 (编译单元 + 文件 + 签名类型)
        meta += [
            '!0 = distinct !DICompileUnit(language: DW_LANG_C99, file: !1, '
            'producer: "lomentc", isOptimized: false, runtimeVersion: 0, '
            'emissionKind: FullDebug)',
            f'!1 = !DIFile(filename: "{mod.name}.lomt", '
            f'directory: "{lom_root.as_posix()}")',
            "!2 = !DISubroutineType(types: !3)",
            "!3 = !{}",
        ]
    for m in mods:
        for f in m.funcs:
            scope, lines = None, {}
            if debug:
                scope = len(meta)
                meta.append(
                    f'!{scope} = distinct !DISubprogram(name: "{f.name}", scope: !1, '
                    f'file: !1, line: {f.line}, type: !2, unit: !0, '
                    f'spFlags: DISPFlagDefinition, retainedNodes: !3)')
            g, text = _emit_ir_func(f, funcs, consts, structs, enums, coverage,
                                    cov_counter, scope, lines, meta if debug else None,
                                    dbg_types if debug else None)
            globals_ += g
            body.append(text)
    text_all = "\n".join(body)
    if debug:  # M59: 具名元数据 + 编号元数据
        out.append("!llvm.dbg.cu = !{!0}")
        base = len(meta)
        meta.append(f'!{base} = !{{i32 2, !"Dwarf Version", i32 4}}')
        meta.append(f'!{base + 1} = !{{i32 2, !"Debug Info Version", i32 3}}')
        out.append(f"!llvm.module.flags = !{{!{base}, !{base + 1}}}")
        out.append("")
    if coverage:  # M63: 块覆盖计数数组 + 块总数
        out.append("@__loment_cov = global [256 x i64] zeroinitializer")
        out.append(f"@__loment_cov_n = constant i64 {cov_counter[0]}")
        out.append("")
    if "@__loment_" in text_all:  # M31: 自带运行时 (无 libc)
        out.append(_IR_RUNTIME)
    if "@__loment_audit" in text_all:  # P4/M38
        out.append("@__loment_audit = internal global [16 x i64] zeroinitializer")
        out.append("")
    if mod.caps:  # P4/M35: 域描述表 (space 名取 FNV-1a 32 位哈希)
        rows = ", ".join(
            "{ i64, i64, i64, i64 } { i64 %d, i64 %d, i64 %d, i64 %d }"
            % (_fnv1a32(c.space), c.lo, c.hi, 1 if c.revocable else 0)
            for c in mod.caps
        )
        out.append(
            f"@__loment_caps = internal constant [{len(mod.caps)} x {{ i64, i64, i64, i64 }}] [{rows}]"
        )
        out.append("")
    if "@__loment_heap" in text_all:  # M15
        out.append("@__loment_heap = internal global [65536 x i8] zeroinitializer")
        out.append("@__loment_off = internal global i32 0")
        out.append("")
    out += globals_
    if globals_:
        out.append("")
    out.append(text_all)
    if debug:
        out.append("")
        out += meta
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- CLI

_PRELUDE = """
module __prelude
pub enum Option<T> { Some(T), None }
pub enum Result<T, E> { Ok(T), Err(E) }
"""


def load(path: Path) -> Module:
    text = path.read_text(encoding="utf-8")
    mod = Parser(lomc.lex(text), text).parse()
    names = {e.name for e in mod.enums}  # M10: 预置 Option/Result
    if "Option" not in names or "Result" not in names:
        pre = Parser(lomc.lex(_PRELUDE), _PRELUDE).parse()
        for e in pre.enums:
            if e.name not in names:
                e.from_prelude = True
                # line 归零: 注入项的行号来自 **prelude 文件**, 原样留着的话文档生成器会
                # 拿它去查**目标文件**的行 —— 实测 Result 会抄到上文某条 capability 的注释。
                # 注入项在目标文件里没有行, 就该是 0 (Loment 版 lomdoc 也是这么做的)。
                e.line = 0
                mod.enums.append(e)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomentc", description="L1 Loment 编译器 v0")
    ap.add_argument("file", help=".lomt 源文件")
    ap.add_argument("--emit-rust", metavar="PATH")
    ap.add_argument("--emit-potato", metavar="PATH")
    ap.add_argument("--emit-llvm", metavar="PATH", help="LLVM IR (native M0: 标量子集, docs/144)")
    ap.add_argument("--coverage", action="store_true", help="M63: IR 块覆盖计数 (配合 --emit-llvm)")
    ap.add_argument("--debug", action="store_true", help="M59: DWARF 行表元数据 (配合 --emit-llvm)")
    ap.add_argument("--print", dest="print_target", choices=("rust", "potato", "llvm"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lom-root", default=None, help="use 的 .lom 搜索根 (默认仓库根)")
    args = ap.parse_args(argv)

    # M46: 形式对象不可关闭 —— 产出任何后端工件必须同时导出 Potato 形式对象。
    if (args.emit_rust or args.emit_llvm) and not args.emit_potato:
        print("[ERR] M46: 编译器强制导出形式对象 —— 需同时给出 --emit-potato PATH",
              file=sys.stderr)
        return 2

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
        llvm = emit_llvm(mod, root, deps, coverage=args.coverage, debug=args.debug) \
            if (args.emit_llvm or args.print_target == "llvm") else ""
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
