#!/usr/bin/env python3
"""lvm.py — LVM 的参考实现：**那份 IR 的第四个读者**（`docs/243`）。

吃 `lomelf` 的同一种文本 IR 子集，**当场执行**，不落机器码。
解析整段复用 `lomelf`（它已经产出 `Instr`/`Block`/`Func`/`Global` 这套表），
这里只把"发射"换成"求值" —— 见 `docs/243` §3。

**跳过的整段**（`docs/243` §2）：x86 编码、`Asm`、`plan_frame`（SSA→栈槽）、
重定位、ELF/PE 写出、收编面。**留下的**是**语言语义**：零/符号扩展、按宽度截断、
phi 的两趟模型。

用法: python tools/lvm.py FILE.lomt      （与 `lomelf` 同一条前门：`.lomt` -> IR -> 跑）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf  # noqa: E402

#: 解释器自己的地址空间。全局从 `GLOBAL_BASE` 起、堆紧跟着、栈从顶上往下。
MEM_SIZE = 16 * 1024 * 1024
GLOBAL_BASE = 0x10000
STACK_TOP = MEM_SIZE
#: 每一层调用一段自己的栈区（按深度算基址 —— 见 `run` 里那段注释）。
FRAME_SIZE = 4096

#: 每个标量占 8 字节（与 `lomelf` 的栈槽模型同口径，`SCALAR_SIZE`）。
SLOT = 8


class LvmError(Exception):
    """解释器自己的错（**不是**"语言不支持" —— 那种要另说，见 `docs/243` §1）。"""


# ---------------------------------------------------------------- 值

def _is_agg(ty: str) -> bool:
    return lomelf.is_agg(ty)


def mask(v: int, ty: str) -> int:
    """按类型的位宽截断 —— 这是**语言语义**，照抄 `lomelf` 的 `put`。"""
    n = lomelf.SCALAR_SIZE.get(ty)
    if n is None:
        return v
    return v & ((1 << (n * 8)) - 1)


def signed(v: int, ty: str) -> int:
    """同一位串按**有符号**读 —— 照抄 `lomelf.get_signed`。"""
    n = lomelf.SCALAR_SIZE.get(ty)
    if n is None:
        return v
    bits = n * 8
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def zeroext(v: int, ty: str) -> int:
    """零扩展到 64 —— 照抄 `lomelf.get` 的那一步（**必须留**，它是语义不是代码生成）。"""
    return mask(v, ty)


# ---------------------------------------------------------------- 指令表

_ARITH = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
    "and": lambda a, b: a & b,
    "or": lambda a, b: a | b,
    "xor": lambda a, b: a ^ b,
    "shl": lambda a, b: a << b,
    "lshr": lambda a, b: a >> b,
    "ashr": lambda a, b: a >> b,          # 符号由下面 signed() 处理
}
_ICMP = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "ugt": lambda a, b: a > b,
    "uge": lambda a, b: a >= b,
    "ult": lambda a, b: a < b,
    "ule": lambda a, b: a <= b,
    "sgt": lambda a, b: a > b,
    "sge": lambda a, b: a >= b,
    "slt": lambda a, b: a < b,
    "sle": lambda a, b: a <= b,
}
_SIGNED_CMP = ("sgt", "sge", "slt", "sle")

_CALL_ASM = lomelf.CALL_ASM_RE


def _parse_asm_constraints(spec: str) -> tuple[str | None, list[str]]:
    """`"={ax},{ax},{di},…,~{cx}"` -> (输出寄存器, [各输入的寄存器])。"""
    out = None
    ins: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part or part.startswith("~"):
            continue
        m = re.match(r"([=+*&]?)\{([^}]+)\}", part)
        if not m:
            continue
        kind, reg = m.group(1), m.group(2)
        if "=" in kind or "+" in kind:
            out = reg
        else:
            ins.append(reg)
    return out, ins


class Mem:
    """一块字节数组 + 一个地址就是下标。全局 / 堆 / 栈都住在里面。"""

    def __init__(self) -> None:
        self.buf = bytearray(MEM_SIZE)
        self.heap = GLOBAL_BASE
        self.sp = STACK_TOP

    def put(self, addr: int, data: bytes) -> None:
        if addr < 0 or addr + len(data) > MEM_SIZE:
            raise LvmError(f"写越界: {addr} + {len(data)}")
        self.buf[addr:addr + len(data)] = data

    def get(self, addr: int, n: int) -> bytes:
        if addr < 0 or addr + n > MEM_SIZE:
            raise LvmError(f"读越界: {addr} + {n}")
        return bytes(self.buf[addr:addr + n])

    def alloc(self, n: int, align: int = 8) -> int:
        self.heap = (self.heap + align - 1) // align * align
        a = self.heap
        self.heap += n
        if self.heap >= self.sp:
            raise LvmError("堆撞上栈 —— 地址空间用完了")
        return a


class Frame:
    """一次调用的栈帧：SSA 名 -> 值；`alloca` 的槽也在这一层。"""

    def __init__(self, mem: Mem, base: int) -> None:
        self.mem = mem
        self.base = base          # 帧底（往上是本帧的槽）
        self.slots: dict[str, int] = {}
        self.vals: dict[str, object] = {}
        self.next = base

    def slot(self) -> int:
        a = self.next
        self.next += SLOT
        return a


class Vm:
    def __init__(self, globals_: list, funcs: list, externs: list, out) -> None:
        self.mem = Mem()
        self.globals = {g.name: g for g in globals_}
        self.funcs = {f.name: f for f in funcs}
        self.externs = set(externs)
        self.out = out
        self.depth = 0
        self._place_globals()

    def _place_globals(self) -> None:
        for g in self.globals.values():
            data = g.data or b"\0" * lomelf.size_of(g.ty)
            align = max(1, min(8, lomelf.size_of(g.ty) or 1))
            g.addr = self.mem.alloc(len(data), align)
            self.mem.put(g.addr, data)

    # ------------------------------------------------------------ 值

    def operand(self, fr: Frame, s: str):
        """一个操作数文本 -> 宿主值。标量是 int，聚合是**内存映像的 bytes**。"""
        ty, v = lomelf.parse_operand(s)
        if v.startswith("%"):
            got = fr.vals.get(v)
            if got is None:
                raise LvmError(f"读未定义的值 {v}")
            return ty, got
        if v.startswith("@"):
            g = self.globals.get(v)
            if g is None:
                raise LvmError(f"未知全局 {v}")
            return ty, g.addr
        if v == "undef" or v == "null":
            return ty, 0 if not _is_agg(ty) else b"\0" * lomelf.size_of(ty)
        if v == "true":
            return ty, 1
        if v == "false":
            return ty, 0
        return ty, int(v)

    def load(self, ty: str, addr: int):
        n = lomelf.size_of(ty)
        raw = self.mem.get(addr, n)
        if _is_agg(ty):
            return raw
        return int.from_bytes(raw, "little")

    def store(self, ty: str, addr: int, val) -> None:
        if _is_agg(ty):
            self.mem.put(addr, val if isinstance(val, bytes) else bytes(val))
            return
        n = lomelf.size_of(ty)
        self.mem.put(addr, (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little"))


class _Exit(Exception):
    """`exit` 走系统调用 —— 它不返回，用异常把栈弹干净。"""

    def __init__(self, code: int) -> None:
        self.code = code


class Executor(Vm):
    # ------------------------------------------------------------ 系统调用
    def syscall(self, nr: int, a: list[int]) -> int:
        """**唯一**的对外通道（`docs/243` §3.4）。表里没有的就点名拒。"""
        if nr == 1:                                    # write(fd, buf, count)
            fd, buf, n = a[0], a[1], a[2]
            data = self.mem.get(buf, n)
            if fd in (1, 2):
                self.out.write(data)
                self.out.flush()
            return n
        if nr == 12:                                   # brk(addr)
            # `_IR_HEAP` 的分配器用它向 OS 要页。解释器的堆本来就在地址空间里，
            # 所以这里只需把"当前可信的堆底"如实回一个数：给了就认，没给就报现值。
            want = a[0]
            if want == 0 or (self.mem.heap <= want < self.mem.sp):
                if want:
                    self.mem.heap = want
                return self.mem.heap
            return self.mem.heap
        if nr == 60:                                   # exit(code)
            raise _Exit(a[0] & 0xFF)
        raise LvmError(f"系统调用表里没有 {nr}（`docs/243` §3.4）")

    # ------------------------------------------------------------ 调用
    def call(self, fr: Frame, text: str):
        m = _CALL_ASM.match(text)
        if m:
            _, spec, argtext = m.group(1), m.group(2), m.group(3)
            out_reg, ins = _parse_asm_constraints(spec)
            a = [self.operand(fr, s)[1] for s in lomelf.split_top(argtext)]
            nr = a[0]
            return self.syscall(nr, a[1:])
        body = text[len("call "):].strip()
        if body.startswith("void"):
            retty, body = None, body[4:].strip()
        else:
            retty, i = lomelf.parse_type(body)
            body = body[i:].strip()
        m2 = re.match(r"@([-A-Za-z0-9_.]+)\((.*)\)\s*$", body, re.S)
        if not m2:
            raise LvmError(f"解析不了调用: {text!r}")
        name = m2.group(1)
        args = [s for s in lomelf.split_top(m2.group(2)) if s]
        if name in self.externs:
            raise LvmError(f"`{name}` 是 extern —— T0 不接 FFI（`docs/243` §5）")
        fn = self.funcs.get(name)
        if fn is None:
            raise LvmError(f"没有这个函数: {name}")
        return self.run(fn, args, fr)

    # ------------------------------------------------------------ 执行
    def run(self, fn, argtexts: list[str], caller: Frame | None):
        # ⚠ **每一层调用各占一段自己的栈区**（按深度算基址）。
        # 第一版每层都取 `mem.sp - 4096`，而 `mem.sp` 从不下降 ⇒ **被调用者和调用者
        # 用同一段地址**，被调者的 `alloca` 覆盖了调用者的槽，返回后读回来全是 0 ——
        # 症状是"凡是算数再打印的程序全错"，而 `user_hello`（值在调用**前**就按值传走）
        # 照样对，所以一开始没露。
        self.depth += 1
        base = STACK_TOP - self.depth * FRAME_SIZE
        if base <= self.mem.heap:
            self.depth -= 1
            raise LvmError("栈溢出")
        try:
            return self._run_body(fn, argtexts, caller, base)
        finally:
            self.depth -= 1

    def _run_body(self, fn, argtexts: list[str], caller: Frame | None, base: int):
        fr = Frame(self.mem, base)
        # 形参与实参：实参在**调用者的**上下文里求值（`docs/243` §3.4）
        for (pty, pname), a in zip(fn.params, argtexts):
            if caller is None:
                fr.vals[pname] = 0
                continue
            _ty, v = self.operand(caller, a)
            fr.vals[pname] = v
        label = fn.blocks[0].label
        prev = None
        ret = 0
        while True:
            blk = next((b for b in fn.blocks if b.label == label), None)
            if blk is None:
                raise LvmError(f"{fn.name}: 没有块 {label}")
            for ins in blk.instrs:
                if ins.text.startswith("phi "):
                    if ins.dest:
                        fr.vals[ins.dest] = self._phi(fr, ins.text, prev)
                    continue
                if ins.text.startswith("br "):
                    label, _pred = self._br(fr, ins.text, blk.label)
                    prev = blk.label
                    break
                if ins.text.startswith("switch "):
                    label = self._switch(fr, ins.text)
                    prev = blk.label
                    break
                if ins.text.startswith("ret"):
                    ret = self._ret(fr, ins.text)
                    return ret
                if ins.text.startswith("unreachable"):
                    raise LvmError("执行到 unreachable")
                # ⚠ **没有目的值的指令也要执行**（`store` 就是：它 `dest is None`）。
                # 第一版写成 `if ins.dest:` 才求值 —— 于是所有 `store` 被**静默跳过**，
                # 症状是实参全是 0、程序照样"跑完"（连错都不报，最坏的一种）。
                v = self._eval(fr, ins.text)
                if ins.dest:
                    fr.vals[ins.dest] = v
            else:
                raise LvmError(f"{fn.name}: 块 {label} 没有终结指令")

    # ------------------------------------------------------------ 单条指令
    def _eval(self, fr: Frame, text: str):
        if text.startswith("alloca"):
            ty, _ = lomelf.parse_type(text[len("alloca "):])
            a = fr.slot()
            fr.next = a + max(SLOT, lomelf.size_of(ty))
            fr.slots[text] = a
            return a
        if text.startswith("load "):
            body = text[len("load "):]
            ty, i = lomelf.parse_type(body)
            _bty, addr = self.operand(fr, body[i:].lstrip(", ").strip())
            return self.load(ty, addr)
        if text.startswith("store "):
            body = text[len("store "):]
            ty, i = lomelf.parse_type(body)
            # `store <ty> <val>, ptr <addr>` —— 值那一格**没带类型**，得把类型的给它。
            val_s, ptr_s = lomelf.split_top(body[i:].lstrip(", "))
            _vty, val = self.operand(fr, ty + " " + val_s)
            _pty, addr = self.operand(fr, ptr_s)
            self.store(ty, addr, val)
            return None
        if text.startswith("getelementptr"):
            return self._gep(fr, text)
        if text.startswith("extractvalue "):
            return self._extractvalue(fr, text)
        if text.startswith("insertvalue "):
            return self._insertvalue(fr, text)
        if text.startswith("icmp "):
            return self._icmp(fr, text)
        if text.startswith("atomicrmw "):
            return self._atomicrmw(fr, text)
        if text.startswith(("trunc ", "zext ", "sext ", "ptrtoint ", "inttoptr ")):
            return self._cast(fr, text)
        if text.startswith("call "):
            return self.call(fr, text)
        m = re.match(r"(sdiv|udiv|srem|urem) (\w+) (\S+), (\S+)", text)
        if m:
            op, ty, a_s, b_s = m.groups()
            _t, a = self.operand(fr, ty + " " + a_s)
            _t2, b = self.operand(fr, ty + " " + b_s)
            if op in ("sdiv", "srem"):
                a, b = signed(a, ty), signed(b, ty)
            if b == 0:
                raise LvmError("除以零")
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            # ⚠ **取余要减回去** —— 第一版只给 `srem` 减了，`urem` 直接返回**商**。
            # 症状极难认：程序跑得完、退出码也对，只是**每一位数都是商**（`42 % 10` 得 4），
            # 于是十进制打印全是错位。`user_hello` 不做算术，所以一开始没露。
            if op in ("srem", "urem"):
                return mask(a - b * q, ty)
            return mask(q, ty)
        m = re.match(r"(\w+) (\w+) (\S+), (\S+)", text)
        if m and m.group(1) in _ARITH:
            op, ty, a_s, b_s = m.groups()
            _t, a = self.operand(fr, ty + " " + a_s)
            _t2, b = self.operand(fr, ty + " " + b_s)
            if op == "ashr":
                v = signed(a, ty) >> b
            else:
                v = _ARITH[op](a, b)
            return mask(v, ty)
        raise LvmError(f"这个指令 T0 没实现: {text!r}")

    def _gep(self, fr: Frame, text: str) -> int:
        body = text[len("getelementptr"):].strip()
        if body.startswith("inbounds"):
            body = body[len("inbounds"):].strip()
        ty, i = lomelf.parse_type(body)
        # ⚠ 类型后面紧跟一个逗号，而 `split_top` 在开头那个逗号处会吐一个**空元素**
        # （它按逗号切、不跳过空段）—— 所以先把这个逗号吃掉。
        parts = lomelf.split_top(body[i:].lstrip(", "))
        _bty, base = self.operand(fr, parts[0])
        off, cur = 0, ty
        for k, idx_s in enumerate(parts[1:]):
            _ity, idx = self.operand(fr, idx_s)
            if k == 0:
                off += idx * lomelf.size_of(cur)
                continue
            if cur.startswith("["):
                # `array_parts` 返回 **(个数, 元素)** —— 个数在前，别取 `[0]`。
                cur = lomelf.array_parts(cur)[1]
                off += idx * lomelf.size_of(cur)
            elif cur.startswith("{"):
                off += lomelf.field_offset(cur, idx)[0]
                cur = lomelf.field_offset(cur, idx)[1]
            else:
                raise LvmError(f"GEP 的这一步 T0 没实现: {text!r}")
        return base + off

    def _extractvalue(self, fr: Frame, text: str):
        ty, i = lomelf.parse_type(text[len("extractvalue "):])
        parts = lomelf.split_top(text[len("extractvalue ") + i:])
        _vty, val = self.operand(fr, (ty + " " + parts[0]).lstrip())
        off, fty = lomelf.field_offset(ty, int(parts[-1]))
        return self._from_bytes(fty, val[off:off + lomelf.size_of(fty)])

    def _insertvalue(self, fr: Frame, text: str):
        ty, i = lomelf.parse_type(text[len("insertvalue "):])
        parts = lomelf.split_top(text[len("insertvalue ") + i:])
        _bty, agg = self.operand(fr, (ty + " " + parts[0]).lstrip())
        _vty, val = self.operand(fr, parts[1])
        off, fty = lomelf.field_offset(ty, int(parts[2]))
        buf = bytearray(agg if isinstance(agg, bytes) else b"\0" * lomelf.size_of(ty))
        buf[off:off + lomelf.size_of(fty)] = self._to_bytes(fty, val)
        return bytes(buf)

    def _from_bytes(self, ty: str, raw: bytes):
        return raw if _is_agg(ty) else int.from_bytes(raw, "little")

    def _to_bytes(self, ty: str, val) -> bytes:
        if _is_agg(ty):
            return val if isinstance(val, bytes) else bytes(val)
        n = lomelf.size_of(ty)
        return (int(val) & ((1 << (n * 8)) - 1)).to_bytes(n, "little")

    def _icmp(self, fr: Frame, text: str) -> int:
        m = re.match(r"icmp (\w+) (\w+) (\S+), (\S+)", text)
        if not m:
            raise LvmError(f"icmp 解析不了: {text!r}")
        pred, ty, a_s, b_s = m.groups()
        _t, a = self.operand(fr, ty + " " + a_s)
        _t2, b = self.operand(fr, ty + " " + b_s)
        if pred in _SIGNED_CMP:
            a, b = signed(a, ty), signed(b, ty)
        return 1 if _ICMP[pred](a, b) else 0

    def _atomicrmw(self, fr: Frame, text: str) -> int:
        m = re.match(r"atomicrmw (\w+) ptr (\S+), (\w+) (\S+)(?: \w+)*", text)
        if not m or m.group(1) != "add":
            raise LvmError(f"atomicrmw 只实现了 add: {text!r}")
        _t, addr = self.operand(fr, "ptr " + m.group(2))
        ty, rhs = m.group(3), m.group(4)
        _t2, val = self.operand(fr, ty + " " + rhs)
        old = self.load(ty, addr)
        self.store(ty, addr, mask(old + val, ty))
        return old

    def _cast(self, fr: Frame, text: str):
        m = re.match(r"(\w+) (\w+) (\S+) to (\w+)", text)
        if not m:
            raise LvmError(f"转型解析不了: {text!r}")
        op, fty, a_s, tty = m.groups()
        _t, a = self.operand(fr, fty + " " + a_s)
        if op == "trunc":
            return mask(a, tty)
        if op == "zext":
            return zeroext(a, fty)
        if op == "sext":
            return signed(a, fty) & ((1 << (lomelf.size_of(tty) * 8)) - 1)
        return a                                       # ptrtoint / inttoptr：地址就是整数

    # ------------------------------------------------------------ 控制流
    def _phi(self, fr: Frame, text: str, prev: str | None):
        ty, entries = lomelf.parse_phi(text)
        for val_s, block in entries:
            if block == prev:
                # ⚠ **入边值不带类型**（`lomelf` 发的是 `phi i64 [ 0, %entry ]`）——
                # 类型只写在 phi 头上。所以先按原样试，不行再把 phi 的类型补上。
                # （第一版直接按原样解，撞到 `0` 就抛"解析不了的值"。）
                try:
                    _t, v = self.operand(fr, val_s)
                except lomelf.ElfError:
                    _t, v = self.operand(fr, f"{ty} {val_s}")
                return v
        raise LvmError(f"phi 没有来自 {prev} 的入边: {text!r}")

    def _br(self, fr: Frame, text: str, cur: str) -> tuple[str, str]:
        m = re.match(r"br label %([\w.]+)$", text)
        if m:
            return m.group(1), cur
        m = lomelf.BR_COND_RE.search(text)
        if not m:
            raise LvmError(f"br 解析不了: {text!r}")
        _t, cond = self.operand(fr, "i1 " + m.group(1))
        return (m.group(2) if cond else m.group(3)), cur

    def _switch(self, fr: Frame, text: str) -> str:
        m = lomelf.SWITCH_RE.match(text)
        if not m:
            raise LvmError(f"switch 解析不了: {text!r}")
        ty, val_s, default, body = m.groups()
        _t, v = self.operand(fr, ty + " " + val_s)
        for cty, cval, label in lomelf.CASE_RE.findall(body):
            if mask(v, cty) == int(cval):
                return label
        return default

    def _ret(self, fr: Frame, text: str):
        rest = text[len("ret"):].strip()
        if not rest or rest == "void":
            return 0
        ty, _i = lomelf.parse_type(rest)
        _t, v = self.operand(fr, rest)
        return v


# ---------------------------------------------------------------- 前门

def run_ir(text: str, out) -> int:
    """IR 文本 -> 跑完 -> 进程该退的码。**入口就是 `_start`**（`docs/243` §3.5）。"""
    globals_, funcs, externs = lomelf.parse_ll(text)
    vm = Executor(globals_, funcs, externs, out)
    start = next((f for f in funcs if f.name == "_start"), None)
    if start is None:
        raise LvmError("这份 IR 里没有 `_start`")
    try:
        vm.run(start, [], None)
    except _Exit as e:
        return e.code
    return 0


def run_source(src: Path, root: Path, out=None) -> int:
    """与 `lomelf` 同一条前门：`.lomt` -> IR -> 跑。"""
    import lomentc  # noqa: PLC0415

    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, root, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise LvmError(f"检查不过: {errs[:2]}")
    return run_ir(lomentc.emit_llvm(mod, root, deps), out if out is not None else sys.stdout)


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    import io  # noqa: PLC0415

    ap = argparse.ArgumentParser(prog="lvm", description="LVM T0：那份 IR 的第四个读者（docs/243）")
    ap.add_argument("entry", help=".lomt 入口，或 --ir 时是 .ll")
    ap.add_argument("--ir", action="store_true", help="直接吃 IR 文本（跳过前端）")
    ap.add_argument("--root", default=".", help="解析 use 的根（默认当前目录）")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    buf = io.BytesIO()
    try:
        if a.ir:
            rc = run_ir(Path(a.entry).read_text(encoding="utf-8"), buf)
        else:
            rc = run_source(Path(a.entry).resolve(), root, buf)
    except (LvmError, lomelf.ElfError, lomelf.Unsupported) as e:
        sys.stdout.buffer.write(buf.getvalue())
        print(f"lvm: {e}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(buf.getvalue())
    return rc


if __name__ == "__main__":
    sys.exit(main())
