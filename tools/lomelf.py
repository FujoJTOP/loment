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


def parse_ll(text: str) -> tuple[list[Global], list[Func]]:
    globals_, funcs, lines, i = [], [], text.split("\n"), 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line[0] in ";!":
            continue
        if line.startswith(("target ", "attributes ", "source_filename", "declare ", "module ")):
            continue
        if line.startswith("@"):
            globals_.append(_parse_global(line))
            continue
        if line.startswith("define "):
            fn, i = _parse_func(lines, i - 1)
            funcs.append(fn)
    return globals_, funcs


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
    def __init__(self, globals_: list[Global]):
        self.asm = Asm(TEXT_VADDR)
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
        """给 target 的每个 phi 写入来自**当前块**的入边值，返回 (真实标签, 跳过标签)。"""
        nxt = f"__ph{self.asm.here():x}"
        phis = self.phis.get(tgt)
        if not phis:
            return tgt, nxt
        for phi in phis:
            pty, entries = parse_phi(phi.text)
            for ival, pred in entries:
                if pred != self.cur_block:
                    continue
                if is_agg(pty):
                    ity, i2 = parse_type(ival)
                    self.agg_addr(ival[i2:].strip(), RSI)
                    self.asm.emit(lea(RDI, RBP, self.slots[phi.dest]))
                    self.copy(RDI, RSI, size_of(pty))
                else:
                    self.get(pty, ival, RAX)
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
        regmap = {"ax": RAX, "di": RDI, "si": RSI, "dx": RDX, "r10": R10, "r8": R8}
        if len(regs) != len(args):
            raise Unsupported(f"内联汇编约束与实参不匹配: {cons!r}")
        # 依赖寄存器顺序: rax 最后写 (syscall 号), 但其它寄存器不能互相踩
        for regname in ("di", "si", "dx", "r10", "r8"):
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
#   * 只实现了语料真正用到的 `write`(1) 与 `exit`(60)；其余号返回 -1。
#     `openat`/`read`(即 /proc/self/cmdline 的 argv 合成)、`getdents64`、`newfstatat`、
#     `brk` 还没做 —— 所以**还不能跑需要 argv 或文件 I/O 的程序**。
#   * Windows 没有 procfs，argv 只能靠 shim 合成（未做）。

PE_IMAGE_BASE = 0x140000000
PE_SEC_ALIGN, PE_FILE_ALIGN = 0x1000, 0x200
PE_TEXT_RVA = 0x1000
PE_IMPORTS = ["ExitProcess", "GetStdHandle", "WriteFile"]
PE_STD_OUTPUT, PE_STD_ERROR = -11, -12
SYS_EXIT, SYS_WRITE = 60, 1


def build_pe_idata(d: int, funcs: list[str]) -> tuple[bytes, dict, int]:
    """kernel32.dll 的导入表。返回 (blob, {标签: IAT 槽 RVA}, IAT RVA)。

    描述符表**必须**以一条全零描述符终止：少了它，加载器会把紧随其后的 ILT 当成第二条
    描述符，导入解析中途失败，IAT 保持未填，随后 `call rax` 直接崩（症状是 SIGSEGV）。
    """
    n = len(funcs)
    ilt = d + 40                                   # 一条描述符 + 一条全零终止项
    iat = ilt + (n + 1) * 8
    off = iat + (n + 1) * 8
    hint_rvas, blobs = [], []
    for nm in funcs:
        b = struct.pack("<H", 0) + nm.encode() + b"\x00"
        if len(b) % 2:
            b += b"\x00"
        hint_rvas.append(off)
        blobs.append((off, b))
        off += len(b)
    dll_rva = off
    dll = b"kernel32.dll\x00"
    off += len(dll)

    out = bytearray(off - d)
    struct.pack_into("<IIIII", out, 0, ilt, 0, 0, dll_rva, iat)
    for i, r in enumerate(hint_rvas):
        struct.pack_into("<Q", out, ilt - d + i * 8, r)
        struct.pack_into("<Q", out, iat - d + i * 8, r)
    for at, b in blobs:
        out[at - d:at - d + len(b)] = b
    out[dll_rva - d:] = dll
    return bytes(out), {f"__iat_{nm}": iat + i * 8 for i, nm in enumerate(funcs)}, iat


def _call_iat(asm: Asm, slot: str) -> None:
    """call qword ptr [slot]。镜像不是 ASLR（无 DYNAMIC_BASE、无 .reloc），绝对取址足够。"""
    at = asm.here()
    asm.emit(b"\x48\xA1" + b"\x00" * 8)            # mov rax, [abs64]
    asm.fixups.append((at + 2, slot, 8, "abs"))
    asm.emit(b"\xFF\xD0")                          # call rax


