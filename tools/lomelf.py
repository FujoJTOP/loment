#!/usr/bin/env python3
"""lomelf.py — Loment 原生 ELF 后端（参考实现）

**吃掉 clang 的活**：把 `tools/lomentc.py` 发射的 LLVM IR 子集直接编成 x86-64 静态 ELF，
中间不再经过任何 C 编译器。这是 docs/167"彻底脱离 clang"的第一格。

    python tools/lomelf.py in.ll -o out
    python tools/lomelf.py in.ll --check        # 只解析与降级，不落盘

判据（`tools/loment_elf_test.py`）：同一份 `.ll`，本工具产出的 ELF 与
`clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fno-pie -fuse-ld=lld`
产出的 ELF **行为逐值一致**（stdout 与退出码相同）。

v0 的取舍（与 clang 路径不同的地方，逐条记账，别当成"等价"）：

* **调用约定是我们自己的**：实参一律走**栈**（每标量 8 字节，聚合按 8 字节向上取整），
  标量返回值放 `rax`。**不保证 System V / C ABI** —— 原生产物 v0 只保证自身自洽，不给 C 调。
* **聚合返回值 v0 不支持**（明确报错，不静默错编）。
* **栈机**：每个 SSA 值一个栈槽，表达式经 `rax`/`rcx` 求值，不做寄存器分配。正确优先。
* **只吃文本 IR**：不依赖编译器内部结构，任何产出同一子集 `.ll` 的东西都能喂进来。

子集（v0）：标量 i1/i8/i16/i32/i64 与 ptr、`{...}` 结构体、`[N x T]` 数组；
alloca/load/store/getelementptr/extractvalue/insertvalue；算术/位运算/比较/转型；
br/switch/phi；直接 call（<= 6 实参）；内联汇编 syscall；atomicrmw add；ret/unreachable。
**没有**：浮点、bitcast、select、间接调用、闭包、聚合返回值、变参、结构体 GEP。
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ELF_TEXT_VADDR, ELF_DATA_VADDR = 0x400000, 0x600000
TEXT_VADDR, DATA_VADDR = ELF_TEXT_VADDR, ELF_DATA_VADDR   # 当前目标的活动值, PE 目标会改
SCALAR_SIZE = {"i1": 1, "i8": 1, "i16": 2, "i32": 4, "i64": 8, "ptr": 8}

RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = 0, 1, 2, 3, 4, 5, 6, 7
R8, R9, R10, R11, R12, R13, R14, R15 = 8, 9, 10, 11, 12, 13, 14, 15
CC = {"o": 0, "no": 1, "b": 2, "ae": 3, "e": 4, "ne": 5, "be": 6, "a": 7,
      "s": 8, "ns": 9, "p": 10, "np": 11, "l": 12, "ge": 13, "le": 14, "g": 15}
ICMP_CC = {"eq": ("e", 0), "ne": ("ne", 0), "ugt": ("a", 0), "uge": ("ae", 0),
           "ult": ("b", 0), "ule": ("be", 0), "sgt": ("g", 1), "sge": ("ge", 1),
           "slt": ("l", 1), "sle": ("le", 1)}


class ElfError(Exception):
    pass


class Unsupported(ElfError):
    pass


# ------------------------------------------------------------------ 类型


def parse_type(s: str, i: int = 0) -> tuple[str, int]:
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        raise ElfError("类型缺失")
    if s[i] in "{[":
        close = "}" if s[i] == "{" else "]"
        depth, j = 0, i
        while j < len(s):
            if s[j] == s[i]:
                depth += 1
            elif s[j] == close:
                depth -= 1
                if depth == 0:
                    return s[i : j + 1], j + 1
            j += 1
        raise ElfError(f"类型括号不闭合: {s[i:]!r}")
    j = i
    while j < len(s) and (s[j].isalnum() or s[j] == "_"):
        j += 1
    return s[i:j], j


def is_agg(ty: str) -> bool:
    return ty.startswith("{") or ty.startswith("[")


def _fields(ty: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in ty[1:-1]:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def array_parts(ty: str) -> tuple[int, str]:
    m = re.match(r"\[\s*(\d+)\s*x\s*(.+)\]\s*$", ty.strip(), re.S)
    if not m:
        raise ElfError(f"不是数组类型: {ty!r}")
    return int(m.group(1)), m.group(2).strip()


def size_of(ty: str) -> int:
    if ty in SCALAR_SIZE:
        return SCALAR_SIZE[ty]
    if ty.startswith("{"):
        off = 0
        for f in _fields(ty):
            sz = size_of(f)
            off = (off + sz - 1) // sz * sz + sz
        return off
    if ty.startswith("["):
        n, et = array_parts(ty)
        return n * size_of(et)
    raise ElfError(f"v0 不支持的类型: {ty!r}")


def field_offset(ty: str, idx: int) -> tuple[int, str]:
    off = 0
    for k, f in enumerate(_fields(ty)):
        sz = size_of(f)
        off = (off + sz - 1) // sz * sz
        if k == idx:
            return off, f
        off += sz
    raise ElfError(f"字段下标越界: {ty} #{idx}")


def elem_of(ty: str) -> str:
    return array_parts(ty)[1]


# ------------------------------------------------------------------ 词法小工具


_VALUE_RE = re.compile(r"^(%[-A-Za-z0-9_.]+|@[-A-Za-z0-9_.]+|-?[0-9]+|true|false|null|undef)$")


def split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_operand(s: str) -> tuple[str, str]:
    ty, i = parse_type(s)
    rest = s[i:].strip()
    if "," in rest:
        rest = rest.split(",", 1)[0].strip()
    m = _VALUE_RE.match(rest)
    if not m:
        raise ElfError(f"解析不了的值: {s!r}")
    return ty, m.group(1)


def unbalanced(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
    return depth > 0


# ------------------------------------------------------------------ IR 解析


class Instr:
    __slots__ = ("dest", "text")

    def __init__(self, dest, text):
        self.dest, self.text = dest, text


class Block:
    def __init__(self, label):
        self.label = label
        self.instrs: list[Instr] = []


class Func:
    def __init__(self, name, ret, params, internal):
        self.name, self.ret, self.params, self.internal = name, ret, params, internal
        self.blocks: list[Block] = []


class Global:
    def __init__(self, name, ty, data):
        self.name, self.ty, self.data = name, ty, data
        self.addr = 0


def parse_ll(text: str) -> tuple[list[Global], list[Func], list[str]]:
    """文本 IR -> (全局, 函数, **外部函数名**)。

    第三个返回值是 `declare` 出来的外部符号 (docs/173 的 `extern fn`)。v0 原先**整行跳过**
    `declare` —— 外部符号连表示都没有, 于是 `call @c_add` 会在 `finalize()` 里报"未定义的
    标签"。现在记下来: 它们由 `--link` 进来的外部目标文件提供, 调用点还要按 **C ABI**
    传参 (见 `Emitter._call`)。
    """
    globals_, funcs, externs, lines, i = [], [], [], text.split("\n"), 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line[0] in ";!":
            continue
        if line.startswith("declare "):
            m = re.match(r"declare\s+(?:[\w.]+\s+)*?(@[-A-Za-z0-9_.]+)\s*\(", line)
            if m:
                externs.append(m.group(1)[1:])
            continue
        if line.startswith(("target ", "attributes ", "source_filename", "module ")):
            continue
        if line.startswith("@"):
            globals_.append(_parse_global(line))
            continue
        if line.startswith("define "):
            fn, i = _parse_func(lines, i - 1)
            funcs.append(fn)
    return globals_, funcs, externs


def _parse_global(line: str) -> Global:
    m = re.match(r"(@[-A-Za-z0-9_.]+)\s*=\s*(.*)$", line)
    if not m:
        raise ElfError(f"全局声明解析失败: {line!r}")
    name, rest = m.group(1), m.group(2)
    while True:
        m2 = re.match(
            r"^(private|internal|external|unnamed_addr|dso_local|constant|global|common)\s+(.*)$", rest)
        if not m2:
            break
        rest = m2.group(2)
    ty, j = parse_type(rest)
    return Global(name, ty, _parse_init(ty, rest[j:].strip()))


def _parse_init(ty: str, init: str) -> bytes:
    init = init.strip()
    if init.startswith('c"'):
        m = re.match(r'c"(.*)"$', init, re.S)
        if not m:
            raise ElfError(f"字节串解析失败: {init!r}")
        body, out, k = m.group(1), bytearray(), 0
        while k < len(body):
            if body[k] == "\\":
                out.append(int(body[k + 1 : k + 3], 16))
                k += 3
            else:
                out.append(ord(body[k]))
                k += 1
        return bytes(out)
    if init.startswith("zeroinitializer"):
        return b"\0" * size_of(ty)
    if ty.startswith("["):
        inner = init[len(ty):].strip() if init.startswith(ty) else init
        if not inner.startswith("["):
            raise ElfError(f"数组初值解析失败: {init!r}")
        out = bytearray()
        for it in split_top(inner[1:-1]):
            t2, j = parse_type(it)
            out += _parse_init(t2, it[j:].strip())
        if len(out) != size_of(ty):
            raise ElfError(f"数组初值长度不符: {init!r}")
        return bytes(out)
    if ty in SCALAR_SIZE:
        if not re.match(r"^-?[0-9]+$", init):
            raise ElfError(f"标量初值解析失败: {init!r}")
        n = int(init)
        return (n & ((1 << (8 * size_of(ty))) - 1)).to_bytes(size_of(ty), "little")
    if ty.startswith("{"):
        # 结构体初值。LLVM 两种写法都要吃:
        #   `{ i64, i64 } { i64 1, i64 2 }`  (带类型前缀, 数组元素里常见)
        #   `{ i64 1, i64 2 }`               (无前缀 —— 能力域表 `@__loment_caps` 就是这种)
        # 字段类型**以 ty 为准**, 不从初值文本里猜: 嵌套聚合的值是 `{ i64 1, i64 2 }`,
        # 拆出来的"类型"其实是值本身。
        inner = init[len(ty):].strip() if init.startswith(ty) else init
        if not inner.startswith("{"):
            raise ElfError(f"结构体初值解析失败: {init!r}")
        flds = _fields(ty)
        vals = split_top(inner[1:-1])
        if len(vals) != len(flds):
            raise ElfError(f"结构体初值字段数不符: {init!r}")
        out = bytearray()
        for k, v in enumerate(vals):
            off, fty = field_offset(ty, k)
            if off > len(out):                    # 字段间的对齐空洞 LLVM 不写, 自己补
                out += b"\0" * (off - len(out))
            t2, j = parse_type(v)
            rest = v[j:].strip()
            out += _parse_init(t2, rest) if rest else _parse_init(fty, v)
        out += b"\0" * (size_of(ty) - len(out))    # 尾随填充
        return bytes(out)
    raise ElfError(f"v0 不支持的全局初值: {ty} {init!r}")


def _parse_func(lines: list[str], start: int) -> tuple[Func, int]:
    head = lines[start].strip()
    m = re.match(
        r"define\s+(?:(?:internal|private)\s+)?(.+?)\s+(@[-A-Za-z0-9_.]+)\s*\((.*?)\)"
        r"(?:\s+\S+)*\s*\{?\s*$", head)
    if not m:
        raise ElfError(f"函数头解析失败: {head!r}")
    internal = "internal" in head or "private" in head
    ret, _ = parse_type(m.group(1))
    params = []
    for p in split_top(m.group(3)):
        if p:
            ty, k = parse_type(p)
            params.append((ty, p[k:].strip()))
    fn = Func(m.group(2)[1:], ret, params, internal)
    i, cur = start + 1, None
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line == "}":
            break
        if not line or line[0] in ";!":
            continue
        if line.endswith(":") and "=" not in line and not line.startswith("%"):
            cur = Block(line[:-1])
            fn.blocks.append(cur)
            continue
        while unbalanced(line) and i < len(lines):
            line += " " + lines[i].strip()
            i += 1
        if cur is None:  # LLVM 允许省略入口块标签
            cur = Block("entry")
            fn.blocks.append(cur)
        m2 = re.match(r"^(%[-A-Za-z0-9_.]+)\s*=\s*(.*)$", line)
        cur.instrs.append(Instr(m2.group(1), m2.group(2).strip()) if m2 else Instr(None, line))
    return fn, i


# ------------------------------------------------------------------ x86-64 编码


def rex(w=0, r=0, x=0, b=0) -> bytes:
    v = 0x40 | (w << 3) | ((r >> 3) << 2) | ((x >> 3) << 1) | (b >> 3)
    return b"" if v == 0x40 else bytes([v])


def modrm(mod, reg, rm) -> bytes:
    return bytes([(mod << 6) | ((reg & 7) << 3) | (rm & 7)])


def _mem(reg_field, base, disp) -> bytes:
    out = bytes([(0b10 << 6) | ((reg_field & 7) << 3) | (base & 7)])
    if (base & 7) == 4:
        out += bytes([0x24])
    return out + struct.pack("<i", disp)


def mov_rr(dst, src) -> bytes:
    return rex(1, src, 0, dst) + b"\x89" + modrm(3, src, dst)


def mov_rr32(dst, src) -> bytes:
    return rex(0, src, 0, dst) + b"\x89" + modrm(3, src, dst)


def mov_ri(dst, imm) -> bytes:
    return rex(1, 0, 0, dst) + bytes([0xB8 + (dst & 7)]) + struct.pack("<Q", imm & 0xFFFFFFFFFFFFFFFF)


def mov_ri32(dst, imm) -> bytes:
    return rex(0, 0, 0, dst) + bytes([0xB8 + (dst & 7)]) + struct.pack("<I", imm & 0xFFFFFFFF)


def mov_rm(dst, base, disp) -> bytes:
    return rex(1, dst, 0, base) + b"\x8B" + _mem(dst, base, disp)


def mov_mr(base, disp, src) -> bytes:
    return rex(1, src, 0, base) + b"\x89" + _mem(src, base, disp)


def load_sized(reg, base, disp, size) -> bytes:
    if size == 8:
        return mov_rm(reg, base, disp)
    if size == 4:
        return rex(0, reg, 0, base) + b"\x8B" + _mem(reg, base, disp)
    op = 0xB6 if size == 1 else 0xB7
    return rex(0, reg, 0, base) + b"\x0F" + bytes([op]) + _mem(reg, base, disp)


def store_sized(base, disp, src, size) -> bytes:
    if size == 8:
        return mov_mr(base, disp, src)
    if size == 4:
        return rex(0, src, 0, base) + b"\x89" + _mem(src, base, disp)
    pre = b"\x66" if size == 2 else b""
    op = 0x88 if size == 1 else 0x89
    return pre + rex(0, src, 0, base) + bytes([op]) + _mem(src, base, disp)


def lea(dst, base, disp) -> bytes:
    return rex(1, dst, 0, base) + b"\x8D" + _mem(dst, base, disp)


def alu_rr(op, dst, src) -> bytes:
    return rex(1, src, 0, dst) + bytes([op]) + modrm(3, src, dst)


def alu_ri(digit, dst, imm) -> bytes:
    """0x81 /digit dst, imm32；digit 是 Group1 的 /digit（0=add 5=sub 7=cmp）。"""
    return rex(1, 0, 0, dst) + b"\x81" + modrm(3, digit, dst) + struct.pack("<i", imm)


ADD, OR_, AND_, SUB, XOR_, CMP = 0, 1, 4, 5, 6, 7


def imul_rr(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x0F\xAF" + modrm(3, dst, src)


def shift_cl(op, reg) -> bytes:
    return rex(1, 0, 0, reg) + b"\xD3" + modrm(3, op, reg)


def group3(op, reg) -> bytes:
    return rex(1, 0, 0, reg) + b"\xF7" + modrm(3, op, reg)


def setcc(cc, reg) -> bytes:
    return rex(0, 0, 0, reg) + b"\x0F" + bytes([0x90 + CC[cc]]) + modrm(3, 0, reg)


def movzx8(dst, src) -> bytes:
    return rex(0, dst, 0, src) + b"\x0F\xB6" + modrm(3, dst, src)


def movsx8(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x0F\xBE" + modrm(3, dst, src)


def movsx16(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x0F\xBF" + modrm(3, dst, src)


def movsxd(dst, src) -> bytes:
    return rex(1, dst, 0, src) + b"\x63" + modrm(3, dst, src)


def zext_to64(reg, size) -> bytes:
    """reg 低 size 字节零扩展成 64 位（reg 已是原生宽度零扩展的形态时只需清高位）。"""
    if size >= 8:
        return b""
    if size == 4:
        return mov_rr32(reg, reg)
    if size == 2:
        return rex(1, reg, 0, reg) + b"\x0F\xB7" + modrm(3, reg, reg)
    return movzx8(reg, reg)


def sext_to64(reg, size) -> bytes:
    if size >= 8:
        return b""
    if size == 4:
        return movsxd(reg, reg)
    if size == 2:
        return movsx16(reg, reg)
    return movsx8(reg, reg)


def push_r(reg) -> bytes:
    return rex(0, 0, 0, reg) + bytes([0x50 + (reg & 7)])


def pop_r(reg) -> bytes:
    return rex(0, 0, 0, reg) + bytes([0x58 + (reg & 7)])


# ------------------------------------------------------------------ 汇编缓冲


class Asm:
    def __init__(self, base):
        self.base, self.buf = base, bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int, str]] = []

    def here(self) -> int:
        return self.base + len(self.buf)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ElfError(f"标签重复: {name}")
        self.labels[name] = self.here()

    def emit(self, b: bytes) -> None:
        self.buf += b

    def jmp(self, lbl: str) -> None:
        at = self.here()
        self.emit(b"\xE9\x00\x00\x00\x00")
        self.fixups.append((at + 1, lbl, 4, "rel"))

    def jcc(self, cc: str, lbl: str) -> None:
        at = self.here()
        self.emit(b"\x0F" + bytes([0x80 + CC[cc]]) + b"\x00\x00\x00\x00")
        self.fixups.append((at + 2, lbl, 4, "rel"))

    def call(self, lbl: str) -> None:
        at = self.here()
        self.emit(b"\xE8\x00\x00\x00\x00")
        self.fixups.append((at + 1, lbl, 4, "rel"))

    def mov_abs(self, reg: int, lbl: str) -> None:
        self.emit(mov_ri(reg, 0))
        self.fixups.append((self.here() - 8, lbl, 8, "abs"))

    def finalize(self) -> bytes:
        for site, lbl, width, kind in self.fixups:
            if lbl not in self.labels:
                raise ElfError(f"未定义的标签: {lbl}")
            if kind == "abs":
                struct.pack_into("<Q", self.buf, site - self.base, self.labels[lbl])
            else:
                struct.pack_into("<i", self.buf, site - self.base,
                                 self.labels[lbl] - (site + width))
        return bytes(self.buf)


# ------------------------------------------------------------------ 结果类型


def result_type(text: str) -> str | None:
    t = text
    if t.startswith("alloca"):
        return "ptr"
    if t.startswith("load "):
        return parse_type(t[5:])[0]
    if t.startswith("getelementptr"):
        return "ptr"
    if t.startswith("extractvalue"):
        ty, i = parse_type(t[len("extractvalue "):])
        return field_offset(ty, int(t[len("extractvalue ") + i:].rsplit(",", 1)[-1]))[1]
    if t.startswith("insertvalue"):
        return parse_type(t[len("insertvalue "):])[0]
    if t.startswith("icmp"):
        return "i1"
    if t.startswith("atomicrmw"):
        return re.search(r",\s*(\w+)\s+\S+", t).group(1)
    for op in ("add", "sub", "mul", "and", "or", "xor",
               "sdiv", "udiv", "srem", "urem", "shl", "lshr", "ashr"):
        if t.startswith(op + " "):
            return parse_type(t[len(op) + 1:])[0]
    if t.startswith(("trunc ", "zext ", "sext ", "ptrtoint ", "inttoptr ")):
        return parse_type(re.search(r"\bto\s+(.+)$", t).group(1))[0]
    if t.startswith("phi "):
        return parse_type(t[4:])[0]
    if t.startswith("call "):
        if t[5:].lstrip().startswith("void"):
            return None
        return parse_type(t[5:])[0]
    return None


CALL_ASM_RE = re.compile(
    r'call\s+(.+?)\s+asm\s+sideeffect\s+"syscall"\s*,\s*"([^"]*)"\s*\((.*)\)\s*$', re.S)
BR_COND_RE = re.compile(r"i1\s+(\S+?),\s*label\s+%([\w.]+?),\s*label\s+%([\w.]+)\s*$")
SWITCH_RE = re.compile(r"switch\s+(\w+)\s+(\S+?),\s*label\s+%([\w.]+)\s*\[(.*)\]\s*$", re.S)
CASE_RE = re.compile(r"(\w+)\s+(-?[0-9]+),\s*label\s+%([\w.]+)")
PHI_RE = re.compile(r"phi\s+(.+?)\s*\[", re.S)
PHI_BODY_RE = re.compile(r"\[([^\[\]]*)\]")
PHI_ENTRY_RE = re.compile(r"(.*?),\s*%([\w.]+)\s*$")


def parse_phi(text: str) -> tuple[str, list[tuple[str, str]]]:
    """`phi i1 [ v, %a ], [ w, %b ]` -> (类型, [(值, 前驱块标签), ...])。"""
    m = PHI_RE.match(text)
    if not m:
        raise ElfError(f"phi 解析失败: {text!r}")
    ty, _ = parse_type(m.group(1))
    entries = []
    for body in PHI_BODY_RE.findall(text):
        em = PHI_ENTRY_RE.match(body.strip())
        if not em:
            raise ElfError(f"phi 入边解析失败: {body!r}")
        entries.append((em.group(1).strip(), em.group(2)))
    return ty, entries


class Emitter:
    def __init__(self, globals_: list[Global], externs: set[str] | None = None):
        self.asm = Asm(TEXT_VADDR)
        self.externs = externs or set()   # docs/173: 这些名字走 C ABI 传参
        self.globals = {g.name: g for g in globals_}
        self.cur: Func | None = None
        self.cur_block = ""
        self.slots: dict[str, int] = {}
        self.allocas: dict[str, tuple[int, int]] = {}
        self.frame = 0
        self.phis: dict[str, list[Instr]] = {}

    # ---- 帧

    def plan_frame(self, f: Func) -> None:
        self.slots, self.allocas, self.frame, self.phis = {}, {}, 0, {}
        self.slots.update({n: -0 for n in ()})
        for blk in f.blocks:
            for ins in blk.instrs:
                if ins.text.startswith("phi ") and ins.dest:
                    self.phis.setdefault(blk.label, []).append(ins)
        arg = 16
        if is_agg(f.ret):
            arg = 24                      # 隐藏结果指针占 [rbp+16]
        for ty, name in f.params:
            self.slots[name] = arg
            arg += (max(8, size_of(ty)) + 7) // 8 * 8
        for blk in f.blocks:
            for ins in blk.instrs:
                if ins.text.startswith("alloca"):
                    ty, _ = parse_type(ins.text[len("alloca "):])
                    sz = size_of(ty)
                    self.frame += (sz + 7) // 8 * 8
                    self.allocas[ins.dest] = (-self.frame, sz)
                elif ins.dest and ins.dest not in self.slots:
                    rt = result_type(ins.text)
                    if rt:
                        sz = max(8, size_of(rt))
                        self.frame += (sz + 7) // 8 * 8
                        self.slots[ins.dest] = -self.frame

    # ---- 取值 / 存值

    def put(self, dest: str, reg: int) -> None:
        self.asm.emit(mov_mr(RBP, self.slots[dest], reg))

    def get(self, ty: str, val: str, reg: int) -> None:
        """标量 -> reg，**零扩展**到 64 位（符号语义在用到的地方显式 sext）。"""
        sz = size_of(ty)
        if val.startswith("%"):
            off = self.slots.get(val)
            if off is None:
                raise ElfError(f"未定义的 SSA 值: {val}")
            self.asm.emit(load_sized(reg, RBP, off, sz))
        elif val.startswith("@"):
            self.asm.mov_abs(reg, val)
            if sz != 8:
                self.asm.emit(load_sized(reg, reg, 0, sz))
        elif val == "true":
            self.asm.emit(mov_ri32(reg, 1) if sz <= 4 else mov_ri(reg, 1))
        elif val in ("false", "null", "undef"):
            self.asm.emit(mov_ri32(reg, 0))
        else:
            self.asm.emit(mov_ri(reg, int(val)))
        if sz < 8:
            self.asm.emit(zext_to64(reg, sz))

    def get_signed(self, ty: str, val: str, reg: int) -> None:
        self.get(ty, val, reg)
        self.asm.emit(sext_to64(reg, size_of(ty)))

    def get_ptr(self, val: str, reg: int) -> None:
        if val.startswith("%"):
            if val in self.allocas:
                self.asm.emit(lea(reg, RBP, self.allocas[val][0]))
                return
            off = self.slots.get(val)
            if off is None:
                raise ElfError(f"未定义的指针: {val}")
            self.asm.emit(mov_rm(reg, RBP, off))
        elif val.startswith("@"):
            self.asm.mov_abs(reg, val)
        else:
            raise ElfError(f"v0 不支持指针常量: {val!r}")

    def agg_addr(self, val: str, reg: int) -> None:
        if val.startswith("%"):
            if val in self.allocas:
                self.asm.emit(lea(reg, RBP, self.allocas[val][0]))
                return
            off = self.slots.get(val)
            if off is None:
                raise ElfError(f"未定义的聚合值: {val}")
            self.asm.emit(lea(reg, RBP, off))
        elif val.startswith("@"):
            self.asm.mov_abs(reg, val)
        else:
            raise ElfError(f"v0 不支持聚合常量: {val!r}")

    def copy(self, dst: int, src: int, n: int) -> None:
        self.asm.emit(mov_rr(RDI, dst))
        self.asm.emit(mov_rr(RSI, src))
        self.asm.emit(mov_ri(RCX, n))
        self.asm.emit(b"\xF3\xA4")

    # ---- 指令

    def lower(self, ins: Instr) -> None:
        t, d = ins.text, ins.dest
        if t.startswith("alloca"):
            return
        if t.startswith("load "):
            return self._load(t, d)
        if t.startswith("store "):
            return self._store(t)
        if t.startswith("getelementptr"):
            return self._gep(t, d)
        if t.startswith("extractvalue"):
            return self._extract(t, d)
        if t.startswith("insertvalue"):
            return self._insert(t, d)
        if t.startswith("icmp "):
            return self._icmp(t, d)
        for op in ("add", "sub", "mul", "and", "or", "xor"):
            if t.startswith(op + " "):
                return self._alu(op, t, d)
        for op in ("sdiv", "udiv", "srem", "urem"):
            if t.startswith(op + " "):
                return self._div(op, t, d)
        for op in ("shl", "lshr", "ashr"):
            if t.startswith(op + " "):
                return self._shift(op, t, d)
        for op in ("trunc", "zext", "sext"):
            if t.startswith(op + " "):
                return self._cast(op, t, d)
        if t.startswith(("ptrtoint ", "inttoptr ")):
            return self._ptrcast(t, d)
        if t.startswith("phi "):
            return
        if t.startswith("br"):
            return self._br(t)
        if t.startswith("switch "):
            return self._switch(t)
        if t.startswith("call "):
            return self._call(t, d)
        if t.startswith("ret"):
            return self._ret(t)
        if t.startswith("unreachable"):
            self.asm.emit(b"\x0F\x0B")
            return
        if t.startswith("atomicrmw"):
            return self._atomic(t, d)
        raise Unsupported(f"v0 不支持的指令: {t!r}")

    def _binoperands(self, op, t):
        ty, i = parse_type(t[len(op) + 1:])
        a, b = split_top(t[len(op) + 1 + i:])
        return ty, a.strip(), b.strip()

    def _load(self, t, d):
        ty, i = parse_type(t[5:])
        _pty, pval = parse_operand(split_top(t[5 + i:].lstrip(" ,"))[0])
        if is_agg(ty):
            self.agg_addr(d, RDI)
            self.get_ptr(pval, RSI)
            self.copy(RDI, RSI, size_of(ty))
        else:
            self.get_ptr(pval, RAX)
            self.asm.emit(load_sized(RAX, RAX, 0, size_of(ty)))
            if size_of(ty) < 8:
                self.asm.emit(zext_to64(RAX, size_of(ty)))
            self.put(d, RAX)

    def _store(self, t):
        ty, i = parse_type(t[6:])
        parts = split_top(t[6 + i:].lstrip(" ,"))
        val = parts[0]
        _pty, pval = parse_operand(parts[1])
        if is_agg(ty):
            self.get_ptr(pval, RDI)
            self.agg_addr(val, RSI)
            self.copy(RDI, RSI, size_of(ty))
        else:
            self.get(ty, val, RAX)
            self.get_ptr(pval, RCX)
            self.asm.emit(store_sized(RCX, 0, RAX, size_of(ty)))

    def _gep(self, t, d):
        rest = re.sub(r"^getelementptr\s+(inbounds\s+)?", "", t)
        ty, i = parse_type(rest)
        parts = split_top(rest[i:].lstrip(" ,"))
        _pty, pval = parse_operand(parts[0])
        self.get_ptr(pval, RAX)
        cur = ty
        for p in parts[1:]:
            ity, ival = parse_operand(p)
            self.get(ity, ival, RCX)
            if cur.startswith("["):
                et = elem_of(cur)
                step = size_of(et)
                cur = et
            elif cur.startswith("{"):
                if not re.match(r"^-?[0-9]+$", ival):
                    raise Unsupported("v0 只支持常量下标的结构体 GEP（动态下标请用 extractvalue）")
                off, cur = field_offset(cur, int(ival))
                if off:
                    self.asm.emit(alu_ri(ADD, RAX, off))
                continue
            else:
                step = size_of(cur)
            if step != 1:
                self.asm.emit(mov_ri(RDX, step))
                self.asm.emit(imul_rr(RCX, RDX))
            self.asm.emit(alu_rr(0x01, RAX, RCX))
        self.put(d, RAX)

    def _extract(self, t, d):
        ty, i = parse_type(t[len("extractvalue "):])
        val, idx_s = t[len("extractvalue ") + i:].lstrip(" ,").rsplit(",", 1)
        off, fty = field_offset(ty, int(idx_s.strip()))
        if is_agg(fty):
            self.agg_addr(d, RDI)
            self.agg_addr(val.strip(), RAX)
            self.asm.emit(lea(RSI, RAX, off))
            self.copy(RDI, RSI, size_of(fty))
        else:
            self.agg_addr(val.strip(), RAX)
            self.asm.emit(load_sized(RAX, RAX, off, size_of(fty)))
            if size_of(fty) < 8:
                self.asm.emit(zext_to64(RAX, size_of(fty)))
            self.put(d, RAX)

    def _insert(self, t, d):
        ty, i = parse_type(t[len("insertvalue "):])
        parts = split_top(t[len("insertvalue ") + i:].lstrip(" ,"))
        agg_val = parts[0]
        ety, eval_ = parse_operand(parts[1])
        idx = int(parts[2])
        off, fty = field_offset(ty, idx)
        dst = self.slots[d]
        if agg_val == "undef":
            self.asm.emit(lea(RDI, RBP, dst))
            self.asm.emit(mov_ri(RCX, size_of(ty)))
            self.asm.emit(mov_ri32(RAX, 0))
            self.asm.emit(b"\xF3\xAA")  # rep stosb
        else:
            self.asm.emit(lea(RDI, RBP, dst))
            self.agg_addr(agg_val, RSI)
            self.copy(RDI, RSI, size_of(ty))
        if is_agg(ety):
            self.asm.emit(lea(RDI, RBP, dst + off) if False else lea(RDI, RBP, dst))
            self.asm.emit(alu_ri(ADD, RDI, off) if off else b"")
            self.agg_addr(eval_, RSI)
            self.copy(RDI, RSI, size_of(ety))
        else:
            self.get(ety, eval_, RAX)
            self.asm.emit(store_sized(RBP, dst + off, RAX, size_of(ety)))

    def _icmp(self, t, d):
        m = re.match(r"icmp\s+(\w+)\s+(.+)$", t, re.S)
        cc, signed = ICMP_CC[m.group(1)]
        ty, i = parse_type(m.group(2))
        a, b = split_top(m.group(2)[i:])
        if signed:
            self.get_signed(ty, a.strip(), RAX)
            self.get_signed(ty, b.strip(), RCX)
        else:
            self.get(ty, a.strip(), RAX)
            self.get(ty, b.strip(), RCX)
        self.asm.emit(alu_rr(0x39, RAX, RCX))
        self.asm.emit(setcc(cc, RAX))
        self.asm.emit(movzx8(RAX, RAX))
        self.put(d, RAX)

    def _alu(self, op, t, d):
        ty, a, b = self._binoperands(op, t)
        self.get(ty, a, RAX)
        self.get(ty, b, RCX)
        if op == "mul":
            self.asm.emit(imul_rr(RAX, RCX))
        else:
            self.asm.emit(alu_rr({"add": 0x01, "sub": 0x29, "and": 0x21,
                                  "or": 0x09, "xor": 0x31}[op], RAX, RCX))
        if size_of(ty) < 8:
            self.asm.emit(zext_to64(RAX, size_of(ty)))
        self.put(d, RAX)

    def _div(self, op, t, d):
        ty, a, b = self._binoperands(op, t)
        signed = op[0] == "s"
        if signed:
            self.get_signed(ty, a, RAX)
            self.get_signed(ty, b, RCX)
            self.asm.emit(b"\x48\x99")  # cqo
        else:
            self.get(ty, a, RAX)
            self.get(ty, b, RCX)
            self.asm.emit(mov_ri32(RDX, 0))
        self.asm.emit(group3(7 if signed else 6, RCX))
        reg = RDX if "rem" in op else RAX
        if size_of(ty) < 8:
            self.asm.emit(zext_to64(reg, size_of(ty)))
        self.put(d, reg)

    def _shift(self, op, t, d):
        ty, a, b = self._binoperands(op, t)
        if op == "ashr":
            self.get_signed(ty, a, RAX)
        else:
            self.get(ty, a, RAX)
        self.get(ty, b, RCX)
        self.asm.emit(shift_cl({"shl": 4, "lshr": 5, "ashr": 7}[op], RAX))
        if size_of(ty) < 8:
            self.asm.emit(zext_to64(RAX, size_of(ty)))
        self.put(d, RAX)

    def _cast(self, op, t, d):
        m = re.match(r"(.+?)\s+(\S+)\s+to\s+(.+)$", t[len(op) + 1:], re.S)
        fty, fval, tty = parse_type(m.group(1))[0], m.group(2), parse_type(m.group(3))[0]
        self.get(fty, fval, RAX)
        fsz, tsz = size_of(fty), size_of(tty)
        if op == "zext":
            self.asm.emit(zext_to64(RAX, fsz))
        elif op == "sext":
            self.asm.emit(sext_to64(RAX, fsz))
        if tsz < 8:
            self.asm.emit(zext_to64(RAX, tsz))
        self.put(d, RAX)

    def _ptrcast(self, t, d):
        op = t.split(" ", 1)[0]
        m = re.match(r"(.+?)\s+(\S+)\s+to\s+(.+)$", t[len(op) + 1:], re.S)
        fty, fval, tty = parse_type(m.group(1))[0], m.group(2), parse_type(m.group(3))[0]
        if op == "ptrtoint":
            self.get_ptr(fval, RAX)
        else:
            self.get(fty, fval, RAX)
        if size_of(tty) < 8:
            self.asm.emit(zext_to64(RAX, size_of(tty)))
        self.put(d, RAX)

    def _br(self, t):
        body = t[2:].strip()
        if body.startswith("label"):
            return self._jump_to(body[len("label "):].strip().lstrip("%"))
        m = BR_COND_RE.search(body)
        self.get("i1", m.group(1), RAX)
        self.asm.emit(b"\x85\xC0")  # test eax, eax
        a, b = m.group(2), m.group(3)
        a, nxt = self._emit_phis_then(a)
        self.asm.jcc("ne", self.blk(a))
        self.asm.label(nxt)
        self._jump_to(b)

    def _jump_to(self, tgt: str) -> None:
        tgt, nxt = self._emit_phis_then(tgt)
        self.asm.jmp(self.blk(tgt))
        self.asm.label(nxt)

    def _emit_phis_then(self, tgt: str) -> tuple[str, str]:
        """给 target 的每个 phi 写入来自**当前块**的入边值，返回 (真实标签, 跳过标签)。

        **两趟，不是一趟。** LLVM 的 phi 是**同时**赋值：同一前驱上的若干 phi
        按"读旧值、写新值"一次完成。一趟顺序写会当场自我覆盖 —— 最典型的是
        `%cur = phi [.., %nxt]` 与 `%prev = phi [.., %cur]` 这一对（链表遍历里
        的标准写法）：先写 `%cur` 再读 `%cur` 给 `%prev`，`%prev` 拿到的是 `%nxt`。

        实测（2026-09-25）：这一条让 `__loment_free` 的插入**每步都以为走到了链尾**，
        于是每次都插在头部 —— 空闲链表恒为 1 个节点，`gc_auto` 因此回收不掉、
        arena 一路涨到 OOM。最小复现是一个三行的循环：`%j = phi [.., %i]` 该得 2，
        一趟写会得 3。所以这一趟暂存不是优化，是语义。

        暂存用 `push`/`pop`（各自 1 字节），不占帧 —— 栈本来就是现成的暂存区，
        而 pop 反序正好把顺序还原。自举侧 `loment/tools/lomelf.lomt` 同一形态，
        两边的产物才逐字节相同。
        """
        nxt = f"__ph{self.asm.here():x}"
        phis = self.phis.get(tgt)
        if not phis:
            return tgt, nxt
        pending: list[tuple[Instr, str, bool]] = []
        for phi in phis:
            pty, entries = parse_phi(phi.text)
            for ival, pred in entries:
                if pred != self.cur_block:
                    continue
                agg = is_agg(pty)
                if agg:
                    ity, i2 = parse_type(ival)
                    self.agg_addr(ival[i2:].strip(), RSI)
                    self.asm.emit(push_r(RSI))
                else:
                    self.get(pty, ival, RAX)
                    self.asm.emit(push_r(RAX))
                pending.append((phi, pty, agg))
        for phi, pty, agg in reversed(pending):
            if agg:
                self.asm.emit(pop_r(RSI))
                self.asm.emit(lea(RDI, RBP, self.slots[phi.dest]))
                self.copy(RDI, RSI, size_of(pty))
            else:
                self.asm.emit(pop_r(RAX))
                self.put(phi.dest, RAX)
        return tgt, nxt

    def _switch(self, t):
        m = SWITCH_RE.match(t)
        ty, val, default, body = m.group(1), m.group(2), m.group(3), m.group(4)
        self.get(ty, val, RAX)
        for _ity, num, lbl in CASE_RE.findall(body):
            self.asm.emit(alu_ri(CMP, RAX, int(num)))
            nxt = f"__sw{self.asm.here():x}"
            self.asm.jcc("ne", nxt)
            self._jump_to(lbl)
            self.asm.label(nxt)
        self._jump_to(default)

    def _call(self, t, d):
        m = CALL_ASM_RE.match(t)
        if m:
            return self._call_asm(m, d)
        rest = t[5:].lstrip()
        if rest.startswith("void"):
            ret_ty, rest = None, rest[4:].lstrip()
        else:
            ret_ty, i = parse_type(rest)
            rest = rest[i:].lstrip()
        cm = re.match(r"(@[-A-Za-z0-9_.]+|%[-A-Za-z0-9_.]+)\s*\((.*)\)\s*$", rest, re.S)
        callee, argstr = cm.group(1), cm.group(2)
        if callee.startswith("%"):
            raise Unsupported("v0 不支持间接调用")
        args = [parse_operand(x) for x in split_top(argstr)] if argstr.strip() else []
        # ---- 外部函数 (docs/173): 实参走**寄存器**, 不走我们自己的栈约定。
        # 求值顺序: **从右往左**逐个算进 RAX 再 push, 然后从左往右 pop 进目标寄存器 ——
        # 这样 push 完栈顶正好是第 0 个实参, pop 的先后与寄存器顺序自然对齐。不能直接
        # `self.get(ty, val, RDI)` 一个个算: `get` 内部要用 RAX/RCX 求值, 会把前面已经放好的
        # 寄存器踩掉。
        if callee[1:] in self.externs:
            if len(args) > len(C_ARG_REGS):
                raise Unsupported(f"extern 调用 {callee[1:]}: 第 1 阶段最多 6 个实参 (docs/173 §3)")
            if ret_ty is not None and is_agg(ret_ty):
                raise Unsupported(f"extern 调用 {callee[1:]}: 第 1 阶段不接受聚合返回值")
            for ty, val in reversed(args):
                if is_agg(ty):
                    raise Unsupported(f"extern 调用 {callee[1:]}: 第 1 阶段不接受聚合实参")
                self.get(ty, val, RAX)
                self.asm.emit(push_r(RAX))
            for reg, _a in zip(C_ARG_REGS, args):
                self.asm.emit(pop_r(reg))
            self.asm.call(callee[1:])
            # **返回值照旧落回目标槽** —— 与内部调用同一步。少了这一句, 调用结果**丢掉**,
            # 后面读到的是那个槽的旧值: 症状是"程序能跑、结果是假 0" (实测第一版就是这样:
            # `c_add(3,4) + c_mul(5,6)` 退出码 0 而不是 37)。
            if d is not None:
                self.put(d, RAX)
            return
        ret_agg = ret_ty is not None and is_agg(ret_ty)
        if len(args) > 16:
            raise Unsupported("v0 最多 16 个实参")
        total = 0
        for ty, _ in reversed(args):
            total += (max(8, size_of(ty)) + 7) // 8 * 8
        if ret_agg:
            total += 8
        for ty, val in reversed(args):
            if is_agg(ty):
                sz = (size_of(ty) + 7) // 8 * 8
                self.asm.emit(alu_ri(SUB, RSP, sz))  # sub rsp, sz
                self.asm.emit(mov_rr(RDI, RSP))
                self.agg_addr(val, RSI)
                self.copy(RDI, RSI, size_of(ty))
            else:
                self.get(ty, val, RAX)
                self.asm.emit(push_r(RAX))
        if ret_agg:
            self.asm.emit(lea(RAX, RBP, self.slots[d]))
            self.asm.emit(push_r(RAX))
        if callee == "@llvm.trap":
            self.asm.emit(b"\x0F\x0B")
        else:
            self.asm.call(callee[1:])
        if total:
            self.asm.emit(alu_ri(ADD, RSP, total))
        if d is not None and not ret_agg:
            self.put(d, RAX)

    def _call_asm(self, m, d):
        cons, argstr = m.group(2), m.group(3)
        regs = [x.strip().strip("{}") for x in cons.split(",") if x.strip().startswith("{")]
        regs = [r for r in regs if not r.startswith(("=", "~"))]
        args = [parse_operand(x) for x in split_top(argstr)] if argstr.strip() else []
        regmap = {"ax": RAX, "di": RDI, "si": RSI, "dx": RDX, "r10": R10, "r8": R8,
                  "r9": R9}
        if len(regs) != len(args):
            raise Unsupported(f"内联汇编约束与实参不匹配: {cons!r}")
        # 依赖寄存器顺序: rax 最后写 (syscall 号), 但其它寄存器不能互相踩
        # （`r9` 是 Linux 第 6 个实参寄存器，`syscall7` 用满它 —— 孪生侧那张表也要同步）
        for regname in ("di", "si", "dx", "r10", "r8", "r9"):
            for (ty, val), r in zip(args, regs):
                if r == regname:
                    self.get(ty, val, regmap[regname])
        for (ty, val), r in zip(args, regs):
            if r == "ax":
                self.get(ty, val, RAX)
        self._emit_syscall()
        if d is not None:
            self.put(d, RAX)

    def _emit_syscall(self) -> None:
        """内联汇编 syscall 落在指令上的那一步。**整个 syscall 面只有这一处** ——
        PE 目标覆盖它去走 `__win_syscall`，其余取参/存值逻辑两边共用。"""
        self.asm.emit(b"\x0F\x05")

    def _ret(self, t):
        if t.strip() != "ret void":
            m = re.match(r"ret\s+(.+?)\s+(\S+)\s*$", t, re.S)
            ty, i = parse_type(m.group(1))
            val = t[len("ret ") + i:].strip()
            if is_agg(ty):
                # 隐藏结果指针在 [rbp+16]: **取它指向的缓冲**, 拷过去, 再把指针回传
                self.asm.emit(mov_rm(RDI, RBP, 16))
                self.agg_addr(val, RSI)
                self.copy(RDI, RSI, size_of(ty))
                self.asm.emit(mov_rm(RAX, RBP, 16))
            else:
                self.get(ty, val, RAX)
        self.asm.emit(b"\xC9\xC3")  # leave; ret

    def _atomic(self, t, d):
        m = re.match(r"atomicrmw\s+add\s+(\w+)\s+(\S+?),\s*(\w+)\s+(\S+?)(?:\s+\w+)?$", t)
        vty, pval = m.group(1), m.group(2)
        self.get_ptr(pval, RCX)
        self.get(m.group(3), m.group(4), RAX)
        self.asm.emit(b"\xF0" + rex(1, RAX, 0, RCX) + b"\x0F\xC1" + _mem(RAX, RCX, 0))
        if d:
            self.put(d, RAX)

    # ---- 函数

    def blk(self, label: str) -> str:
        assert self.cur is not None
        return f"{self.cur.name}${label}"

    def emit_func(self, f: Func) -> None:
        self.cur = f
        self.plan_frame(f)
        self.asm.label(f.name)
        self.asm.emit(b"\x55")          # push rbp
        self.asm.emit(b"\x48\x89\xE5")  # mov rbp, rsp
        if self.frame:
            self.asm.emit(alu_ri(SUB, RSP, self.frame))
        for blk in f.blocks:
            self.cur_block = blk.label
            self.asm.label(self.blk(blk.label))
            for ins in blk.instrs:
                self.lower(ins)
        self.cur = None

    def emit_entry_stub(self, start_name: str) -> None:
        self.asm.label("__entry")
        self.asm.call(start_name)
        self.asm.emit(mov_ri32(RAX, 60))  # exit(0)
        self.asm.emit(mov_ri32(RDI, 0))
        self.asm.emit(b"\x0F\x05")

    def emit_conscript(self, defined: set[str]) -> None:
        """**收编面**（`docs/219` §6 第 1 条）：C 世界里那几个"**可闭合**"的名字，由**我们**提供。

        名字与实现在这里，**表的另一半在 `tools/loment_ports.py`** —— 那边把它判成 `close`，
        这边把它做出来；两处指的是同一批符号（那是 `docs/219` §5.2 条件二："名字的另一端
        在我们手里"）。⚠ 改动这一批时**两边一起改**，否则尺子会报"可闭合"而链接器提供不出。

        **为什么是裸机器码而不是 IR**：调用方是**外部 C 对象**，按 System V 传参
        （`rdi`/`rsi`/`rdx`），而我们自己 IR 定义的函数**实参走栈**。这三个都是叶子函数
        —— 只用 caller-saved 寄存器、不碰栈、不需要序言 —— 所以既不用给发射器加
        "C ABI 定义"那一档（那是 `docs/205` C2 的活），也不需要栈帧。

        三条都**不主张**超出 C 标准的语义：`memcpy` 重叠是 UB（所以前向拷贝就够），
        `memcmp` 只保证符号。

        **优先级**：这几个标签在我们自己的代码**之后、外部对象之前**立起来，而摆对象时的
        规则是"以先来的为准"（`compile_ll` 里那句 `if nm in em.asm.labels: continue`）——
        所以**一个自带 `memcpy` 的库，它的那份会被我们盖掉**。这不是疏忽，是 §5.2 条件二：
        名字的另一端**在我们手里**，"`memcpy` 是什么意思"由我们说了算。

        **内部跳转写成字面字节**（不是 `jcc` + 标签）：这样两侧发的是**同一串字节**，
        自举侧 (`loment/tools/lomelf.lomt` 的 `emit_conscript`) 不必用它的回填机制 ——
        而逐字节一致是 `loment_elf_test` 的自举镜像那格在钉的。位移按下面的布局手算，
        改任何一条指令都要把三处 `rel32`（`0F 84`/`0F 85` 后面那 4 字节）重算。
        """
        a = self.asm
        # memcpy(rdi=d, rsi=s, rdx=n)。布局: test(3) jz(6)=9 | loop@9 … jnz@22(6) | done@28 ret
        a.label("memcpy")
        a.emit(b"\x48\x85\xD2"                      # test rdx, rdx
               b"\x0F\x84\x13\x00\x00\x00"          # jz done   (28 - 9  = +19)
               b"\x8A\x06"                          # mov al, [rsi]
               b"\x88\x07"                          # mov [rdi], al
               b"\x48\xFF\xC6"                      # inc rsi
               b"\x48\xFF\xC7"                      # inc rdi
               b"\x48\xFF\xCA"                      # dec rdx
               b"\x0F\x85\xED\xFF\xFF\xFF"          # jnz loop  (9 - 28  = -19)
               b"\xC3")                             # ret
        # memset(rdi=p, esi=v, rdx=n) —— C 的第二个实参是 `int`，低 8 位在 sil。
        # 布局: test(3) jz(6)=9 | loop@9(9 字节) … jnz@18(6) | done@24 ret
        a.label("memset")
        a.emit(b"\x48\x85\xD2"                      # test rdx, rdx
               b"\x0F\x84\x0F\x00\x00\x00"          # jz done   (24 - 9  = +15)
               b"\x40\x88\x37"                      # mov [rdi], sil
               b"\x48\xFF\xC7"                      # inc rdi
               b"\x48\xFF\xCA"                      # dec rdx
               b"\x0F\x85\xF1\xFF\xFF\xFF"          # jnz loop  (9 - 24  = -15)
               b"\xC3")                             # ret
        # memcmp(rdi=a, rsi=b, rdx=n) -> eax (符号)。
        # 布局: test(3) jz eq(6)=9 | loop@9 jnz diff@15(6) | jnz loop@30(6) | eq@36 ret | diff@39 ret
        a.label("memcmp")
        a.emit(b"\x48\x85\xD2"                      # test rdx, rdx
               b"\x0F\x84\x1B\x00\x00\x00"          # jz eq     (36 - 9  = +27)
               b"\x8A\x07"                          # mov al, [rdi]
               b"\x8A\x0E"                          # mov cl, [rsi]
               b"\x38\xC8"                          # cmp al, cl
               b"\x0F\x85\x12\x00\x00\x00"          # jnz diff  (39 - 21 = +18)
               b"\x48\xFF\xC7"                      # inc rdi
               b"\x48\xFF\xC6"                      # inc rsi
               b"\x48\xFF\xCA"                      # dec rdx
               b"\x0F\x85\xE5\xFF\xFF\xFF"          # jnz loop  (9 - 36  = -27)
               b"\x31\xC0"                          # xor eax, eax
               b"\xC3"                              # ret
               b"\x0F\xB6\xC0"                      # movzx eax, al
               b"\x0F\xB6\xC9"                      # movzx ecx, cl
               b"\x29\xC8"                          # sub eax, ecx
               b"\xC3")                             # ret
        # ---- malloc / free：**内存那一条不收编，是"划掉"**（`docs/219` §2.1）----
        #
        # 它们是一层**换 ABI 的薄壳**，不是一份新分配器：C 按 System V 把实参放在
        # `rdi`，而我们自己的函数**从栈上取实参**（调用方 `push`、`[rbp+16]` 起）。
        # 所以壳做三件事：`push rbp` / `push rdi` / `call`，回来清栈再返回。
        #
        # **只在堆在的时候发**：底下那两条 `@__loment_alloc` / `@__loment_free` 是我们这份
        # IR 里的定义（编译器在程序用到 `alloc`/`free` 时发出）。没有它们就没有"我们的堆"——
        # 那时**不发**，让外部对象的 `malloc` 在链接期响亮地报未定义，而不是发一个悬空调用。
        if "__loment_alloc" in defined:
            a.label("malloc")
            a.emit(b"\x55\x57")                  # push rbp; push rdi (size)
            a.call("__loment_alloc")
            a.emit(b"\x48\x83\xC4\x08\x5D\xC3")  # add rsp, 8; pop rbp; ret
        if "__loment_free" in defined:
            a.label("free")
            a.emit(b"\x55\x57")                  # push rbp; push rdi (ptr)
            a.call("__loment_free")
            a.emit(b"\x48\x83\xC4\x08\x5D\xC3")  # add rsp, 8; pop rbp; ret


# ------------------------------------------------------------------ ELF 写出


def align_up(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def build_elf(text: bytes, data: bytes, bss_size: int, entry: int) -> bytes:
    text_off = 0x1000
    data_off = align_up(text_off + len(text), 0x1000)
    file_end = data_off + len(data)

    ehdr = bytearray()
    ehdr += b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
    ehdr += struct.pack("<HHIQQQIHHHHHH", 2, 0x3E, 1, entry, 64, 0, 0, 64, 56, 2, 0, 0, 0)

    def phdr(flags, off, vaddr, filesz, memsz):
        return struct.pack("<IIQQQQQQ", 1, flags, off, vaddr, vaddr, filesz, memsz, 0x1000)

    ph_text = phdr(5, text_off, TEXT_VADDR, len(text), len(text))
    ph_data = phdr(6, data_off, DATA_VADDR, len(data), len(data) + bss_size)

    out = bytearray(ehdr + ph_text + ph_data)
    out += b"\x00" * (text_off - len(out))
    out += text
    out += b"\x00" * (data_off - len(out))
    out += data
    assert len(out) == file_end, (len(out), file_end)
    return bytes(out)


# ------------------------------------------------------------------ PE 目标 (Windows, x86-64)
#
# 同一份 IR 的第二个目标：PE64 控制台程序。为了**去掉 WSL** —— 原来的 Windows 路径要
# 把 ELF 丢进 WSL 跑，PE 产物在 Windows 上原生就能跑。
#
# 与 ELF 目标的差别（逐条记账，别当成"等价"）：
#   * x64 Windows 没有 `syscall` 指令，所以每个内联汇编 syscall 都改发 `call __win_syscall`，
#     由 shim 按 syscall 号派发到 kernel32。**syscall 面只收敛在这一处**。
#   * shim 自己做栈对齐（`and rsp,-16`）—— Loment 自己的帧不保证 16 字节对齐，
#     而被调方（Windows API）里的 `movaps` 会直接 #GP。
#   * 实现了 21 个号，**其余号返回 -1**。第一批 8 个是文件与进程：
#     `exit`(60) `write`(1) `read`(0) `close`(3) `openat`(257) `brk`(12)
#     `getdents64`(217) `newfstatat`(262)。第二批 13 个是**联网**（2026-10-10，docs/217）：
#     `socket`(41) `connect`(42) `accept`(43) `accept4`(288) `sendto`(44) `recvfrom`(45)
#     `shutdown`(48) `bind`(49) `listen`(50) `getsockname`(51) `getpeername`(52)
#     `setsockopt`(54) `getsockopt`(55) —— 由 `ws2_32.dll` 顶上，socket 型 fd 在
#     read/write/close 上分流到 recv/send/closesocket。
#     写要跨平台跑的程序时按这 21 个来 —— 别处用了别的号, 在 PE 上是静默的 -1。
#   * 联网那批是**语义仿真**而不是转发：负 errno、`AF_INET6`=10（Windows 是 23）、
#     `SOL_SOCKET` 与 `SO_*` 的号、`struct timeval` 的超时（Windows 只认毫秒）、
#     `MSG_NOSIGNAL`（Windows 没有这一位）都得翻译。派发面的真话是 `PE_DISPATCH`
#     那张表，判据把它与冻出来的 blob 逐条对齐。
#   * Windows 没有 procfs，argv 靠 shim 从 `GetCommandLineA` 合成 `/proc/self/cmdline`
#     （空格分隔转 NUL 分隔, 并合并连续分隔符）。

PE_IMAGE_BASE = 0x140000000
PE_SEC_ALIGN, PE_FILE_ALIGN = 0x1000, 0x200
# **四个节都钉在固定 RVA**（不是算出来的）。这样 shim 里对 IAT 与静态状态的取址全是编译期
# 常量 —— 整段 shim 与布局**完全无关**，可以原样冻结成一段 blob 交给自举镜像，那边一个回填
# 都不用做。顺带也干掉了"按代码长度重排 .data"的不动点循环。
PE_TEXT_RVA = 0x1000             # 代码（可增长，别超 16 MiB）
PE_IDATA_RVA = 0x1000000         # 导入表 + "/proc/self/cmdline" 字面量（有文件内容）
PE_DATA_RVA = 0x2000000          # 全局变量（可增长，别超 16 MiB）
PE_STATE_RVA = 0x3000000         # shim 的静态状态（纯 bss，不占文件）
PE_STATE_VA = PE_IMAGE_BASE + PE_STATE_RVA
PE_LIT_OFF = 0x1000              # 字面量在 .idata 内的偏移
PE_LIT_VA = PE_IMAGE_BASE + PE_IDATA_RVA + PE_LIT_OFF
PE_CMDLINE = b"/proc/self/cmdline\x00"
PE_IMPORTS = ["ExitProcess", "GetStdHandle", "ReadFile", "WriteFile", "CloseHandle",
              "CreateFileA", "GetFileAttributesA", "VirtualAlloc", "GetCommandLineA",
              "FindFirstFileA", "FindNextFileA"]
# 第二个导入 DLL：联网。名字与上一组无一重名，所以 `slots` 仍是一张按函数名索引的平表。
PE_IMPORTS_WS2 = ["WSAStartup", "socket", "connect", "bind", "listen", "accept",
                  "send", "recv", "sendto", "recvfrom", "closesocket", "shutdown",
                  "setsockopt", "getsockopt", "getpeername", "getsockname",
                  "ioctlsocket", "select", "WSAGetLastError"]
# 描述符表 = 每个 DLL 一条 + 一条全零终止项（少了终止项加载器会把 ILT 当第二条描述符）。
PE_DLLS = [("kernel32.dll", PE_IMPORTS), ("ws2_32.dll", PE_IMPORTS_WS2)]
PE_IMPORT_DESC_BYTES = 20 * (len(PE_DLLS) + 1)
PE_STD_INPUT, PE_STD_OUTPUT, PE_STD_ERROR = -10, -11, -12
SYS_READ, SYS_WRITE, SYS_CLOSE, SYS_BRK, SYS_EXIT = 0, 1, 3, 12, 60
SYS_GETDENTS64, SYS_OPENAT, SYS_NEWFSTATAT = 217, 257, 262
SYS_POLL, SYS_SELECT = 7, 23
SYS_SOCKET, SYS_CONNECT, SYS_ACCEPT, SYS_ACCEPT4 = 41, 42, 43, 288
SYS_SENDTO, SYS_RECVFROM, SYS_SHUTDOWN = 44, 45, 48
SYS_BIND, SYS_LISTEN, SYS_GETSOCKNAME, SYS_GETPEERNAME = 49, 50, 51, 52
SYS_SETSOCKOPT, SYS_GETSOCKOPT = 54, 55
#: **PE 运行时的派发面 —— 这是一张表，不是一段手写的发射序列。**
#:
#: 每条：`(Linux 系统调用号, 面名, 处理块标签)`。`__win_syscall` 的序言照这张表生成
#: （`emit_win_shim` 里那个循环），所以 —— 这是本文档这一格的关键 ——
#: **"给运行时加自己的系统调用 / 划自己的面"改的是数据**，不是手写的机器码。
#:
#: 面名由**用的人**定，不是这里内建的闭集：`file`/`mem`/`proc`/`net` 只是仓库自己的划法。
#: 谁想把自己那族号叫 `peer`、`ledger`、`radio` 都行 —— 表是他的。这一点是刻意的：
#: 在这门语言里**万物可改**，连执行者本身也可改，所以这里能做的从来不是"设闸"，
#: 只能是把"改了什么"变成**看得见的东西**。
#:
#: **看得见的形态就是这段字节的哈希**：表 → 生成的派发面 → `--dump-win-shim` 冻出的
#: `loment/tools/win_shim_data.lomt` → 发布清单里的 sha256（`loment/build/SHA256SUMS`）。
#: 于是"两端跑的是不是同一张表"不靠版本号，靠**字节**。
#: 判据：`tools/loment_pe_test.py::test_pe_dispatch_table_is_the_single_source`
#: （表决定字节，双向可证）。
PE_SYSCALLS = (
    (SYS_EXIT, "proc", "__ws_exit"),
    (SYS_WRITE, "file", "__ws_write"),
    (SYS_READ, "file", "__ws_read"),
    (SYS_CLOSE, "file", "__ws_close"),
    (SYS_OPENAT, "file", "__ws_openat"),
    (SYS_BRK, "mem", "__ws_brk"),
    (SYS_GETDENTS64, "file", "__ws_getdents"),
    (SYS_NEWFSTATAT, "file", "__ws_fstatat"),
    (SYS_POLL, "net", "__ws_poll"),
    (SYS_SOCKET, "net", "__ws_socket"),
    (SYS_CONNECT, "net", "__ws_connect"),
    (SYS_ACCEPT, "net", "__ws_accept"),
    (SYS_ACCEPT4, "net", "__ws_accept4"),
    (SYS_SENDTO, "net", "__ws_sendto"),
    (SYS_RECVFROM, "net", "__ws_recvfrom"),
    (SYS_SHUTDOWN, "net", "__ws_shutdown"),
    (SYS_BIND, "net", "__ws_bind"),
    (SYS_LISTEN, "net", "__ws_listen"),
    (SYS_GETSOCKNAME, "net", "__ws_getsockname"),
    (SYS_GETPEERNAME, "net", "__ws_getpeername"),
    (SYS_SETSOCKOPT, "net", "__ws_setsockopt"),
    (SYS_GETSOCKOPT, "net", "__ws_getsockopt"),
)
#: 从表派生 —— 别手写第二份（那样两处会漂，而这是个只需要一处的信息）。
PE_DISPATCH = [num for num, _surface, _label in PE_SYSCALLS]


def _pe_table_check() -> None:
    """表是**用户改的**，所以只在"写得不成形"时拦一下 —— 不拦"你声明了什么"。

    号重复会让后一条**静默**变成死代码；面名或处理块空着会让计数与派发对不上。
    这两件都属于"表写坏了"，报出来比产出一段坏机器码强。
    """
    seen: set = set()
    for num, surface, label in PE_SYSCALLS:
        if num in seen:
            raise ElfError(f"派发表里号重复: {num}（后一条会静默变成死代码）")
        if not surface or not label:
            raise ElfError(f"派发表里 {num} 缺面名或处理块")
        seen.add(num)


def pe_surface_sites() -> dict:
    """每个面各领走几个号 —— **运行时这一半**的"面有多大"。

    与 Potato 里 `boundary` / `gc_ladder` 是同一个动作（把一件事变成可数的数，
    `docs/210` §3）：只不过这一半数的是**运行时实现了的号**，另一半（`docs/218` §7）
    数的是**单元里的调用点**。两个数合起来才叫"联网面"。
    """
    _pe_table_check()
    counts: dict = {}
    for _num, surface, _label in PE_SYSCALLS:
        counts[surface] = counts.get(surface, 0) + 1
    return counts
PE_O_WRONLY, PE_O_CREAT, PE_O_TRUNC = 1, 0x40, 0x200
# Linux 语义 → WinSock 的翻译表（两边**不同名同值**，所以是仿真不是转发）
# Linux `poll` 的事件位。**与 WinSock 的 `POLL*` 不同值**（那边 POLLRDNORM=0x100、
# POLLWRNORM=0x10），所以 shim 进出各映射一次 —— 直接透传就是静默错值。
LINUX_POLLIN, LINUX_POLLOUT = 0x0001, 0x0004
LINUX_POLLERR, LINUX_POLLHUP, LINUX_POLLNVAL = 0x0008, 0x0010, 0x0020
WS2_SOL_SOCKET = 0xFFFF
WS2_SO = {2: 0x0004, 9: 0x0008, 7: 0x1001, 8: 0x1002, 21: 0x1005, 20: 0x1006, 4: 0x1007}
WS2_SO_RCVTIMEO, WS2_SO_SNDTIMEO = 0x1006, 0x1005
WIN_AF_INET6, LINUX_AF_INET6 = 23, 10
WIN_FIONBIO = 0x8004667E
LINUX_MSG_NOSIGNAL = 0x4000
LINUX_SOCK_NONBLOCK, LINUX_SOCK_CLOEXEC = 0x800, 0x80000
# WSA 错误 → 负 errno。WSAETIMEDOUT 归 EAGAIN，与 Linux 上 SO_RCVTIMEO 到点的表现一致
# （docs/217 §超时那一格：`read` 返回 -11，不是 -110）。
WSA_ERRNO = [(10035, 11), (10060, 11), (10036, 115), (10038, 88), (10047, 97),
             (10048, 98), (10049, 99), (10051, 101), (10054, 104), (10061, 111),
             (10065, 113), (10022, 22)]

# shim 的静态状态：排在 .data 的**零填充尾巴**上（不占文件的 raw 字节，靠 VirtualSize>RawDataSize）。
WS_FD_COUNT = 64
WS_FD_SIZE = 24                                # kind(u32) / handle(u64) / pos(u64)
WS_FD_KIND, WS_FD_HANDLE, WS_FD_POS = 0, 8, 16
WS_FD_FREE, WS_FD_FILE, WS_FD_DIR, WS_FD_CMD, WS_FD_SOCK = 0, 1, 2, 3, 4
WS_BRK = WS_FD_COUNT * WS_FD_SIZE              # brk 当前值 / 上界 / cmdline 长度 / cmdline 缓冲
WS_BRK_END = WS_BRK + 8
WS_CMD_LEN = WS_BRK_END + 8
WS_CMD_BUF = WS_CMD_LEN + 8
WS_CMD_CAP = 8192
WS_DIR_BUF = WS_CMD_BUF + WS_CMD_CAP           # 每个 fd 一块目录清单缓冲
WS_DIR_CAP = 16384
WS_TMP = WS_DIR_BUF + WS_FD_COUNT * WS_DIR_CAP  # 路径翻译的暂存（不动调用方的缓冲）
WS_TMP_CAP = 4096
WS_TMP2 = WS_TMP + WS_TMP_CAP                   # 目录枚举 / dirent 记录暂存
WS_TMP2_CAP = 4096
WS_FIND = WS_TMP2 + WS_TMP2_CAP                 # WIN32_FIND_DATAA（320 字节）
WS_FIND_NAME = 44                               # cFileName 在 FIND_DATAA 里的偏移
WS_WSA = WS_FIND + 320                          # WSAStartup 只做一次的标志
WS_WSADATA = WS_WSA + 8                         # WSADATA（408 字节，留 512）
WS_WSADATA_CAP = 512
WS_SOCKTMP = WS_WSADATA + WS_WSADATA_CAP        # timeval↔毫秒 / FIONBIO 的暂存
WS_SOCKTMP_CAP = 256
WS_ADDR_FIX = WS_SOCKTMP + 240                  # "AF_INET6 的 family 字被我改过"标志
WS_FDSETS = WS_SOCKTMP + WS_SOCKTMP_CAP         # `poll`/`select` 用：三个 WinSock fd_set
WS_FDSET_CAP = 520                              # 4（count）+ 64 × 8（SOCKET）= 516 → 对齐到 520
WS_POLLTV = WS_FDSETS + 3 * WS_FDSET_CAP        # WinSock 的 timeval（两个 32 位 long）
WS_SIZE = WS_POLLTV + 16
WS_HEAP = 64 * 1024 * 1024
WS_CP_UTF8, WS_CP_ACP = 65001, 0               # 多字节码页（cmdline 走 A 版 API 要显式转）


def build_pe_idata() -> tuple[bytes, dict]:
    """`PE_DLLS` 的导入表 + cmdline 字面量。返回 (blob, {函数名: IAT 槽 VA})。

    描述符表**必须**以一条全零描述符终止：少了它，加载器会把紧随其后的 ILT 当成第二条
    描述符，导入解析中途失败、IAT 保持未填，随后 `call rax` 直接崩（症状是 SIGSEGV）。

    多个 DLL 只是把这一套排两遍：每条描述符指自己那条 ILT / IAT / 名字串，终止项仍在最后。
    `slots` 仍按函数名索引（两组无一重名）；这些都还是 RVA 相对量，blob 照旧与布局无关。
    """
    d = PE_IDATA_RVA
    off = d + 20 * (len(PE_DLLS) + 1)              # 描述符表（含全零终止项）
    ilts, iats = [], []
    for _name, funcs in PE_DLLS:
        ilts.append(off)
        off += (len(funcs) + 1) * 8
    for _name, funcs in PE_DLLS:
        iats.append(off)
        off += (len(funcs) + 1) * 8
    hint_rvas, blobs = [], []
    for _name, funcs in PE_DLLS:
        for nm in funcs:
            b = struct.pack("<H", 0) + nm.encode() + b"\x00"
            if len(b) % 2:
                b += b"\x00"
            hint_rvas.append(off)
            blobs.append((off, b))
            off += len(b)
    name_rvas, name_blobs = [], []
    for dll_name, _funcs in PE_DLLS:
        b = dll_name.encode() + b"\x00"
        name_rvas.append(off)
        name_blobs.append((off, b))
        off += len(b)

    out = bytearray(max(off - d, PE_LIT_OFF + len(PE_CMDLINE)))
    slots, k = {}, 0
    for gi, (_name, funcs) in enumerate(PE_DLLS):
        struct.pack_into("<IIIII", out, gi * 20, ilts[gi], 0, 0, name_rvas[gi], iats[gi])
        for j, nm in enumerate(funcs):
            struct.pack_into("<Q", out, ilts[gi] - d + j * 8, hint_rvas[k])
            struct.pack_into("<Q", out, iats[gi] - d + j * 8, hint_rvas[k])
            slots[nm] = PE_IMAGE_BASE + iats[gi] + j * 8
            k += 1
    for at, b in blobs:
        out[at - d:at - d + len(b)] = b
    for at, b in name_blobs:
        out[at - d:at - d + len(b)] = b
    out[PE_LIT_OFF:PE_LIT_OFF + len(PE_CMDLINE)] = PE_CMDLINE   # shim 用常量取址，无需回填
    return bytes(out), slots


def _call_iat(asm: Asm, addr: int) -> None:
    """call qword ptr [addr]。地址是**编译期常量**（IAT 在固定 RVA），所以不需要回填。"""
    asm.emit(b"\x48\xA1" + struct.pack("<Q", addr))   # mov rax, [abs64]
    asm.emit(b"\xFF\xD0")                             # call rax


def emit_win_shim(em: "PeEmitter", slots: dict) -> None:
    """`__win_syscall`: rax = syscall 号，参数按 Linux 习惯在 rdi/rsi/rdx/r10/r8。

    Linux 的 `syscall` 把号放 rax、参数放 rdi/rsi/rdx/r10/r8；这里保持同一套寄存器约定，
    所以 `_call_asm` 的取参代码一个字都不用改，只是把 `0F 05` 换成 `call __win_syscall`。

    状态基址放 `rbx`：Loment 生成的代码不用 rbx/r12-r15，Windows API 调用又会保存它，
    所以它在整段 shim 里稳定。Loment 侧写的是 **Linux 语义**（brk 给堆、/proc/self/cmdline
    给 argv、linux_dirent64 给目录项），所以这里是**语义仿真**，不是"差不多能用"。

    派发面照 `PE_SYSCALLS` 那张表生成（见那里的注释）—— 表是**用户改的**，
    所以这里先核一遍"写得成不成形"，别把一张坏表变成一段坏机器码。
    """
    _pe_table_check()
    a = em.asm

    def i32(v):
        return struct.pack("<I", v & 0xFFFFFFFF)

    def cmp_eax(v):
        a.emit(b"\x3D" + i32(v))

    def cmp_ecx(v):
        a.emit(b"\x81\xF9" + i32(v))

    def cmp_edi(v):
        if -128 <= v <= 127:
            a.emit(b"\x83\xFF" + bytes([v & 0xFF]))
        else:
            a.emit(b"\x81\xFF" + i32(v))

    def jcc_l(cc, lbl):
        a.emit(b"\x0F" + bytes([0x80 + CC[cc]]))
        a.fixups.append((a.here(), lbl, 4, "rel"))
        a.emit(b"\x00\x00\x00\x00")

    def jmp_l(lbl):
        a.emit(b"\xE9")
        a.fixups.append((a.here(), lbl, 4, "rel"))
        a.emit(b"\x00\x00\x00\x00")

    def api(name):
        _call_iat(a, slots[name])

    def movi(reg, v):
        a.emit(mov_ri(reg, v))

    def movi32(reg, v):
        a.emit(mov_ri32(reg, v))

    def imm_label(reg, lbl):
        a.emit(mov_ri(reg, 0))
        a.fixups.append((a.here() - 8, lbl, 8, "abs"))

    def ld(dst, base, disp):
        a.emit(mov_rm(dst, base, disp))

    def ld32(dst, base, disp):
        a.emit(rex(0, dst, 0, base) + b"\x8B" + _mem(dst, base, disp))

    def st(base, disp, src):
        a.emit(mov_mr(base, disp, src))

    def st32(base, disp, src):
        a.emit(rex(0, src, 0, base) + b"\x89" + _mem(src, base, disp))

    def st8(base, disp, src):
        a.emit(rex(0, src, 0, base) + b"\x88" + _mem(src, base, disp))

    def test_rax():
        a.emit(b"\x48\x85\xC0")

    def test_rr(d, s):
        a.emit(rex(1, s, 0, d) + b"\x85" + modrm(3, s, d))

    def test_ecx():
        a.emit(b"\x85\xC9")

    def test_al():
        a.emit(b"\x84\xC0")

    def load_al(base):
        a.emit(rex(0, 0, 0, base) + b"\x8A" + modrm(0, 0, base))

    def store_al(base):
        a.emit(rex(0, 0, 0, base) + b"\x88" + modrm(0, 0, base))

    def cmp_al(v):
        a.emit(b"\x3C" + bytes([v]))

    def cmp_al_mem(base):
        a.emit(rex(0, 0, 0, base) + b"\x3A" + modrm(0, 0, base))

    def add_ri(reg, v):
        a.emit(rex(1, 0, 0, reg) + b"\x83" + modrm(3, 0, reg) + bytes([v]))

    def sub_ri(reg, v):
        a.emit(rex(1, 0, 0, reg) + b"\x83" + modrm(3, 5, reg) + bytes([v]))

    def imul_ri(reg, v):
        a.emit(rex(1, reg, 0, reg) + b"\x6B" + modrm(3, reg, reg) + bytes([v & 0xFF]))

    def add_rr(d, s):
        # 注意：alu_rr 收的是**真 opcode**（0x01=add / 0x29=sub）；模块常量 ADD/SUB 是给
        # alu_ri 用的 Group1 /digit —— 传错会让 `add r64,r64` 静默变成 `add r/m8,r8`。
        a.emit(alu_rr(0x01, d, s))

    def sub_rsp(v):
        a.emit(alu_ri(SUB, RSP, v))

    def and_ri(reg, imm8):
        a.emit(rex(1, 0, 0, reg) + b"\x83" + modrm(3, 4, reg) + bytes([imm8 & 0xFF]))

    def st16(base, disp, src):
        a.emit(b"\x66" + rex(0, src, 0, base) + b"\x89" + _mem(src, base, disp))

    def ld16z(dst, base, disp):                 # movzx r32, word [base+disp]
        a.emit(rex(0, dst, 0, base) + b"\x0F\xB7" + _mem(dst, base, disp))

    def test_edx(v):
        a.emit(b"\xF7\xC2" + i32(v))

    def sub_rr(d, s):
        a.emit(alu_rr(0x29, d, s))

    def entry_of_fd():   # edi -> rax = &fd 表项（edi 必须 < WS_FD_COUNT）
        a.emit(b"\x89\xF8")                     # mov eax, edi
        imul_ri(RAX, WS_FD_SIZE)
        add_rr(RAX, RBX)

    def cmp_edx(v):
        a.emit(b"\x81\xFA" + i32(v))            # cmp edx, imm32

    def cmp_eax_m1():
        a.emit(b"\x83\xF8\xFF")                 # cmp eax, -1（**32 位**返回值：SOCKET_ERROR）

    def cmp_rax_m1():
        a.emit(b"\x48\x83\xF8\xFF")             # cmp rax, -1（句柄 / SOCKET）

    def zext_eax():
        a.emit(b"\x89\xC0")                     # mov eax, eax：int 返回值的上 32 位是未定义的

    def xor_eax():
        a.emit(b"\x31\xC0")                     # xor eax, eax（成功返回 0）

    def add_rsp28():
        a.emit(b"\x48\x83\xC4\x28")             # add rsp, 0x28

    def ret_():
        a.emit(b"\xC3")

    a.label("__win_syscall")
    a.emit(b"\x55")                             # push rbp
    a.emit(b"\x48\x89\xE5")                     # mov rbp, rsp
    a.emit(b"\x48\x83\xE4\xF0")                 # and rsp, -16
    sub_rsp(0x80)                               # 32 shadow + 第五~七参 + 各 case 的暂存
                                                # （`poll` 那一格要用到 0x60/0x68，
                                                #   所以从 0x60 抬到 0x80；既有格位不动）
    movi(RBX, PE_STATE_VA)

    # 派发面**照表生成** —— 加号、改面、换名字改的是 `PE_SYSCALLS`，不是这段代码。
    for _num, _surface, _label in PE_SYSCALLS:
        cmp_eax(_num)
        jcc_l("e", _label)
    a.emit(b"\x48\xC7\xC0\xFF\xFF\xFF\xFF")     # mov rax, -1（表外的号）
    jmp_l("__ws_ret")

    a.label("__ws_ret")
    a.emit(b"\x48\x89\xEC")                     # mov rsp, rbp
    a.emit(b"\x5D\xC3")                         # pop rbp; ret

    a.label("__ws_fail")                        # 统一的失败出口（rax = -1）
    a.emit(b"\x48\xC7\xC0\xFF\xFF\xFF\xFF")
    jmp_l("__ws_ret")

    # ---- exit(code) ------------------------------------------------------
    a.label("__ws_exit")
    a.emit(mov_rr(RCX, RDI))
    api("ExitProcess")
    a.emit(b"\x0F\x0B")                         # ud2（不该回来）

    # ---- fd -> HANDLE（子程序：in edi, out rax；0 = 无效）------------------
    a.label("__ws_handle")
    sub_rsp(0x28)
    cmp_edi(1)
    jcc_l("e", "__wsh_out")
    cmp_edi(2)
    jcc_l("e", "__wsh_err")
    cmp_edi(0)
    jcc_l("e", "__wsh_in")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__wsh_bad")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__wsh_bad")
    ld(RAX, RAX, WS_FD_HANDLE)
    a.emit(b"\x48\x83\xC4\x28\xC3")             # add rsp,0x28; ret
    a.label("__wsh_in")
    movi32(RCX, PE_STD_INPUT & 0xFFFFFFFF)
    jmp_l("__wsh_go")
    a.label("__wsh_out")
    movi32(RCX, PE_STD_OUTPUT & 0xFFFFFFFF)
    jmp_l("__wsh_go")
    a.label("__wsh_err")
    movi32(RCX, PE_STD_ERROR & 0xFFFFFFFF)
    a.label("__wsh_go")
    api("GetStdHandle")
    a.emit(b"\x48\x83\xC4\x28\xC3")
    a.label("__wsh_bad")
    a.emit(b"\x31\xC0\x48\x83\xC4\x28\xC3")     # xor eax,eax; add rsp,0x28; ret

    # ---- write(fd, buf, len) --------------------------------------------
    a.label("__ws_write")
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    a.call("__ws_sock_of_fd")                   # socket 型 fd（edi 还是 fd）
    test_rax()
    jcc_l("ne", "__ws_write_sock")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)                          # 第五参 lpOverlapped = NULL
    api("WriteFile")
    ld(RAX, RSP, 0x38)                          # 返回写入字节数（照 write(2)）
    jmp_l("__ws_ret")

    # socket 型 fd：write 落到 send（Windows 上 WriteFile 写不了 socket 句柄）。
    # 掩掉 MSG_NOSIGNAL：Windows 没这一位，那边也没有 SIGPIPE。
    a.label("__ws_write_sock")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x45\x31\xC9")
    api("send")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    jmp_l("__ws_ret")

    # ---- close(fd) -------------------------------------------------------
    a.label("__ws_close")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(3)
    jcc_l("b", "__ws_close_ok")                 # 0/1/2 是标准流，关掉也当成功
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_close_ok")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__ws_close_ok")
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_close_zap")                # CMD 没有句柄可关
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_close_sock")
    st(RSP, 0x28, RAX)                          # 保存表项指针（CloseHandle 会踩寄存器）
    ld(RCX, RAX, WS_FD_HANDLE)
    api("CloseHandle")
    ld(RAX, RSP, 0x28)
    a.label("__ws_close_zap")
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    st32(RAX, WS_FD_KIND, RCX)
    st(RAX, WS_FD_HANDLE, RCX)
    st(RAX, WS_FD_POS, RCX)
    a.label("__ws_close_ok")
    a.emit(b"\x31\xC0")                         # xor eax, eax
    jmp_l("__ws_ret")

    # socket 型 fd：走 closesocket（rdi 还拿着 fd）
    a.label("__ws_close_sock")
    a.call("__ws_sock_drop")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ---- brk(addr) -------------------------------------------------------
    a.label("__ws_brk")
    ld(RAX, RBX, WS_BRK)
    test_rax()
    jcc_l("ne", "__ws_brk_have")
    sub_rsp(0x28)
    a.emit(b"\x31\xC9")                         # lpAddress = NULL
    movi(RDX, WS_HEAP)
    movi32(R8, 0x3000)                          # MEM_COMMIT|MEM_RESERVE
    movi32(R9, 4)                               # PAGE_READWRITE
    api("VirtualAlloc")
    a.emit(b"\x48\x83\xC4\x28")
    test_rax()
    jcc_l("e", "__ws_fail")
    st(RBX, WS_BRK, RAX)
    movi(RDX, WS_HEAP)
    add_rr(RDX, RAX)
    st(RBX, WS_BRK_END, RDX)
    a.label("__ws_brk_have")
    test_rax()
    jcc_l("e", "__ws_brk_ret")                  # brk(0) 只查询
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_brk_ret")
    ld(RCX, RBX, WS_BRK_END)
    a.emit(b"\x48\x39\xCF")                     # cmp rdi, rcx
    jcc_l("a", "__ws_fail")                     # 越界 -> -1
    movi(RAX, 0)
    ld(RAX, RBX, WS_BRK)
    st(RBX, WS_BRK, RDI)                        # 新 break
    movi(RAX, 0)
    a.emit(mov_rr(RAX, RDI))                    # 返回新 break（Linux 语义）
    jmp_l("__ws_ret")
    a.label("__ws_brk_ret")
    ld(RAX, RBX, WS_BRK)
    jmp_l("__ws_ret")

    # ---- openat(dirfd, path, flags, mode) --------------------------------
    a.label("__ws_openat")
    st(RSP, 0x28, RSI)                          # path
    st(RSP, 0x30, RDX)                          # flags
    movi(R8, PE_LIT_VA)
    a.emit(mov_rr(R9, RSI))
    a.label("__ws_cmp")
    load_al(R9)
    cmp_al_mem(R8)
    jcc_l("ne", "__ws_open_file")
    test_al()
    jcc_l("e", "__ws_open_cmd")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_cmp")

    a.label("__ws_open_cmd")                    # /proc/self/cmdline：合成一个 fd
    a.call("__ws_fill_cmdline")
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")                         # test eax, eax
    jcc_l("s", "__ws_fail")
    movi(RCX, WS_FD_CMD)
    st32(RDX, WS_FD_KIND, RCX)
    movi(RCX, PE_STATE_VA + WS_CMD_BUF)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    st(RDX, WS_FD_POS, RCX)
    jmp_l("__ws_ret")

    a.label("__ws_open_file")
    movi(R10, PE_STATE_VA + WS_TMP)                # 翻译路径到暂存（不动调用方缓冲）
    ld(R9, RSP, 0x28)
    a.label("__ws_tr")
    load_al(R9)
    cmp_al(47)                                  # '/'
    jcc_l("ne", "__ws_tr_store")
    a.emit(b"\xB0" + bytes([92]))               # mov al, '\'
    a.label("__ws_tr_store")
    store_al(R10)
    test_al()
    jcc_l("e", "__ws_tr_done")
    add_ri(R9, 1)
    add_ri(R10, 1)
    jmp_l("__ws_tr")
    a.label("__ws_tr_done")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")             # 目录要单独处理（CreateFileA 打不开目录）
    a.emit(b"\x83\xF8\xFF")                     # cmp eax, -1（不存在）
    jcc_l("e", "__ws_open_create")
    a.emit(b"\xA8\x10")                         # test al, FILE_ATTRIBUTE_DIRECTORY
    jcc_l("e", "__ws_open_create")
    # ---- 目录：拼 "path\*" 开一次枚举，fd 记住 find 句柄 ----
    movi(R8, PE_STATE_VA + WS_TMP2)
    movi(R9, PE_STATE_VA + WS_TMP)
    a.label("__ws_dc")
    load_al(R9)
    store_al(R8)
    test_al()
    jcc_l("e", "__ws_dc_end")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_dc")
    a.label("__ws_dc_end")
    a.emit(b"\xB0" + bytes([92]))               # '\'
    store_al(R8)
    add_ri(R8, 1)
    a.emit(b"\xB0" + bytes([42]))               # '*'
    store_al(R8)
    add_ri(R8, 1)
    a.emit(b"\x31\xC0")
    store_al(R8)
    movi(RCX, PE_STATE_VA + WS_TMP2)
    movi(RDX, PE_STATE_VA + WS_FIND)
    api("FindFirstFileA")
    a.emit(b"\x48\x83\xF8\xFF")                 # cmp rax, -1
    jcc_l("e", "__ws_fail")
    st(RSP, 0x38, RAX)
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    movi(RCX, WS_FD_DIR)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x38)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)                     # 0 = FindFirstFileA 那一条还没发出去
    jmp_l("__ws_ret")

    a.label("__ws_open_create")
    movi(RCX, PE_STATE_VA + WS_TMP)                # lpFileName
    movi32(RDX, 0x80000000)                     # GENERIC_READ
    ld(R11, RSP, 0x30)                          # flags
    a.emit(b"\x41\xF6\xC3\x01")                 # test r11b, O_WRONLY
    jcc_l("e", "__ws_o_acc")
    movi32(RDX, 0x40000000)                     # GENERIC_WRITE
    a.label("__ws_o_acc")
    movi32(R8, 3)                               # FILE_SHARE_READ|WRITE
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d
    movi32(RAX, 3)                              # OPEN_EXISTING
    a.emit(b"\x41\xF6\xC3\x40")                 # test r11b, O_CREAT
    jcc_l("e", "__ws_o_disp")
    movi32(RAX, 2)                              # CREATE_ALWAYS
    a.label("__ws_o_disp")
    st(RSP, 0x20, RAX)
    movi32(RAX, 0x80)                           # FILE_ATTRIBUTE_NORMAL
    st(RSP, 0x28, RAX)
    movi32(RAX, 0)
    st(RSP, 0x30, RAX)
    api("CreateFileA")
    a.emit(b"\x48\x83\xF8\xFF")                 # cmp rax, -1
    jcc_l("e", "__ws_fail")
    st(RSP, 0x38, RAX)                          # 先存句柄
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    movi(RCX, WS_FD_FILE)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x38)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)
    jmp_l("__ws_ret")

    # ---- 内部：分配一个 fd（out eax = fd，rdx = &表项；满则 eax = -1）-----
    a.label("__ws_alloc_fd")
    movi32(RAX, 3)
    a.label("__ws_af_loop")
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_af_full")
    a.emit(mov_rr(RDX, RAX))
    imul_ri(RDX, WS_FD_SIZE)
    add_rr(RDX, RBX)
    ld32(RCX, RDX, WS_FD_KIND)
    test_ecx()
    jcc_l("e", "__ws_af_got")
    add_ri(RAX, 1)
    jmp_l("__ws_af_loop")
    a.label("__ws_af_got")
    a.emit(b"\xC3")                             # ret
    a.label("__ws_af_full")
    movi32(RAX, -1)
    a.emit(b"\xC3")

    # ---- 内部：把 GetCommandLineA 的空格分隔转成 NUL 分隔 ------------------
    a.label("__ws_fill_cmdline")
    sub_rsp(0x28)
    ld(RAX, RBX, WS_CMD_LEN)
    test_rax()
    jcc_l("ne", "__ws_fc_done")
    api("GetCommandLineA")
    # 源指针挪进 r11：**不能拿 rax 当指针又用 al 装字节** —— `movb (%rax), %al`
    # 会把指针自己的低字节写掉，指针每走一步就跳飞（症状是 argv 只剩一个字符）。
    a.emit(mov_rr(R11, RAX))
    movi(R10, PE_STATE_VA + WS_CMD_BUF)
    a.emit(mov_rr(R8, R10))
    movi32(R9, WS_CMD_CAP - 2)
    a.emit(b"\x31\xC0")                         # xor eax, eax
    st(RSP, 0x40, RAX)                          # 「上一个输出的是分隔符」标志
    a.label("__ws_fc_loop")
    test_rr(R9, R9)
    jcc_l("e", "__ws_fc_end")
    load_al(R11)
    test_al()
    jcc_l("e", "__ws_fc_end")
    cmp_al(0x20)                                # ' '
    jcc_l("e", "__ws_fc_sep")
    cmp_al(0x22)                                # '"'
    jcc_l("e", "__ws_fc_sep")
    # 普通字符（注意：只能清 ecx，**不能动 al** —— 它就是待存的字符）
    a.emit(b"\x31\xC9")                         # xor ecx, ecx
    st(RSP, 0x40, RCX)                          # 清标志
    a.label("__ws_fc_put")
    store_al(R8)
    add_ri(R8, 1)
    sub_ri(R9, 1)
    add_ri(R11, 1)
    jmp_l("__ws_fc_loop")
    # 分隔符（空格或引号）：收拢**一整段**，至多产出一个 NUL。
    # cmd 给子进程的命令行与 PowerShell/bash 的形态不同（实测会多一个空参数），
    # 所以这里按"分隔符串"处理而不是"一个字符一个分隔符"。
    a.label("__ws_fc_sep")
    ld(RAX, RSP, 0x40)
    test_rax()
    jcc_l("ne", "__ws_fc_skip1")                # 已经出过分隔符：只吞掉这个字符
    a.emit(b"\x4D\x39\xD0")                     # cmp r8, r10：还没写过任何字符就别写 NUL
    jcc_l("e", "__ws_fc_skip1")
    movi(RCX, 1)
    st(RSP, 0x40, RCX)
    a.emit(b"\x30\xC0")                         # xor al, al（**只能清 al**）
    jmp_l("__ws_fc_put")                        # put 负责存 + 推进源指针
    a.label("__ws_fc_skip1")
    add_ri(R11, 1)
    jmp_l("__ws_fc_loop")
    a.label("__ws_fc_end")
    ld(RAX, RSP, 0x40)
    test_rax()
    jcc_l("ne", "__ws_fc_term")                 # 已经以分隔符收尾了，别再补一个
    a.emit(b"\x30\xC0")                         # xor al, al
    store_al(R8)
    add_ri(R8, 1)
    a.label("__ws_fc_term")
    a.emit(mov_rr(RAX, R8))
    a.emit(b"\x4C\x29\xD0")                     # sub rax, r10
    st(RBX, WS_CMD_LEN, RAX)
    a.label("__ws_fc_done")
    a.emit(b"\x48\x83\xC4\x28\xC3")

    # ---- read(fd, buf, len) ---------------------------------------------
    a.label("__ws_read")
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_read_file")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_SOCK)
    jcc_l("e", "__ws_read_sock")
    cmp_ecx(WS_FD_CMD)
    jcc_l("e", "__ws_read_cmd")
    a.label("__ws_read_file")
    a.call("__ws_handle")
    test_rax()
    jcc_l("e", "__ws_fail")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x4C\x8D\x4C\x24\x28")             # lea r9, [rsp+0x28]
    movi32(RAX, 0)
    st(RSP, 0x20, RAX)
    api("ReadFile")
    ld32(RAX, RSP, 0x28)
    jmp_l("__ws_ret")

    # socket 型 fd：read 落到 recv（Windows 上 ReadFile 读不了 socket 句柄）
    a.label("__ws_read_sock")
    a.call("__ws_sock_of_fd")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    a.emit(b"\x45\x31\xC9")                     # xor r9d, r9d（flags = 0）
    api("recv")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    zext_eax()
    jmp_l("__ws_ret")

    a.label("__ws_read_cmd")
    ld(RDX, RAX, WS_FD_POS)                     # pos
    ld(RCX, RAX, WS_FD_HANDLE)                  # cmd 缓冲地址
    add_rr(RCX, RDX)
    ld(R8, RBX, WS_CMD_LEN)
    a.emit(alu_rr(0x29, R8, RDX))               # 剩余 = 总长 - pos（0x29 = sub r/m64, r64）
    test_rr(R8, R8)
    jcc_l("le", "__ws_rc_zero")
    ld(R9, RSP, 0x38)                           # 调用方要的字节数
    a.emit(b"\x4D\x39\xC8")                     # cmp r8, r9
    jcc_l("be", "__ws_rc_n")
    a.emit(mov_rr(R8, R9))
    a.label("__ws_rc_n")
    st(RSP, 0x40, R8)                           # 本次拷贝字节数
    ld(R10, RSP, 0x30)                          # dst
    a.label("__ws_rc_cp")
    test_rr(R8, R8)
    jcc_l("e", "__ws_rc_done")
    load_al(RCX)
    store_al(R10)
    add_ri(RCX, 1)
    add_ri(R10, 1)
    a.emit(b"\x49\x83\xE8\x01")                 # sub r8, 1
    jmp_l("__ws_rc_cp")
    a.label("__ws_rc_done")
    ld(R8, RSP, 0x40)
    entry_of_fd()                               # rdi 还是 fd
    ld(RCX, RAX, WS_FD_POS)
    add_rr(RCX, R8)
    st(RAX, WS_FD_POS, RCX)
    a.emit(mov_rr(RAX, R8))
    jmp_l("__ws_ret")
    a.label("__ws_rc_zero")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ---- newfstatat(dirfd, path, stb, flags) -----------------------------
    # 消费方只读 st_mode（`load32(stb, 24) & S_IFMT`），所以只填这一个字段。
    a.label("__ws_fstatat")
    st(RSP, 0x28, RDX)                          # stb
    movi(R8, PE_STATE_VA + WS_TMP)
    a.emit(mov_rr(R9, RSI))
    a.label("__ws_fs_tr")
    load_al(R9)
    cmp_al(47)
    jcc_l("ne", "__ws_fs_st")
    a.emit(b"\xB0" + bytes([92]))
    a.label("__ws_fs_st")
    store_al(R8)
    test_al()
    jcc_l("e", "__ws_fs_done")
    add_ri(R9, 1)
    add_ri(R8, 1)
    jmp_l("__ws_fs_tr")
    a.label("__ws_fs_done")
    movi(RCX, PE_STATE_VA + WS_TMP)
    api("GetFileAttributesA")
    a.emit(b"\x83\xF8\xFF")
    jcc_l("e", "__ws_fail")
    ld(RDX, RSP, 0x28)
    a.emit(b"\xA8\x10")                         # test al, FILE_ATTRIBUTE_DIRECTORY
    movi32(RCX, 0x81ED)                         # S_IFREG | 0755
    jcc_l("e", "__ws_fs_reg")
    movi32(RCX, 0x41ED)                         # S_IFDIR | 0755
    a.label("__ws_fs_reg")
    st32(RDX, 24, RCX)                          # st_mode
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ---- getdents64(fd, buf, n) -----------------------------------------
    # 一次发一条 linux_dirent64（消费方本来就是 while 循环读到 0 为止）。
    a.label("__ws_getdents")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_fail")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_DIR)
    jcc_l("ne", "__ws_fail")
    st(RSP, 0x40, RAX)                          # &表项
    st(RSP, 0x48, RSI)                          # 调用方缓冲
    ld(RCX, RAX, WS_FD_POS)
    test_rr(RCX, RCX)
    jcc_l("e", "__ws_gd_emit")                  # pos=0：FindFirstFileA 那条还没发
    ld(RCX, RAX, WS_FD_HANDLE)
    movi(RDX, PE_STATE_VA + WS_FIND)
    api("FindNextFileA")
    a.emit(b"\x85\xC0")
    jcc_l("e", "__ws_gd_end")
    a.label("__ws_gd_emit")
    ld(RAX, RSP, 0x40)
    movi(RCX, 1)
    st(RAX, WS_FD_POS, RCX)
    movi(R8, PE_STATE_VA + WS_TMP2)                # 记录缓冲
    movi(R9, 1)
    st(R8, 0, R9)                               # d_ino
    a.emit(b"\x31\xC9")
    st(R8, 8, RCX)                              # d_off
    movi(R10, PE_STATE_VA + WS_FIND + WS_FIND_NAME)
    a.emit(mov_rr(R11, R10))
    a.label("__ws_gd_nl")
    load_al(R11)
    test_al()
    jcc_l("e", "__ws_gd_len")
    add_ri(R11, 1)
    jmp_l("__ws_gd_nl")
    a.label("__ws_gd_len")
    sub_rr(R11, R10)                            # namelen
    movi(RAX, 19)
    add_rr(RAX, R11)
    add_ri(RAX, 1)
    add_ri(RAX, 7)
    and_ri(RAX, -8)                             # d_reclen = align(19+namelen+1, 8)
    st16(R8, 16, RAX)
    st(RSP, 0x50, RAX)
    movi(RDX, PE_STATE_VA + WS_FIND)
    ld32(RDX, RDX, 0)                           # dwFileAttributes
    test_edx(0x10)
    movi32(RCX, 8)                              # DT_REG
    jcc_l("e", "__ws_gd_dt")
    movi32(RCX, 4)                              # DT_DIR
    a.label("__ws_gd_dt")
    a.emit(b"\x41\x88\x48\x12")                 # mov [r8+18], cl
    movi(R9, PE_STATE_VA + WS_TMP2 + 19)
    a.label("__ws_gd_cp")
    load_al(R10)
    store_al(R9)
    test_al()
    jcc_l("e", "__ws_gd_cpd")
    add_ri(R10, 1)
    add_ri(R9, 1)
    jmp_l("__ws_gd_cp")
    a.label("__ws_gd_cpd")
    ld(R8, RSP, 0x50)                           # reclen
    movi(R9, PE_STATE_VA + WS_TMP2)
    ld(R10, RSP, 0x48)
    st(RSP, 0x58, R8)
    a.label("__ws_gd_out")
    test_rr(R8, R8)
    jcc_l("e", "__ws_gd_outd")
    load_al(R9)
    store_al(R10)
    add_ri(R9, 1)
    add_ri(R10, 1)
    a.emit(b"\x49\x83\xE8\x01")                 # sub r8, 1
    jmp_l("__ws_gd_out")
    a.label("__ws_gd_outd")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_gd_end")
    a.emit(b"\x31\xC0")
    jmp_l("__ws_ret")

    # ================================================================ 联网
    # （docs/217）。这一批是**语义仿真**，不是转发 —— 凡两边不同值的都在这里翻译：
    #   * `AF_INET`=2 两边同值、`sockaddr_in`/`sockaddr_in6` 布局逐字节相同 ⇒ 直通；
    #     **`AF_INET6` Linux=10 / Windows=23** ⇒ 进出各补一次 family 字；
    #   * `SOL_SOCKET` 1→0xffff，`SO_*` 各有各的号（见 `WS2_SO`）；`IPPROTO_TCP`=6 与
    #     `TCP_NODELAY`=1 两边同值 ⇒ 直通；
    #   * `SO_RCVTIMEO`/`SO_SNDTIMEO`：Linux 收 `struct timeval{i64 sec; i64 usec}`，
    #     Windows 只认 32 位毫秒 ⇒ 两种量纲都要算（**除法**，`imul_ri` 只吃 imm8）；
    #   * 失败返回**负 errno**（`__ws_wsa_err` 那张表），不是 SOCKET_ERROR；
    #   * `MSG_NOSIGNAL`(0x4000) 掩掉：Windows 没有这一位，那边也没有 SIGPIPE；
    #   * `SOCK_NONBLOCK`(0x800) → ioctlsocket(FIONBIO)，`SOCK_CLOEXEC`(0x80000) 丢掉。
    #   * **点名不做**（用得着就是 -1）：`poll`/`select`/`epoll`、`sendmsg`/`recvmsg`、
    #     `socketpair`、`AF_UNIX`。
    #
    # 两条纪律，错了是静默的错值：
    #   ① Win64 只保 rbx/rbp/rdi/rsi/r12-r15 ⇒ Linux 的实参（rsi/rdx/r10/r8/r9）在**第一次
    #      API 调用前**全部落到栈槽；
    #   ② 这些 case 直接坐在 `__win_syscall` 的序言栈帧上（rsp 16 对齐、0x60 里已含 32 字节
    #      影子空间）⇒ **不再 `sub rsp,0x28`**，第五参就是 [rsp+0x20]。子程序是 `call`
    #      进来的，只有它们才要自己 sub。（子程序用的格子从 0x40 起，落在调用方 0x20 以下。）

    # ---- 内部：WSAStartup 只做一次（不做则第一次 socket() 报 WSANOTINITIALISED）---
    a.label("__ws_wsa_start")
    sub_rsp(0x28)
    ld(RAX, RBX, WS_WSA)
    test_rax()
    jcc_l("ne", "__ws_wsa_done")
    movi32(RCX, 0x0202)                         # MAKEWORD(2, 2)
    movi(RDX, PE_STATE_VA + WS_WSADATA)
    api("WSAStartup")
    movi(RCX, 1)
    st(RBX, WS_WSA, RCX)                        # 失败也立标志：真正的错由后面那次调用报
    a.label("__ws_wsa_done")
    add_rsp28()
    ret_()

    # ---- 内部：WSA 错误码 → 负 errno -------------------------------------
    a.label("__ws_wsa_err")
    sub_rsp(0x28)
    api("WSAGetLastError")
    add_rsp28()
    for _code, _errno in WSA_ERRNO:
        cmp_eax(_code)
        jcc_l("e", f"__ws_wsa_e{_code}")
    movi(RAX, -1)                               # 表外的一律 -1（明写的上限，见 docs/217）
    ret_()
    for _code, _errno in WSA_ERRNO:
        a.label(f"__ws_wsa_e{_code}")
        movi(RAX, -_errno)
        ret_()

    # ---- 内部：fd → SOCKET（不是 socket 型就 0）---------------------------
    a.label("__ws_sock_of_fd")
    cmp_edi(3)
    jcc_l("b", "__ws_sof_no")                   # 0/1/2 是标准流
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_sof_no")
    entry_of_fd()
    ld32(RCX, RAX, WS_FD_KIND)
    cmp_ecx(WS_FD_SOCK)
    jcc_l("ne", "__ws_sof_no")
    ld(RAX, RAX, WS_FD_HANDLE)
    ret_()
    a.label("__ws_sof_no")
    xor_eax()
    ret_()

    # ---- 内部：SOCKET（rax）→ 一个 socket 型 fd（eax = fd，rdx = &表项）----
    a.label("__ws_sock_alloc")
    sub_rsp(0x28)
    st(RSP, 0x40, RAX)                          # SOCKET：__ws_alloc_fd 会踩 rax/rdx/rcx
    a.call("__ws_alloc_fd")
    a.emit(b"\x85\xC0")                         # test eax, eax
    jcc_l("s", "__ws_sa_full")
    movi(RCX, WS_FD_SOCK)
    st32(RDX, WS_FD_KIND, RCX)
    ld(RCX, RSP, 0x40)
    st(RDX, WS_FD_HANDLE, RCX)
    a.emit(b"\x31\xC9")
    st(RDX, WS_FD_POS, RCX)
    add_rsp28()
    ret_()
    a.label("__ws_sa_full")
    movi32(RAX, -1)
    add_rsp28()
    ret_()

    # ---- 内部：关掉一个 socket 型 fd（edi = fd；表项清零）-----------------
    a.label("__ws_sock_drop")
    cmp_edi(3)
    jcc_l("b", "__ws_sd_done")
    a.emit(mov_rr(RAX, RDI))
    cmp_eax(WS_FD_COUNT)
    jcc_l("ae", "__ws_sd_done")
    entry_of_fd()
    st(RSP, 0x40, RAX)                          # &表项
    ld(RCX, RAX, WS_FD_HANDLE)
    sub_rsp(0x28)
    api("closesocket")
    add_rsp28()
    ld(RAX, RSP, 0x40)                          # 同一个格子：sub/add 前后 RSP 相同
    a.emit(b"\x31\xC9")
    st32(RAX, WS_FD_KIND, RCX)
    st(RAX, WS_FD_HANDLE, RCX)
    st(RAX, WS_FD_POS, RCX)
    a.label("__ws_sd_done")
    ret_()

    # ---- 内部：sockaddr 的 family 字（AF_INET6：Linux 10 ↔ Windows 23）-----
    # 布局逐字节相同，差别只在这两个字，所以**就地改**、进 API 前后各补一次（单线程，
    # 程序在系统调用期间看不到）。空指针合法（connect/accept 的 addr 可以是 0）⇒ 跳过。
    a.label("__ws_addr_in")                     # rdi = &sockaddr
    xor_eax()
    st(RBX, WS_ADDR_FIX, RAX)                   # 默认"没改过"
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_ai_done")
    a.emit(b"\x0F\xB7\x07")                     # movzx eax, word [rdi]
    cmp_eax(LINUX_AF_INET6)
    jcc_l("ne", "__ws_ai_done")
    movi(RCX, WIN_AF_INET6)
    a.emit(b"\x66\x89\x0F")                     # mov word [rdi], cx
    movi(RAX, 1)
    st(RBX, WS_ADDR_FIX, RAX)
    a.label("__ws_ai_done")
    ret_()

    a.label("__ws_addr_restore")                # rdi = &sockaddr
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_ar_done")
    ld(RAX, RBX, WS_ADDR_FIX)
    test_rax()
    jcc_l("e", "__ws_ar_done")
    movi(RCX, LINUX_AF_INET6)
    a.emit(b"\x66\x89\x0F")
    a.label("__ws_ar_done")
    ret_()

    a.label("__ws_addr_out")                    # 出口方向：23 → 10
    test_rr(RDI, RDI)
    jcc_l("e", "__ws_ao_done")
    a.emit(b"\x0F\xB7\x07")
    cmp_eax(WIN_AF_INET6)
    jcc_l("ne", "__ws_ao_done")
    movi(RCX, LINUX_AF_INET6)
    a.emit(b"\x66\x89\x0F")
    a.label("__ws_ao_done")
    ret_()

    # ---- 公共出口 --------------------------------------------------------
    a.label("__ws_not_sock")
    movi(RAX, -88)                              # ENOTSOCK：给的 fd 不是 socket
    jmp_l("__ws_ret")

    a.label("__ws_sock_err")                    # API 失败 → 负 errno
    a.call("__ws_wsa_err")
    jmp_l("__ws_ret")

    def case_fd1(api_name):
        """listen(50) / shutdown(48)：参数两边同值，直通。"""
        st(RSP, 0x30, RSI)
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        a.emit(mov_rr(RCX, RAX))
        ld(RDX, RSP, 0x30)
        api(api_name)
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        xor_eax()
        jmp_l("__ws_ret")

    def case_addr2(api_name):
        """connect(42) / bind(49)：fd, addr, addrlen —— 差别只在 API 名字。"""
        st(RSP, 0x30, RSI)                      # addr
        st(RSP, 0x38, RDX)                      # addrlen
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        st(RSP, 0x40, RAX)                      # SOCKET
        ld(RDI, RSP, 0x30)
        a.call("__ws_addr_in")
        ld(RCX, RSP, 0x40)
        ld(RDX, RSP, 0x30)
        ld(R8, RSP, 0x38)
        api(api_name)
        st(RSP, 0x48, RAX)                      # 返回值
        ld(RDI, RSP, 0x30)
        a.call("__ws_addr_restore")
        ld(RAX, RSP, 0x48)
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        xor_eax()
        jmp_l("__ws_ret")

    def case_peername(api_name):
        """getsockname(51) / getpeername(52)：出口方向要补 family 字。"""
        st(RSP, 0x30, RSI)                      # addr（可为 0）
        st(RSP, 0x38, RDX)                      # addrlen 指针
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        a.emit(mov_rr(RCX, RAX))
        ld(RDX, RSP, 0x30)
        ld(R8, RSP, 0x38)
        api(api_name)
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        ld(RDI, RSP, 0x30)
        a.call("__ws_addr_out")
        xor_eax()
        jmp_l("__ws_ret")

    def case_sendrecv(api6, api4, is_send, tag):
        """sendto(44) / recvfrom(45)：6 参，第 5/6 参走栈（[rsp+0x20]/[rsp+0x28]）。

        **addr 为 NULL 时走 `send`/`recv`**：Linux 上 `sendto(已连接, …, NULL, …)` 就是
        `send`，而 WinSock 的 `sendto` 拿 NULL 加非零 tolen 会报 WSAEFAULT —— 不分流就是
        一个"Linux 能跑、Windows 报错"的暗沟。
        """
        st(RSP, 0x30, RSI)                      # buf
        st(RSP, 0x38, RDX)                      # len
        st(RSP, 0x40, R10)                      # flags
        st(RSP, 0x48, R8)                       # addr
        st(RSP, 0x50, R9)                       # addrlen
        a.call("__ws_sock_of_fd")
        test_rax()
        jcc_l("e", "__ws_not_sock")
        st(RSP, 0x58, RAX)                      # SOCKET
        a.emit(mov_rr(RCX, RAX))
        ld(RDX, RSP, 0x30)
        ld(R8, RSP, 0x38)
        ld(R9, RSP, 0x40)
        a.emit(alu_ri(AND_, R9, ~LINUX_MSG_NOSIGNAL))                # 掩 MSG_NOSIGNAL
        ld(RAX, RSP, 0x48)
        test_rax()
        jcc_l("e", f"__ws_sr_{tag}_plain")
        st(RSP, 0x20, RAX)                      # 第 5 参 = addr
        ld(RAX, RSP, 0x50)
        st(RSP, 0x28, RAX)                      # 第 6 参 = addrlen
        ld(RDI, RSP, 0x48)
        if is_send:
            a.call("__ws_addr_in")
        api(api6)
        st(RSP, 0x50, RAX)                      # 返回值（0x50 那个 addrlen 已经用完了）
        ld(RDI, RSP, 0x48)
        if is_send:
            a.call("__ws_addr_restore")
        else:
            a.call("__ws_addr_out")
        ld(RAX, RSP, 0x50)
        jmp_l(f"__ws_sr_{tag}_fin")
        a.label(f"__ws_sr_{tag}_plain")         # addr == NULL ⇒ send/recv
        api(api4)
        a.label(f"__ws_sr_{tag}_fin")
        cmp_eax_m1()
        jcc_l("e", "__ws_sock_err")
        zext_eax()
        jmp_l("__ws_ret")

    # ---- socket(af, type, proto) ----------------------------------------
    a.label("__ws_socket")
    st(RSP, 0x28, RDI)                          # af
    st(RSP, 0x30, RSI)                          # type
    st(RSP, 0x38, RDX)                          # proto
    a.call("__ws_wsa_start")
    ld(RCX, RSP, 0x30)
    a.emit(mov_rr(RAX, RCX))
    movi32(R11, LINUX_SOCK_NONBLOCK)
    a.emit(alu_rr(0x21, RAX, R11))              # and rax, r11（非零 = 要设非阻塞）
    st(RSP, 0x40, RAX)
    movi32(RAX, 0xFFF7F7FF)                     # 清 SOCK_NONBLOCK|SOCK_CLOEXEC 两位
    a.emit(alu_rr(0x21, RCX, RAX))
    st(RSP, 0x30, RCX)
    ld(RDI, RSP, 0x28)
    cmp_edi(LINUX_AF_INET6)
    jcc_l("ne", "__ws_sk_af")
    movi32(RDI, WIN_AF_INET6)
    a.label("__ws_sk_af")
    ld(RDX, RSP, 0x38)
    a.emit(mov_rr(R8, RDX))                     # proto → 第 3 参
    ld(RDX, RSP, 0x30)
    a.emit(mov_rr(RCX, RDI))                    # af → 第 1 参
    api("socket")
    cmp_rax_m1()                                # INVALID_SOCKET 是 64 位的全 1
    jcc_l("e", "__ws_sock_err")
    a.call("__ws_sock_alloc")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    st(RSP, 0x48, RAX)                          # fd
    ld(R11, RSP, 0x40)
    test_rr(R11, R11)
    jcc_l("e", "__ws_sk_done")
    movi(R11, 1)
    st(RBX, WS_SOCKTMP, R11)                    # FIONBIO 的 u_long = 1
    ld(RCX, RDX, WS_FD_HANDLE)                  # rdx 还指着刚分配的表项
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    cmp_eax_m1()
    jcc_l("ne", "__ws_sk_done")
    a.call("__ws_wsa_err")                      # 设不上非阻塞就别留个"假非阻塞"的 socket
    st(RSP, 0x50, RAX)
    ld(RDI, RSP, 0x48)
    a.call("__ws_sock_drop")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")
    a.label("__ws_sk_done")
    ld(RAX, RSP, 0x48)
    jmp_l("__ws_ret")

    # ---- accept(43) / accept4(288) --------------------------------------
    a.label("__ws_accept")
    a.emit(b"\x45\x31\xD2")                     # xor r10d, r10d：accept 没有 flags
    jmp_l("__ws_ac_go")
    a.label("__ws_accept4")
    a.label("__ws_ac_go")
    st(RSP, 0x40, R10)                          # flags（accept4）
    st(RSP, 0x30, RSI)                          # addr（可为 0）
    st(RSP, 0x38, RDX)                          # addrlen 指针
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    a.emit(mov_rr(RCX, RAX))
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    api("accept")
    cmp_rax_m1()
    jcc_l("e", "__ws_sock_err")
    st(RSP, 0x48, RAX)                          # 新 SOCKET
    ld(RDI, RSP, 0x30)
    a.call("__ws_addr_out")
    ld(RAX, RSP, 0x48)
    a.call("__ws_sock_alloc")
    a.emit(b"\x85\xC0")
    jcc_l("s", "__ws_fail")
    st(RSP, 0x50, RAX)                          # fd
    ld(R11, RSP, 0x40)
    movi32(RCX, LINUX_SOCK_NONBLOCK)
    a.emit(alu_rr(0x85, R11, RCX))              # test r11, rcx（只用来跳）
    jcc_l("e", "__ws_ac_done")
    movi(R11, 1)
    st(RBX, WS_SOCKTMP, R11)
    ld(RCX, RDX, WS_FD_HANDLE)
    movi32(RDX, WIN_FIONBIO)
    movi(R8, PE_STATE_VA + WS_SOCKTMP)
    api("ioctlsocket")
    cmp_eax_m1()
    jcc_l("ne", "__ws_ac_done")
    a.call("__ws_wsa_err")
    st(RSP, 0x58, RAX)
    ld(RDI, RSP, 0x50)
    a.call("__ws_sock_drop")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_ac_done")
    ld(RAX, RSP, 0x50)
    jmp_l("__ws_ret")

    # ---- connect / bind / listen / shutdown / getsockname / getpeername --
    a.label("__ws_connect")
    case_addr2("connect")
    a.label("__ws_bind")
    case_addr2("bind")
    a.label("__ws_listen")
    case_fd1("listen")
    a.label("__ws_shutdown")
    case_fd1("shutdown")
    a.label("__ws_getsockname")
    case_peername("getsockname")
    a.label("__ws_getpeername")
    case_peername("getpeername")
    a.label("__ws_sendto")
    case_sendrecv("sendto", "send", True, "sd")
    a.label("__ws_recvfrom")
    case_sendrecv("recvfrom", "recv", False, "rf")

    # ---- setsockopt(fd, level, optname, optval, optlen) ------------------
    # Linux 的 level/optname 与 `SO_*` 号都要翻；两个 TIMEO 还要把 timeval 折成毫秒。
    a.label("__ws_setsockopt")
    st(RSP, 0x30, RSI)                          # level
    st(RSP, 0x38, RDX)                          # optname
    st(RSP, 0x40, R10)                          # optval
    st(RSP, 0x48, R8)                           # optlen
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    st(RSP, 0x58, RAX)                          # SOCKET
    ld(RCX, RSP, 0x30)
    cmp_ecx(1)                                  # SOL_SOCKET
    jcc_l("ne", "__ws_sso_go")                  # 别的 level 两边同值 ⇒ 直通
    movi(RCX, WS2_SOL_SOCKET)
    st(RSP, 0x30, RCX)
    ld(RDX, RSP, 0x38)
    for _opt in sorted(WS2_SO):
        cmp_edx(_opt)
        jcc_l("e", f"__ws_sso_{_opt}")
    jmp_l("__ws_sso_bad")
    for _opt in sorted(WS2_SO):
        a.label(f"__ws_sso_{_opt}")
        movi(RCX, WS2_SO[_opt])
        st(RSP, 0x38, RCX)
        if _opt in (21, 20):                    # SO_SNDTIMEO / SO_RCVTIMEO
            # ms = sec*1000 + usec/1000（`imul_ri` 只吃 imm8，乘 1000 得用 imul_rr/div）
            ld(RAX, RSP, 0x40)
            ld(RAX, RAX, 0)
            movi(RCX, 1000)
            a.emit(imul_rr(RAX, RCX))           # sec * 1000
            st(RSP, 0x50, RAX)
            ld(RAX, RSP, 0x40)
            ld(RAX, RAX, 8)                     # usec
            a.emit(b"\x31\xD2")                 # xor edx, edx
            movi(RCX, 1000)
            a.emit(group3(6, RCX))              # div rcx → rax = usec/1000
            ld(R11, RSP, 0x50)
            add_rr(RAX, R11)
            st(RBX, WS_SOCKTMP, RAX)            # 低 32 位就是毫秒
            movi(RCX, PE_STATE_VA + WS_SOCKTMP)
            st(RSP, 0x40, RCX)
            movi(RCX, 4)
            st(RSP, 0x48, RCX)
        jmp_l("__ws_sso_go")
    a.label("__ws_sso_bad")
    movi(RAX, -95)                              # EOPNOTSUPP：没映射的 SO_*
    jmp_l("__ws_ret")
    a.label("__ws_sso_go")
    ld(RCX, RSP, 0x58)
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    ld(R9, RSP, 0x40)
    ld(RAX, RSP, 0x48)
    st(RSP, 0x20, RAX)                          # 第 5 参 = optlen
    api("setsockopt")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    xor_eax()
    jmp_l("__ws_ret")

    # ---- getsockopt(fd, level, optname, optval, optlen 指针) -------------
    a.label("__ws_getsockopt")
    st(RSP, 0x30, RSI)
    st(RSP, 0x38, RDX)
    st(RSP, 0x40, R10)
    st(RSP, 0x48, R8)
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_not_sock")
    st(RSP, 0x58, RAX)
    ld(RCX, RSP, 0x30)
    cmp_ecx(1)
    jcc_l("ne", "__ws_gso_go")
    movi(RCX, WS2_SOL_SOCKET)
    st(RSP, 0x30, RCX)
    ld(RDX, RSP, 0x38)
    for _opt in sorted(WS2_SO):
        cmp_edx(_opt)
        jcc_l("e", f"__ws_gso_{_opt}")
    jmp_l("__ws_gso_bad")
    for _opt in sorted(WS2_SO):
        a.label(f"__ws_gso_{_opt}")
        movi(RCX, WS2_SO[_opt])
        st(RSP, 0x38, RCX)
        jmp_l("__ws_gso_go")
    a.label("__ws_gso_bad")
    movi(RAX, -95)
    jmp_l("__ws_ret")
    a.label("__ws_gso_go")
    ld(RCX, RSP, 0x58)
    ld(RDX, RSP, 0x30)
    ld(R8, RSP, 0x38)
    ld(R9, RSP, 0x40)
    ld(RAX, RSP, 0x48)                          # optlen 指针（调用方的，Windows 会回写）
    st(RSP, 0x20, RAX)
    api("getsockopt")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    ld(RCX, RSP, 0x38)
    cmp_ecx(WS2_SO_RCVTIMEO)
    jcc_l("e", "__ws_gso_time")
    cmp_ecx(WS2_SO_SNDTIMEO)
    jcc_l("e", "__ws_gso_time")
    xor_eax()
    jmp_l("__ws_ret")
    a.label("__ws_gso_time")                    # 毫秒 → timeval（Windows 还把 *optlen 改成 4）
    ld(RAX, RSP, 0x40)
    ld32(RAX, RAX, 0)                           # ms（DWORD）
    a.emit(b"\x31\xD2")
    movi(RCX, 1000)
    a.emit(group3(6, RCX))                      # div rcx → rax = sec，rdx = ms % 1000
    movi(RCX, 1000)
    a.emit(imul_rr(RDX, RCX))                   # usec = (ms%1000) * 1000
    ld(RCX, RSP, 0x40)
    st(RCX, 0, RAX)                             # timeval.tv_sec
    st(RCX, 8, RDX)                             # timeval.tv_usec
    ld(RCX, RSP, 0x48)
    movi(RDX, 16)
    st32(RCX, 0, RDX)                           # *optlen = sizeof(struct timeval)
    xor_eax()
    jmp_l("__ws_ret")

    # ---- poll(7)：Linux 的 pollfd 数组 → WinSock 的 select --------------------
    #
    # 为什么架在 `select` 上而不是 `WSAPoll`：WSAPoll 在 Windows 上有名的问题
    # （连接失败时 POLLOUT 照样报、POLLHUP 不可靠），而 `select` 是 WinSock 里最老最稳的一个。
    #
    # **两处点名偏差**（也写进 `docs/221`）：
    #   * 只认 **socket 型 fd**：文件 fd（0/1/2 等）在 PE 上报 `POLLNVAL`，
    #     而 Linux 会给它们 `POLLIN`/`POLLOUT`；
    #   * 三个集合全空时**不睡**（WinSock 的 select 拿空集合直接 `WSAEINVAL`）。
    #
    # 事件位两边**不一样**（Linux `POLLIN`=1 / `POLLOUT`=4；Windows `POLLRDNORM`=0x100 /
    # `POLLWRNORM`=0x10），所以进出各映射一次；`POLLNVAL` 这一档 Windows 没有，由这里补。

    a.label("__ws_set_init")                    # rdi = setptr
    xor_eax()
    st32(RDI, 0, RAX)                           # fd_count = 0
    ret_()

    a.label("__ws_set_add")                     # rdi = setptr, rsi = handle → rax = 1 / 0(满)
    ld32(RCX, RDI, 0)
    cmp_ecx(WS_FD_COUNT)                        # WinSock 的 FD_SETSIZE = 64
    jcc_l("ae", "__ws_sadd_full")
    a.emit(mov_rr(RAX, RCX))
    imul_ri(RAX, 8)
    add_rr(RAX, RDI)
    st(RAX, 8, RSI)                             # fd_array[count] = handle
                                                # ⚠ 偏移是 **8** 不是 4：Win64 上
                                                # `SOCKET` 是 8 字节，`u_int fd_count` 后面
                                                # 要按 8 对齐 —— 写进 4 就落进填充区，
                                                # select 拿到的是垃圾句柄（症状：它报错）
    add_ri(RCX, 1)
    st32(RDI, 0, RCX)
    movi(RAX, 1)
    ret_()
    a.label("__ws_sadd_full")
    xor_eax()
    ret_()

    a.label("__ws_set_has")                     # rdi = setptr, rsi = handle → rax = 1 / 0
    ld32(RCX, RDI, 0)
    a.emit(b"\x31\xD2")                         # xor edx, edx
    a.label("__ws_shas_loop")
    a.emit(alu_rr(0x39, RDX, RCX))              # cmp rdx, rcx
    jcc_l("ae", "__ws_shas_no")
    a.emit(mov_rr(RAX, RDX))
    imul_ri(RAX, 8)
    add_rr(RAX, RDI)
    ld(RAX, RAX, 8)                             # fd_array 在偏移 8（见 __ws_set_add）
    a.emit(alu_rr(0x39, RAX, RSI))              # cmp rax, rsi
    jcc_l("e", "__ws_shas_yes")
    add_ri(RDX, 1)
    jmp_l("__ws_shas_loop")
    a.label("__ws_shas_no")
    xor_eax()
    ret_()
    a.label("__ws_shas_yes")
    movi(RAX, 1)
    ret_()

    a.label("__ws_poll")
    st(RSP, 0x28, RDI)                          # fds
    st(RSP, 0x30, RSI)                          # nfds
    st(RSP, 0x38, RDX)                          # timeout（毫秒；< 0 = 无限等）
    a.emit(mov_rr(RAX, RSI))
    cmp_eax(WS_FD_COUNT)                        # 我们只认自己那张 64 格的 fd 表
    jcc_l("a", "__ws_poll_einval")
    a.call("__ws_wsa_start")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    a.call("__ws_set_init")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    a.call("__ws_set_init")
    movi(RDI, PE_STATE_VA + WS_FDSETS + 2 * WS_FDSET_CAP)
    a.call("__ws_set_init")
    # ---- 第一趟：清 revents、把要等的 handle 塞进集合 ----
    xor_eax()
    st(RSP, 0x40, RAX)                          # i = 0
    a.label("__ws_poll_1l")
    ld(RAX, RSP, 0x40)
    ld(RCX, RSP, 0x30)
    a.emit(alu_rr(0x39, RAX, RCX))              # cmp rax, rcx
    jcc_l("ae", "__ws_poll_1d")
    imul_ri(RAX, 8)
    ld(RCX, RSP, 0x28)
    add_rr(RAX, RCX)                            # rax = &pollfd[i]
    st(RSP, 0x48, RAX)
    ld32(RCX, RAX, 0)                           # fd
    movi32(RDX, 0)
    st16(RAX, 6, RDX)                           # revents = 0
    cmp_ecx(0)
    jcc_l("l", "__ws_poll_1n")                  # 负 fd：Linux 跳过它
    a.emit(mov_rr(RDI, RCX))                    # edi = fd
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("ne", "__ws_poll_have")
    ld(RAX, RSP, 0x48)
    movi32(RCX, LINUX_POLLNVAL)
    st16(RAX, 6, RCX)                           # 不是 socket ⇒ POLLNVAL
    jmp_l("__ws_poll_1n")
    a.label("__ws_poll_have")
    st(RSP, 0x50, RAX)                          # SOCKET
    ld(RDX, RSP, 0x48)
    ld16z(RCX, RDX, 4)                          # events
    movi32(RAX, LINUX_POLLIN)
    a.emit(alu_rr(0x85, RCX, RAX))              # test rcx, rax
    jcc_l("e", "__ws_poll_ev_w")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_poll_einval")              # 集合满了：报错，不静默丢
    a.label("__ws_poll_ev_w")
    ld(RDX, RSP, 0x48)
    ld16z(RCX, RDX, 4)
    movi32(RAX, LINUX_POLLOUT)
    a.emit(alu_rr(0x85, RCX, RAX))              # test rcx, rax
    jcc_l("e", "__ws_poll_1n")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_add")
    test_rax()
    jcc_l("e", "__ws_poll_einval")
    a.label("__ws_poll_1n")
    ld(RAX, RSP, 0x40)
    add_ri(RAX, 1)
    st(RSP, 0x40, RAX)
    jmp_l("__ws_poll_1l")
    # ---- 等 ----
    a.label("__ws_poll_1d")
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld32(RCX, RDI, 0)
    movi(RDX, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld32(RAX, RDX, 0)
    add_rr(RCX, RAX)
    test_ecx()
    jcc_l("e", "__ws_poll_2")                   # 全空 ⇒ 不睡（见上面的偏差说明）
    ld32(RAX, RSP, 0x38)
    cmp_eax(0)
    jcc_l("l", "__ws_poll_inf")
    a.emit(b"\x31\xD2")                         # xor edx, edx
    movi(RCX, 1000)
    a.emit(group3(6, RCX))                      # div rcx → rax = 秒，rdx = 毫秒余数
    movi(RCX, 1000)
    a.emit(imul_rr(RDX, RCX))                   # 微秒 = (ms % 1000) * 1000
    movi(RDI, PE_STATE_VA + WS_POLLTV)
    st32(RDI, 0, RAX)
    st32(RDI, 4, RDX)
    st(RSP, 0x20, RDI)
    jmp_l("__ws_poll_go")
    a.label("__ws_poll_inf")
    movi(RDX, 0)
    st(RSP, 0x20, RDX)                          # 第五参 NULL ⇒ 无限等
    a.label("__ws_poll_go")
    movi32(RCX, 0)                              # WinSock 忽略 nfds
    movi(RDX, PE_STATE_VA + WS_FDSETS)
    movi(R8, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    movi(R9, 0)                                 # exceptfds = NULL
    api("select")
    cmp_eax_m1()
    jcc_l("e", "__ws_sock_err")
    # ---- 第二趟：把就绪映射回 revents，数几个非零 ----
    a.label("__ws_poll_2")
    xor_eax()
    st(RSP, 0x40, RAX)                          # i
    st(RSP, 0x58, RAX)                          # n = 0
    a.label("__ws_poll_2l")
    ld(RAX, RSP, 0x40)
    ld(RCX, RSP, 0x30)
    a.emit(alu_rr(0x39, RAX, RCX))
    jcc_l("ae", "__ws_poll_2d")
    imul_ri(RAX, 8)
    ld(RCX, RSP, 0x28)
    add_rr(RAX, RCX)
    st(RSP, 0x48, RAX)
    ld32(RCX, RAX, 0)
    cmp_ecx(0)
    jcc_l("l", "__ws_poll_2n")
    a.emit(mov_rr(RDI, RCX))
    a.call("__ws_sock_of_fd")
    test_rax()
    jcc_l("e", "__ws_poll_2n")                  # 非 socket（POLLNVAL 已经写好）
    st(RSP, 0x50, RAX)
    ld(RDX, RSP, 0x48)
    ld16z(RCX, RDX, 6)
    st(RSP, 0x60, RCX)                          # rev = 上一趟留下的 revents
    movi(RDI, PE_STATE_VA + WS_FDSETS)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_poll_2w")
    ld(RAX, RSP, 0x60)
    movi32(RCX, LINUX_POLLIN)
    add_rr(RAX, RCX)                            # 位不相交 ⇒ 加法就是或
    st(RSP, 0x60, RAX)
    a.label("__ws_poll_2w")
    movi(RDI, PE_STATE_VA + WS_FDSETS + WS_FDSET_CAP)
    ld(RSI, RSP, 0x50)
    a.call("__ws_set_has")
    test_rax()
    jcc_l("e", "__ws_poll_2s")
    ld(RAX, RSP, 0x60)
    movi32(RCX, LINUX_POLLOUT)
    add_rr(RAX, RCX)
    st(RSP, 0x60, RAX)
    a.label("__ws_poll_2s")
    ld(RDX, RSP, 0x48)
    ld(RAX, RSP, 0x60)
    st16(RDX, 6, RAX)
    test_rax()
    jcc_l("e", "__ws_poll_2n")
    ld(RAX, RSP, 0x58)
    add_ri(RAX, 1)
    st(RSP, 0x58, RAX)
    a.label("__ws_poll_2n")
    ld(RAX, RSP, 0x40)
    add_ri(RAX, 1)
    st(RSP, 0x40, RAX)
    jmp_l("__ws_poll_2l")
    a.label("__ws_poll_2d")
    ld(RAX, RSP, 0x58)
    jmp_l("__ws_ret")
    a.label("__ws_poll_einval")
    movi(RAX, -22)                              # EINVAL
    jmp_l("__ws_ret")

    # （cmdline 字面量在 .idata 的固定偏移 PE_LIT_OFF 上，shim 用常量取址，不必内嵌）


class PeEmitter(Emitter):
    """把 syscall 改成走 shim、把入口桩改成 Windows 形态，其余降级与 ELF 目标共用。"""

    def _emit_syscall(self) -> None:
        self.asm.call("__win_syscall")

    def emit_entry_stub(self, start_name: str) -> None:
        a = self.asm
        a.label("__entry")
        a.emit(b"\x48\x83\xEC\x28")                # sub rsp, 0x28
        a.call(start_name)
        a.emit(mov_ri32(RAX, SYS_EXIT))            # exit(0) 也走 shim，保持单一路径
        a.emit(mov_ri32(RDI, 0))
        a.call("__win_syscall")
        a.emit(b"\x0F\x0B")


def build_pe(text: bytes, data: bytes, entry_rva: int, idata: bytes) -> bytes:
    """静态 PE32+（console, x86-64）。四个节都钉在固定 RVA：.text / .idata / .data / .state。"""
    nsec = 3
    hdr = 0x40 + 4 + 20 + 240 + 40 * nsec
    toff = align_up(hdr, PE_FILE_ALIGN)                      # .text 的 raw
    ioff = toff + align_up(len(text), PE_FILE_ALIGN)         # .idata 的 raw
    doff = ioff + align_up(len(idata), PE_FILE_ALIGN)        # .data 的 raw
    image_size = align_up(PE_STATE_RVA + WS_SIZE, PE_SEC_ALIGN)

    o = bytearray()
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)
    o += dos
    o += b"PE\x00\x00"
    o += struct.pack("<HHIIIHH", 0x8664, nsec, 0, 0, 0, 240, 0x22)
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    opt[2] = 14
    struct.pack_into("<I", opt, 16, entry_rva)
    struct.pack_into("<I", opt, 20, PE_TEXT_RVA)             # BaseOfCode
    struct.pack_into("<Q", opt, 24, PE_IMAGE_BASE)
    struct.pack_into("<I", opt, 32, PE_SEC_ALIGN)
    struct.pack_into("<I", opt, 36, PE_FILE_ALIGN)
    struct.pack_into("<HHHHHH", opt, 40, 6, 0, 0, 0, 6, 0)
    # PE32+ 的 SizeOfImage / SizeOfHeaders 在 56 / 60；写成 PE32 的 54 / 58 会把值落进
    # Win32VersionValue 槽，加载器读到 SizeOfHeaders=0 直接拒收（"不是有效的 Win32 应用程序"）。
    struct.pack_into("<I", opt, 56, image_size)
    struct.pack_into("<I", opt, 60, toff)
    struct.pack_into("<H", opt, 68, 3)                       # Subsystem: console
    # 栈留 16 MB：编译器自己的递归下降比小工具深得多（Linux 那侧默认 8 MB，本来就够宽）。
    struct.pack_into("<Q", opt, 72, 16 * 1024 * 1024)
    struct.pack_into("<Q", opt, 80, 0x100000)
    struct.pack_into("<Q", opt, 88, 0x100000)
    struct.pack_into("<Q", opt, 96, 0x1000)
    struct.pack_into("<I", opt, 108, 16)
    struct.pack_into("<II", opt, 112 + 8, PE_IDATA_RVA, PE_IMPORT_DESC_BYTES)  # Import Directory
    o += opt

    def sec(name, vs, rva, rs, rp, ch):
        h = bytearray(40)
        h[0:len(name)] = name
        struct.pack_into("<I", h, 8, vs)
        struct.pack_into("<I", h, 12, rva)
        struct.pack_into("<I", h, 16, rs)
        struct.pack_into("<I", h, 20, rp)
        struct.pack_into("<I", h, 36, ch)
        return bytes(h)

    # 每节的 VirtualSize 一直铺到下一节的起点：加载器**不接受节间有空洞**（实测：只要
    # .text 与 .idata 之间留 0x1000 的空隙就直接 "不是有效的 Win32 应用程序"）。
    # 铺满既消掉空洞，又保住了各节的固定 RVA。
    o += sec(b".text", PE_IDATA_RVA - PE_TEXT_RVA, PE_TEXT_RVA,
             align_up(len(text), PE_FILE_ALIGN), toff, 0x60000020)
    o += sec(b".idata", PE_DATA_RVA - PE_IDATA_RVA, PE_IDATA_RVA,
             align_up(len(idata), PE_FILE_ALIGN), ioff, 0xC0000040)
    o += sec(b".data", PE_STATE_RVA - PE_DATA_RVA + WS_SIZE, PE_DATA_RVA,
             align_up(len(data), PE_FILE_ALIGN), doff, 0xC0000040)
    o += b"\x00" * (toff - len(o)) + text
    o += b"\x00" * (ioff - len(o)) + idata
    # 最后一节也必须补齐到声明的 SizeOfRawData，否则加载器读到 EOF 之外直接拒收
    o += b"\x00" * (doff - len(o)) + bytes(data)
    o += b"\x00" * (align_up(len(data), PE_FILE_ALIGN) - len(data))
    return bytes(o)


def _pe_layout_globals(globals_: list[Global], data_rva: int) -> tuple[bytearray, int]:
    """把全局布到 data/bss。内容与 data_rva 无关，只有地址跟着变。"""
    DATA_VADDR = PE_IMAGE_BASE + data_rva          # noqa: N806 (与 ELF 目标同名同义)
    data, bss = bytearray(), 0
    for g in globals_:
        if g.data != b"\0" * size_of(g.ty):
            while len(data) % 8:
                data.append(0)
            g.addr = DATA_VADDR + len(data)
            data += g.data
    while len(data) % 8:
        data.append(0)
    for g in globals_:
        sz = size_of(g.ty)
        if g.data == b"\0" * sz:
            bss = align_up(bss, 8)
            g.addr = DATA_VADDR + len(data) + bss
            bss += align_up(sz, 8)
    return data, bss


def dump_win_shim(path) -> int:
    """把 `__win_syscall` 的机器码**冻结**成一段 blob（自举镜像照抄这一份）。

    四个节的 RVA 与 IAT 槽都是编译期常量，所以这段 blob 与布局**完全无关** ——
    里面一个待回填的地址都没有，镜像把它原样摆在 .text 里即可。
    判据：镜像产出的 PE 与参考实现产出的 PE 行为一致（`tools/loment_pe_test.py`）。
    """
    a = Asm(0)
    em = PeEmitter([])
    em.asm = a
    _idata, slots = build_pe_idata()
    emit_win_shim(em, slots)
    blob = a.finalize()
    Path(path).write_bytes(blob)
    # 再生成自举镜像用的**自包含**数据模块（hex 内嵌）：镜像是独立二进制，
    # 不该依赖运行时去读仓库里的文件。
    idata, _slots = build_pe_idata()

    def hex_parts(data):
        h = data.hex()
        return [h[i:i + 400] for i in range(0, len(h), 400)]

    def part_fn(name, parts):
        out = [f"pub fn {name}(i: u32) -> str {{"]
        out += [f'    if i == {k} {{ return "{p}"; }}' for k, p in enumerate(parts)]
        out += ['    return "";', "}", ""]
        return out

    sparts, iparts = hex_parts(blob), hex_parts(idata)
    src = [
        "// win_shim_data.lomt — 由 `tools/lomelf.py --dump-win-shim` 生成，别手改。",
        "//",
        f"// 两段**与布局无关**的数据：`__win_syscall` 的机器码（{len(blob)} 字节）与",
        f"// 导入表（{len(idata)} 字节，kernel32 + ws2_32，含 /proc/self/cmdline 字面量）。四个节钉在固定",
        "// RVA、IAT 槽是常量，所以它们里面一个待回填的地址都没有 —— 自举镜像原样搬即可。",
        "//",
        "// **故意按片给、不做 str_concat**：运行时的 bump 堆只有 64 KiB（lomentc.py 的",
        "// alloc_ir，超了直接 abort = ud2 = SIGILL），拼一个 8 KB 的 hex 串就会撞顶。",
        "// 调用方逐片解、逐片写进输出即可。",
        "",
        "module win_shim_data",
        "",
        f"pub fn n_shim() -> u32 {{ return {len(sparts)}; }}",
        "",
    ]
    src += part_fn("shim_part", sparts)
    src += [f"pub fn n_idata() -> u32 {{ return {len(iparts)}; }}", ""]
    src += part_fn("idata_part", iparts)
    shim_src = Path(__file__).resolve().parent.parent / "loment" / "tools" / "win_shim_data.lomt"
    shim_src.write_text("\n".join(src), encoding="utf-8", newline="\n")
    print(f"[OK] {path} ({len(blob)} B) + {shim_src.name}")
    return 0


def _pe_emit(funcs, globals_, slots) -> tuple[bytes, int]:
    em = PeEmitter(globals_)
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    em.emit_entry_stub("_start")
    emit_win_shim(em, slots)
    for f in funcs:
        em.emit_func(f)
    return em.asm.finalize(), em.asm.labels["__entry"]


def compile_pe(text: str) -> tuple[bytes, dict]:
    global TEXT_VADDR
    globals_, funcs, _externs = parse_ll(text)
    if not any(f.name == "_start" for f in funcs):
        raise Unsupported("没有 _start 入口（PE 产物需要一个用户态入口）")

    # 四个节的 RVA 全是常量，所以**不需要不动点**：发射一遍就完事。
    # （早先按"代码长度决定 .data 的 RVA"排过一次，那是为了迁就 .text 只能钉在最低 RVA；
    #   现在每节都钉死了，两遍发射连同它引发的节区重叠问题一起消失。）
    TEXT_VADDR = PE_IMAGE_BASE + PE_TEXT_RVA
    data, bss = _pe_layout_globals(globals_, PE_DATA_RVA)
    idata, slots = build_pe_idata()
    code, entry = _pe_emit(funcs, globals_, slots)

    blob = build_pe(code, bytes(data), entry - PE_IMAGE_BASE, idata)
    return blob, {"text": len(code), "data": len(data), "bss": bss,
                  "entry": entry - PE_IMAGE_BASE, "funcs": [f.name for f in funcs]}


# ------------------------------------------------------------------ 驱动


#: 我们实现的重定位类型 (docs/173 阶段 2)。值就是 ELF 的 `R_X86_64_*` 编号。
REL_64 = 1        # S + A
REL_PC32 = 2      # S + A - P
REL_PLT32 = 4     # 同 PC32 —— 静态链接里没有 PLT, 两者等价 (GNU ld 也这么处理)
REL_32 = 10       # (S + A) 的低 32 位, 无符号
REL_32S = 11      # 同上, 有符号 (x86-64 上 addrmode 的绝对值走这条)
REL_PC64 = 24     # S + A - P, 64 位
SUPPORTED_RELOCS = (REL_64, REL_PC32, REL_PLT32, REL_32, REL_32S, REL_PC64)

#: 我们**不摆**、丢掉也无害的节: 展开信息与调试信息。代码引用不到它们, 所以针对它们的重定位
#: 是空转 —— **跳过而不是报错**。
#:
#: **为什么只放这几个**: `.data`/`.rodata` 也"我们不摆", 但丢掉它们**不是无害的** —— 代码
#: 里的字符串常量就住在 `.rodata`, 丢了会让程序读到垃圾 (静默错)。所以只有"代码引用不到"的
#: 元数据节能进这张名单, 其余一律仍按"指向非代码节"硬拒。
#: 实测来源: Zig 的 `-OReleaseSmall` 对象只剩一条 `.rela.eh_frame`, 不放行它整个语言就用不了。
_META_SEC = (".eh_frame", ".debug", ".comment", ".note")


class ForeignObject:
    """一个外部 ELF64 可重定位目标文件里我们真正要用的那点东西 (docs/173 阶段 1/2)。

    **取什么**: `.text` 的字节 + 节在其中的偏移 + 它导出的全局符号 (名字 -> 段内偏移)
    + **重定位表**。三条边界仍然**硬失败**而不是猜:

    * 重定位类型不认识 → 报错 (猜的后果是"跳到错地址", 那是运行期崩溃而不是编译期报错);
    * 重定位指向**非代码节** (`.data`/`.rodata`) → 报错 —— 本档只摆代码节;
    * 一个符号**有重定位引用它、但到链接结束都没人定义** → 报错 (第 2 阶段仍不链 libc)。

    **未定义符号本身不再是错误**: 它可能由**另一个对象**提供 —— 这正是多目标文件 C 库的
    形状 (`a.o` 调 `b.o`)。判定推迟到"所有对象都摆好、符号表建好之后"。
    """

    def __init__(self, path: Path):
        self._load(path, path.name, path.read_bytes())

    @classmethod
    def from_bytes(cls, path: Path, name: str, raw: bytes) -> "ForeignObject":
        """从**内存里的字节**构造 —— 归档成员没有自己的文件 (`docs/173` 阶段 2)。"""
        self = cls.__new__(cls)
        self._load(path, name, raw)
        return self

    def _load(self, path: Path, name: str, raw: bytes) -> None:
        self.path = path
        self.name = name
        if raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
            raise Unsupported(f"{name}: 不是 ELF64 小端目标文件")
        if struct.unpack_from("<H", raw, 16)[0] != 1:      # e_type != ET_REL
            raise Unsupported(f"{name}: 不是可重定位目标文件 (ET_REL)")
        e_shoff, = struct.unpack_from("<Q", raw, 40)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", raw, 58)
        secs = []
        for k in range(e_shnum):
            o = e_shoff + k * e_shentsize
            nameoff, typ, flags, addr, off, size, link, info, align, entsize = \
                struct.unpack_from("<IIQQQQIIQQ", raw, o)
            secs.append({"nameoff": nameoff, "type": typ, "off": off, "size": size,
                         "link": link, "info": info, "entsize": entsize, "name": ""})
        shstr = secs[e_shstrndx]
        for s in secs:
            end = raw.index(b"\x00", shstr["off"] + s["nameoff"])
            s["name"] = raw[shstr["off"] + s["nameoff"]:end].decode("utf-8", "replace")
        # 代码节: `.text` **与 `.text.*`**。
        # **为什么必须认 `.text.*`**: clang/gcc 默认把所有函数塞进一个 `.text`, 但
        # rustc/LLVM 默认**按函数分节** (每个函数一个 `.text.<名字>`), 此时 `.text` 本身
        # 是**空的** (size 0) —— 只认 `.text` 会直接报"没有 .text" (实测 Rust 那条判据就是
        # 这么红的)。所以把所有代码节**按节表顺序拼成一块**, 并记下每节在拼接结果里的偏移,
        # 符号地址 = 该节偏移 + 符号的段内值 (16 对齐, 与函数对齐要求一致)。
        code: list[dict] = []
        buf = bytearray()
        for s in secs:
            if s["type"] == 1 and (s["name"] == ".text" or s["name"].startswith(".text.")):
                while len(buf) % 16:
                    buf.append(0)
                s["at"] = len(buf)
                buf += raw[s["off"]:s["off"] + s["size"]]
                code.append(s)
        if not code:
            raise Unsupported(f"{name}: 没有代码节 (.text / .text.*)")
        self.text = bytes(buf)
        self.sec_at = {k: s["at"] for k, s in enumerate(secs) if "at" in s}
        # 符号: 定义在代码节里的全局/弱符号进 `syms`; 未定义的只记名 (`undefined`)
        syms: dict[str, int] = {}
        undefined: list[str] = []
        #: (symtab 节号, 符号下标) -> 名字 —— 重定位按**下标**引用符号, 靠它回查名字
        name_of: dict[tuple[int, int], str] = {}
        for si, s in enumerate(secs):
            if s["type"] != 2:                             # SHT_SYMTAB
                continue
            strt = secs[s["link"]]
            for k in range(s["size"] // 24):
                o = s["off"] + k * 24
                nameoff, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", raw, o)
                if nameoff == 0:
                    continue
                end = raw.index(b"\x00", strt["off"] + nameoff)
                nm = raw[strt["off"] + nameoff:end].decode("utf-8", "replace")
                name_of[(si, k)] = nm
                # **`st_info` 的高 4 位是绑定, 低 4 位是类型** —— `ELF64_ST_INFO(bind, type)`
                # 就是 `(bind << 4) | type`。原文这里是 `info & 0x0F`, 取到的是**类型**:
                # 收定义时靠"类型是 FUNC/OBJECT"误打误撞能对, 但一旦要判绑定 (比如"未定义
                # 的符号是不是全局的") 就整个失效 —— 实测它让归档的固定点选不出成员
                # (`c_sub` 的 info=0x10, `&0x0F` 得 0 被当成 LOCAL)。
                bind = info >> 4
                if shndx == 0:
                    if bind in (1, 2):                     # GLOBAL / WEAK
                        undefined.append(nm)
                # **shndx 可以是保留值** (SHN_ABS=0xfff1 / SHN_COMMON=0xfff2 / SHN_XINDEX=0xffff)
                # —— 它们比节表长度大, 直接拿去索引会 IndexError (实测第一版就崩在这)。
                elif shndx < len(secs) and bind in (1, 2):  # GLOBAL / WEAK
                    tgt = secs[shndx]
                    if "at" in tgt:                              # 落在某个代码节里
                        syms[nm] = tgt["at"] + value
        self.syms = syms
        self.undefined = sorted(set(undefined))
        # 重定位: (目标节号, 节内偏移, 类型, 目标符号名, 加数)。**只摆代码节** ——
        # 落在 `.data`/`.rodata` 上的一律拒, 因为本档不摆那些节, 静默算出来的地址是错的。
        self.relocs: list[tuple[int, int, int, str, int]] = []
        for s in secs:
            if s["type"] != 4 or s["size"] == 0:        # SHT_RELA
                continue
            tgt = s["info"]
            if tgt >= len(secs) or "at" not in secs[tgt]:
                where = secs[tgt]["name"] if tgt < len(secs) else f"#{tgt}"
                if tgt < len(secs) and secs[tgt]["name"].startswith(_META_SEC):
                    continue          # 展开/调试信息: 我们不摆, 丢掉无损 (见 _META_SEC)
                raise Unsupported(f"{name}: 重定位指向非代码节 ({where}) —— docs/173 §3")
            for k in range(s["size"] // 24):
                o = s["off"] + k * 24
                r_off, r_info, r_add = struct.unpack_from("<QQq", raw, o)
                ty, sym_i = r_info & 0xFFFFFFFF, r_info >> 32
                if ty not in SUPPORTED_RELOCS:
                    raise Unsupported(f"{name}: 不认识的重定位类型 {ty} (docs/173 §3)")
                nm = name_of.get((s["link"], sym_i))
                if nm is None:
                    raise Unsupported(f"{name}: 重定位引用了下标记号外的符号 {sym_i}")
                self.relocs.append((tgt, r_off, ty, nm, r_add))


class Archive:
    """一个 `ar` 归档 (`.a`): 静态库的**标准形状** (docs/173 阶段 2)。

    **只取需要的东西**: 经典固定点选择 —— 某成员**定义**了当前需要的符号就收它, 收完把它
    引用的未定义符号也加进需求, 重复到不再变化。**不整包收**: 整包收会把"某个成员用了
    printf"这种根本用不到的成员也拖进来, 于是**整个库被拒** —— 而真实库里几乎总有那么
    一两个成员带着无关的 libc 依赖。这个差别是"多目标文件 C 库"这条判据能过的原因。
    """

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        if raw[:8] != b"!<arch>\n":
            raise Unsupported(f"{path.name}: 不是 ar 归档 (缺 !<arch> 魔数)")
        members: list[tuple[str, bytes]] = []
        pos = 8
        while pos + 60 <= len(raw):
            hdr = raw[pos:pos + 60]
            if hdr[58:60] != b"`\n":                # 成员头以 "`\n" 收尾 (0x60 0x0A)
                raise Unsupported(f"{path.name}: 归档成员头损坏 (偏移 {pos})")
            name = hdr[0:16].decode("ascii", "replace").strip()
            size_s = hdr[48:58].decode("ascii", "replace").strip()
            try:
                size = int(size_s)
            except ValueError:
                raise Unsupported(f"{path.name}: 归档成员长度不是数字 ({size_s!r})")
            data = raw[pos + 60:pos + 60 + size]
            if len(data) != size:
                raise Unsupported(f"{path.name}: 归档成员被截断 ({name})")
            members.append((name, data))
            pos += 60 + size + (size & 1)          # 数据按偶数对齐, 奇数补一个填充字节
        if not members:
            raise Unsupported(f"{path.name}: 归档里没有成员")
        self.members = members

    def select(self, needed: set) -> "list[ForeignObject]":
        # 元数据成员 (`/` 符号索引、`//` 长名表、`/SYM64/`) 不是 ELF —— 按魔数滤掉最稳,
        # 因为它们的名字格式在各家 ar 实现里并不一致。
        pool = [(n, d) for n, d in self.members if d[:4] == b"\x7fELF"]
        got: list[ForeignObject] = []
        changed = True
        while changed:
            changed = False
            rest = []
            for name, data in pool:
                try:
                    fo = ForeignObject.from_bytes(self.path, name, data)
                except Unsupported:
                    continue        # 这个成员我们用不了: 放着; 真需要它的符号时后面会报到
                if set(fo.syms) & needed:
                    got.append(fo)
                    needed |= set(fo.undefined)
                    changed = True
                else:
                    rest.append((name, data))
            pool = rest
        return got


def load_foreign(path: Path):
    """按**内容**分流 (不看扩展名): `!<arch>\\n` 是归档, `\\x7fELF` 是目标文件。

    看内容而不是后缀: `.a` / `.o` / `.lib` / 无后缀在各家工具链里并不统一, 而两种格式的
    魔数都极短且不可能互串。
    """
    if path.read_bytes()[:8] == b"!<arch>\n":
        return Archive(path)
    return ForeignObject(path)


#: System V AMD64 整数实参寄存器 (我们**自己的**约定是实参走栈, 这一套只给 extern 调用点)。
C_ARG_REGS = (RDI, RSI, RDX, RCX, R8, R9)

#: **链接器今天真正发出的收编面**（`docs/219` §6 第 1 条）。`Emitter.emit_conscript`
#: 发的那几个名字 —— 与 `loment/tools/lomelf.lomt` 的 `emit_conscript` 同序同字节。
#:
#: ⚠ 它是 `tools/loment_ports.py` 的 `PROVIDE` 的**子集**，而这件事有一条判据钉着
#: （`loment_ports_test::test_the_linkers_conscript_surface_is_inside_the_rulers_provide_set`）：
#: 尺子说"可闭合"而链接器发不出来，那条产物就会在链接期报"未定义的符号"，
#: **与表上的话正好相反**。反过来不要求 —— 表里还有一批**尚未实现**的
#: （`calloc` / `realloc` / `strlen` / `__udivdi3` …），那是 S1d/S1e 的活。
#:
#: ⚠ `malloc` / `free` 多一条条件：**堆在才发** —— 它们是一层"换成 C ABI"的薄壳，
#: 底下调的是 `@__loment_alloc` / `@__loment_free`（`docs/219` §2.1）。没有堆就没有
#: "我们的堆"可指，发了会悬着一个未定义的调用。判据是**我们这份 IR 里有没有那两条定义**
#: （`funcs` 的名字），不是文本里出现过那个名字。
CONSCRIPT = ("memcpy", "memset", "memcmp", "malloc", "free")


def compile_ll(text: str, objects: list | None = None) -> tuple[bytes, dict]:
    objects = objects or []
    global TEXT_VADDR, DATA_VADDR
    TEXT_VADDR, DATA_VADDR = ELF_TEXT_VADDR, ELF_DATA_VADDR   # PE 目标会改这两个全局
    globals_, funcs, externs = parse_ll(text)
    # 归档展开 (docs/173 阶段 2): 先算出"当前需要的符号", 再让每个归档按固定点挑成员。
    # 需求有两个来源: ① 我们自己 `declare` 的外部符号; ② 直接给的 `.o` 里未定义的符号。
    # 两者都要, 因为真实库里常常是"我的 a.o 引用 ar 成员里的 b"。
    if any(isinstance(o, Archive) for o in objects):
        need = {x for x in externs if not x.startswith("llvm.")}
        direct = [o for o in objects if not isinstance(o, Archive)]
        for o in direct:
            need |= set(o.undefined)
        flat = list(direct)
        for a in objects:
            if isinstance(a, Archive):
                flat += a.select(need)
        objects = flat
    # 全局布局: bytes 在 data, 全零在 bss
    data = bytearray()
    bss = 0
    for g in globals_:  # 第一遍: 有内容的进 data
        if g.data != b"\0" * size_of(g.ty):
            while len(data) % 8:
                data.append(0)
            g.addr = DATA_VADDR + len(data)
            data += g.data
    while len(data) % 8:
        data.append(0)
    for g in globals_:  # 第二遍: 全零的进 bss（排在 data 之后, 否则会和 rodata 撞）
        sz = size_of(g.ty)
        if g.data == b"\0" * sz:
            bss = align_up(bss, 8)
            g.addr = DATA_VADDR + len(data) + bss
            bss += align_up(sz, 8)
    # **`llvm.*` 不是外部函数, 是内建**: 运行期块里有 `declare void @llvm.trap()`, 而 `_call`
    # 对它是**特判** (发 `ud2` 而不是真去 call)。放进 externs 会把它抢进 C ABI 那条路,
    # 于是 `call llvm.trap` 在 finalize 里报"未定义的标签" —— 实测把 loment_elf_test 从
    # 7/7 打到 4/7。所以这里滤掉 (除 llvm.* 之外没有别的内建 declare)。
    em = Emitter(globals_, {x for x in externs if not x.startswith("llvm.")})
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    if not any(f.name == "_start" for f in funcs):
        raise Unsupported("没有 _start 入口（原生产物 v0 需要一个用户态入口）")
    em.emit_entry_stub("_start")
    for f in funcs:
        em.emit_func(f)
    # **收编面**（`docs/219` §6）：链里带了外部对象，就把"可闭合"那几个名字提供出来，
    # 于是它们**不再是未定义符号** —— 从表上划掉。粒度是"有没有外部对象"，不是
    # "有没有人要它"：精确到后者要链接器先算出需求集，自举侧 `lomelf.lomt` 一次只驻留
    # 一个对象，那一格留到下一轮（`docs/219` §8b）。**测量不受影响** —— 那始终是尺子的活。
    if objects:
        em.emit_conscript({f.name for f in funcs})
    # 外部目标文件 (docs/173 阶段 1/2): 三步, **顺序不能换** ——
    #   ① **摆位置**: 每个对象的 `.text` 接在我们自己的代码之后 (16 对齐), 记下基址;
    #   ② **建符号表**: 把我们自己的标签与所有对象的导出符号合成一张表。**必须先全摆完**:
    #      `a.o` 调 `b.o` 时, `a.o` 的重定位指向一个只有 `b.o` 知道的地址;
    #   ③ **应用重定位**: 到这一步才能算 `S + A - P`。
    # 调用点照旧走现有的 `call`/fixup 机制 (唯一多出来的是"参数进寄存器", 见 `Emitter._call`)。
    bases: list[int] = []
    appended: list[str] = []
    for obj in objects:
        while len(em.asm.buf) % 16:
            em.asm.buf.append(0)
        bases.append(len(em.asm.buf))
        for nm, val in obj.syms.items():
            if nm in em.asm.labels:
                continue          # 我们 (或先摆的那个对象) 已经有它: 以先来的为准
            em.asm.labels[nm] = TEXT_VADDR + bases[-1] + val
            appended.append(nm)
        em.asm.buf += obj.text
    applied = 0
    for i, obj in enumerate(objects):
        base = TEXT_VADDR + bases[i]
        for sec, r_off, ty, nm, add in obj.relocs:
            target = em.asm.labels.get(nm)
            if target is None:
                # **到这一步才判"没人定义"**: 这是多目标文件 C 库与"引用 libc"的分界线 ——
                # 前者由另一个对象提供, 后者到链接结束都空着。
                raise Unsupported(
                    f"{obj.name}: 引用了未定义的符号 {nm} —— 到链接结束都没人提供它"
                    f" (第 2 阶段仍不链 libc, docs/173 §3)")
            site = bases[i] + obj.sec_at[sec] + r_off     # 写进缓冲的位置 (缓冲内偏移)
            here = base + obj.sec_at[sec] + r_off         # 公式里的 P (绝对地址)
            val = target + add                            # 公式里的 S + A
            if ty in (REL_PC32, REL_PLT32):
                rel = val - here
                if not -2 ** 31 <= rel < 2 ** 31:
                    raise Unsupported(f"{obj.name}: PC32 重定位溢出 ({nm})")
                struct.pack_into("<i", em.asm.buf, site, rel)
            elif ty == REL_PC64:
                struct.pack_into("<q", em.asm.buf, site, val - here)
            elif ty == REL_64:
                struct.pack_into("<Q", em.asm.buf, site, val & 0xFFFFFFFFFFFFFFFF)
            else:                                          # REL_32 / REL_32S
                struct.pack_into("<I", em.asm.buf, site, val & 0xFFFFFFFF)
            applied += 1
    text = em.asm.finalize()
    entry = em.asm.labels["__entry"]
    return build_elf(text, bytes(data), bss, entry), {
        "text": len(text), "data": len(data), "bss": bss, "entry": entry,
        "funcs": [f.name for f in funcs], "objects": [o.name for o in objects],
        "linked": appended, "relocs": applied,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--dump-win-shim" and len(argv) == 2:
        return dump_win_shim(argv[1])
    out, check, target = None, False, "elf"
    files: list[str] = []
    link: list[Path] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-o", "--out"):
            i += 1
            out = argv[i]
        elif a == "--target":
            i += 1
            target = argv[i]
        elif a == "--check":
            check = True
        elif a == "--link":
            i += 1
            link.append(Path(argv[i]))
        else:
            files.append(a)
        i += 1
    if len(files) != 1 or target not in ("elf", "pe"):
        print("用法: lomelf.py IN.ll [-o OUT] [--target elf|pe] [--check]"
              " [--link OBJ.o ...]", file=sys.stderr)
        return 2
    if link and target == "pe":
        print("[ERR] --link 目前只支持 ELF 目标 (docs/173 §3)", file=sys.stderr)
        return 2
    src = Path(files[0])
    try:
        objs = [load_foreign(p) for p in link]
        blob, info = (compile_pe if target == "pe" else compile_ll)(
            src.read_text(encoding="utf-8"), *(() if target == "pe" else (objs,)))
    except Unsupported as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    except ElfError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    if check:
        print(f"[OK] {src} 可编 (text {info['text']} B, data {info['data']} B, bss {info['bss']} B)")
        return 0
    if out is None:
        out = str(src.with_suffix(".exe" if target == "pe" else ""))
    Path(out).write_bytes(blob)
    import os
    os.chmod(out, 0o755)
    print(f"[OK] {src} -> {out} ({len(blob)} B, text {info['text']} B, data {info['data']} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