def emit_win_shim(em: "PeEmitter") -> None:
    """`__win_syscall`: rax = syscall 号，参数按 Linux 习惯在 rdi/rsi/rdx。

    Linux 的 `syscall` 是把号放 rax、参数放 rdi/rsi/rdx/r10/r8；这里保持同一套寄存器约定，
    所以 `_call_asm` 的取参代码一个字都不用改，只是把 `0F 05` 换成 `call __win_syscall`。
    """
    a = em.asm
    a.label("__win_syscall")
    a.emit(b"\x55")                                # push rbp
    a.emit(b"\x48\x89\xE5")                        # mov rbp, rsp
    a.emit(b"\x48\x83\xE4\xF0")                    # and rsp, -16
    a.emit(b"\x48\x83\xEC\x40")                    # sub rsp, 0x40 (32 shadow + 第五参 + 溢写槽)

    a.emit(b"\x3D" + struct.pack("<I", SYS_EXIT))  # cmp eax, 60
    a.emit(b"\x0F\x84")                            # je __ws_exit
    a.fixups.append((a.here(), "__ws_exit", 4, "rel"))
    a.emit(b"\x00\x00\x00\x00")
    a.emit(b"\x3D" + struct.pack("<I", SYS_WRITE))  # cmp eax, 1
    a.emit(b"\x0F\x84")                             # je __ws_write
    a.fixups.append((a.here(), "__ws_write", 4, "rel"))
    a.emit(b"\x00\x00\x00\x00")
    a.emit(b"\x48\xC7\xC0\xFF\xFF\xFF\xFF")        # mov rax, -1   (未实现的号)
    a.emit(b"\x48\x89\xEC")                        # mov rsp, rbp
    a.emit(b"\x5D\xC3")                            # pop rbp; ret

    a.label("__ws_exit")                           # exit(code) -> ExitProcess
    a.emit(mov_rr(RCX, RDI))                       # mov ecx, edi
    _call_iat(a, "__iat_ExitProcess")
    a.emit(b"\x0F\x0B")                            # ud2 (不该回来)

    # write(fd, buf, len) -> GetStdHandle + WriteFile
    # r10/r11 是易失寄存器，跨调用必须溢写到栈上（[rsp+0x20] 留给第五参）。
    a.label("__ws_write")
    a.emit(mov_mr(RSP, 0x30, RSI))                 # mov [rsp+0x30], rsi   ; buf
    a.emit(mov_mr(RSP, 0x38, RDX))                 # mov [rsp+0x38], rdx   ; len
    a.emit(b"\xB9" + struct.pack("<I", PE_STD_OUTPUT & 0xFFFFFFFF))    # mov ecx, -11
    a.emit(b"\x83\xFF\x02")                        # cmp edi, 2
    a.jcc("ne", "__ws_stdout_ok")
    a.emit(b"\xB9" + struct.pack("<I", PE_STD_ERROR & 0xFFFFFFFF))     # mov ecx, -12
    a.label("__ws_stdout_ok")
    _call_iat(a, "__iat_GetStdHandle")
    a.emit(mov_rr(RCX, RAX))                       # mov rcx, rax          ; hFile
    a.emit(mov_rm(RDX, RSP, 0x30))                 # mov rdx, [rsp+0x30]   ; lpBuffer
    a.emit(mov_rm(R8, RSP, 0x38))                  # mov r8,  [rsp+0x38]   ; nBytes
    a.emit(b"\x45\x31\xC9")                        # xor r9d, r9d          ; lpWritten = NULL
    a.emit(b"\x31\xC0")                            # xor eax, eax
    a.emit(mov_mr(RSP, 0x20, RAX))                 # mov [rsp+0x20], rax   ; 第五参 lpOverlapped = NULL
    _call_iat(a, "__iat_WriteFile")
    a.emit(mov_rm(RAX, RSP, 0x38))                 # 返回写入字节数（照 write(2)）
    a.emit(b"\x48\x89\xEC")                        # mov rsp, rbp
    a.emit(b"\x5D\xC3")                            # pop rbp; ret


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


def build_pe(text: bytes, data: bytes, entry_rva: int, data_rva: int,
             idata: bytes, idata_rva: int) -> bytes:
    """静态 PE32+（console, x86-64）。节表按 RVA 升序：.text -> .data -> .idata。"""
    nsec = 3
    hdr = 0x40 + 4 + 20 + 240 + 40 * nsec
    toff = align_up(hdr, PE_FILE_ALIGN)                      # .text 的 raw
    doff = toff + align_up(len(text), PE_FILE_ALIGN)         # .data 的 raw
    ioff = doff + align_up(len(data), PE_FILE_ALIGN)         # .idata 的 raw
    image_size = align_up(idata_rva + len(idata), PE_SEC_ALIGN)

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
    struct.pack_into("<Q", opt, 72, 0x100000)
    struct.pack_into("<Q", opt, 80, 0x1000)
    struct.pack_into("<Q", opt, 88, 0x100000)
    struct.pack_into("<Q", opt, 96, 0x1000)
    struct.pack_into("<I", opt, 108, 16)
    struct.pack_into("<II", opt, 112 + 8, idata_rva, 40)     # Import Directory
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

    o += sec(b".text", len(text), PE_TEXT_RVA,
             align_up(len(text), PE_FILE_ALIGN), toff, 0x60000020)
    o += sec(b".data", idata_rva - data_rva, data_rva,
             align_up(len(data), PE_FILE_ALIGN), doff, 0xC0000040)
    o += sec(b".idata", len(idata), idata_rva,
             align_up(len(idata), PE_FILE_ALIGN), ioff, 0xC0000040)
    o += b"\x00" * (toff - len(o)) + text
    o += b"\x00" * (doff - len(o)) + bytes(data)
    # 最后一节也必须补齐到声明的 SizeOfRawData，否则加载器读到 EOF 之外直接拒收
    o += b"\x00" * (ioff - len(o)) + idata
    o += b"\x00" * (align_up(len(idata), PE_FILE_ALIGN) - len(idata))
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


def _pe_emit(funcs, globals_, slots) -> tuple[bytes, int]:
    em = PeEmitter(globals_)
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    for name, rva in slots.items():
        em.asm.labels[name] = PE_IMAGE_BASE + rva
    em.emit_entry_stub("_start")
    emit_win_shim(em)
    for f in funcs:
        em.emit_func(f)
    return em.asm.finalize(), em.asm.labels["__entry"]


def compile_pe(text: str) -> tuple[bytes, dict]:
    global TEXT_VADDR                              # .text 钉在最低 RVA
    globals_, funcs = parse_ll(text)
    if not any(f.name == "_start" for f in funcs):
        raise Unsupported("没有 _start 入口（PE 产物需要一个用户态入口）")

    TEXT_VADDR = PE_IMAGE_BASE + PE_TEXT_RVA
    # .text 在最低 RVA，.data 紧随其后 —— 所以 data 的 RVA 取决于代码长度。代码长度与地址
    # 无关（所有回填都是定长），于是「发射 -> 量长度 -> 重排 -> 再发射」一步收敛。
    # 早先 .text 固定 0x1000、.data 固定 0x2000，文本一过一页两节 RVA 就重叠，
    # 加载器报 "不是有效的 Win32 应用程序"。
    data_rva = PE_TEXT_RVA + PE_SEC_ALIGN
    for _ in range(4):
        data, bss = _pe_layout_globals(globals_, data_rva)
        idata_rva = align_up(data_rva + max(len(data) + bss, 1), PE_SEC_ALIGN)
        idata, slots, _ = build_pe_idata(idata_rva, PE_IMPORTS)
        code, entry = _pe_emit(funcs, globals_, slots)
        want = align_up(PE_TEXT_RVA + len(code), PE_SEC_ALIGN)
        if want == data_rva:
            break
        data_rva = want
    else:
        raise ElfError("PE 布局不动点没收敛")

    blob = build_pe(code, bytes(data), entry - PE_IMAGE_BASE, data_rva, idata, idata_rva)
    return blob, {"text": len(code), "data": len(data), "bss": bss,
                  "entry": entry - PE_IMAGE_BASE, "funcs": [f.name for f in funcs]}


# ------------------------------------------------------------------ 驱动


def compile_ll(text: str) -> tuple[bytes, dict]:
    global TEXT_VADDR, DATA_VADDR
    TEXT_VADDR, DATA_VADDR = ELF_TEXT_VADDR, ELF_DATA_VADDR   # PE 目标会改这两个全局
    globals_, funcs = parse_ll(text)
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
    em = Emitter(globals_)
    for g in globals_:
        em.asm.labels[g.name] = g.addr
    if not any(f.name == "_start" for f in funcs):
        raise Unsupported("没有 _start 入口（原生产物 v0 需要一个用户态入口）")
    em.emit_entry_stub("_start")
    for f in funcs:
        em.emit_func(f)
    text = em.asm.finalize()
    entry = em.asm.labels["__entry"]
    return build_elf(text, bytes(data), bss, entry), {
        "text": len(text), "data": len(data), "bss": bss, "entry": entry,
        "funcs": [f.name for f in funcs],
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out, check, target = None, False, "elf"
    files = []
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
        else:
            files.append(a)
        i += 1
    if len(files) != 1 or target not in ("elf", "pe"):
        print("用法: lomelf.py IN.ll [-o OUT] [--target elf|pe] [--check]", file=sys.stderr)
        return 2
    src = Path(files[0])
    try:
        blob, info = (compile_pe if target == "pe" else compile_ll)(
            src.read_text(encoding="utf-8"))
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
