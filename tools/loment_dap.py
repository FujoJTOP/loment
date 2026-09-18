#!/usr/bin/env python3
"""loment_dap.py — Loment 的**调试适配器**（DAP）+ `ptrace` 后端（`docs/190`）。

让 VS Code 里按 F5 能调试 `.lomt`。

## 为什么自己写后端，而不是包一层 gdb

**这台机器上没有 gdb，也没有 lldb**（WSL 里查过，只有 `strace`）。所以"链一条 gdb"
那条路一开始就不通。而自己写的一条是通的：

    VS Code --DAP(stdio)--> 本文件（在 WSL 里）--ptrace--> 被调试的 ELF

**必须跑在 WSL（Linux 真内核）里**：`ptrace` 是 Linux 系统调用，WSL2 有（实测
`PTRACE_TRACEME` 返回 0）。WSL1 没有 —— 那种环境下本文件会明确报错，不会假装能调。

## 它自己把整条链都走完（所以扩展那边只有"起一个进程"这一件事）

`tools/lomentc.py` 是**纯标准库**的 Python，所以在 WSL 里 import 得动（实测）。于是：

    import lomentc -> emit_llvm(debug=True) -> IR
    /mnt/c/.../clang.exe -g -c IR -> .o -> 链成 ELF       （Windows 侧工具链，见下）
    /mnt/c/.../llvm-objdump.exe -d -l ELF -> **行表**      （地址已经是最终 ELF 的）

**clang / llvm-objdump 是 Windows 侧的可执行文件**，从 WSL 走 `/mnt/c/...` 直接调
（`loment/bootstrap.sh` 走的是同一条路，用的是同一组开关）。参数里的路径要
`wslpath -w` 转成 Windows 形式，否则 Windows 那个进程认不出 `/mnt/d/...`。

**为什么行表取自最终 ELF 而不是那个 `.o`**：`.o` 的地址要重定位才等于最终地址，而
`llvm-objdump -d -l` 对 ELF 直接给的就是最终地址 —— 少一层算错的机会。实测 DWARF
**活得下来**（链完 `.debug_line` 还在）。

## 支持到哪 —— 以及**不支持什么**（写清楚，免得用的人以为是坏的）

支持：`initialize` `launch` `setBreakpoints`（按行）`configurationDone` `continue`
`next`（跨过）`stepIn`（步入）`stepOut` `stackTrace` `scopes` `variables` `threads`
`disconnect` `terminate`，以及 stdout/stderr 的 `output` 事件。

**不支持，而且是刻意的**：

  * **局部变量**。DWARF 里已经有 `!DILocalVariable`，但要读出值得解析 DWARF 的
    **位置表达式**（变量在寄存器还是栈槽、活到哪一行）—— 那是一整块活。现在给的是
    **寄存器**（`scopes` 里的 "Registers"），对一门整数/指针为主的语言够用。
  * **多于一层的调用栈**。栈回溯要 CFI（`.eh_frame`）或可靠的 RBP 链；现在 `stackTrace`
    只给**当前帧**（函数名 + 行号，从行表来）。这一条在文档里明说，不装作有。
  * **条件断点 / 命中计数 / 表达式求值 / watch**。

**宁可少给一样，也不给一个"点了没反应"的**：本仓一贯把静默当敌人（`docs/167`）。

用法（扩展会自动这么起，一般不用手敲）:

    wsl -e python3 tools/loment_dap.py
退出码: 0 = 正常结束。
"""

from __future__ import annotations

import bisect
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# ---------------------------------------------------------------- Windows 侧工具链
#
# **从 WSL 调 Windows 的可执行文件**（`bootstrap.sh` 走同一条路）。
# 找不到就是找不到 —— 报出来，不退回一个"看起来能编但没调试信息"的路径。

_CLANG_CANDIDATES = (
    "/mnt/c/Program Files/LLVM/bin/clang.exe",
    "/mnt/c/Program Files (x86)/LLVM/bin/clang.exe",
)
_OBJDUMP_CANDIDATES = (
    "/mnt/c/Program Files/LLVM/bin/llvm-objdump.exe",
    "/mnt/c/Program Files (x86)/LLVM/bin/llvm-objdump.exe",
)


def _first_exe(cands) -> str | None:
    for c in cands:
        if Path(c).is_file():
            return c
    return None


# ---------------------------------------------------------------- DAP 协议层


class Dap:
    """`Content-Length: N\\r\\n\\r\\n<json>` —— DAP 的线格式（与 LSP 同构）。"""

    def __init__(self, rfile, wfile) -> None:
        self.r = rfile
        self.w = wfile
        self.seq = 0

    def read(self) -> dict | None:
        n = None
        while True:
            line = self.r.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            m = re.match(rb"Content-Length:\s*(\d+)", line)
            if m:
                n = int(m.group(1))
        if n is None:
            return None
        return json.loads(self.r.read(n).decode("utf-8"))

    def send(self, obj: dict) -> None:
        self.seq += 1
        obj["seq"] = self.seq
        body = json.dumps(obj).encode("utf-8")
        self.w.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.w.flush()

    def respond(self, req: dict, body: dict | None = None, ok: bool = True,
                message: str = "") -> None:
        r = {"type": "response", "request_seq": req.get("seq", 0),
             "command": req.get("command", ""), "success": ok}
        if ok:
            r["body"] = body or {}
        else:
            r["message"] = message
        self.send(r)

    def event(self, name: str, body: dict | None = None) -> None:
        self.send({"type": "event", "event": name, "body": body or {}})


# ---------------------------------------------------------------- 行表


class LineTable:
    """地址 <-> (文件, 行)。从**最终 ELF** 的 DWARF 行表来（`llvm-objdump -d -l`）。

    `llvm-objdump -l` 把源位置打成 `; <路径>:<行>` 的行，跟在它后面那几条指令就属于它。
    """

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, int]] = []   # (addr, file, line) 按 addr 升序
        self.funcs: list[tuple[int, str]] = []       # (起始地址, 函数名) 按地址升序
        self.addrs: list[int] = []                   # 每条指令的地址，升序
        self.at_map: dict[int, tuple[str, int]] = {}  # 补过缺口之后：每条指令 -> 行
        self.at_keys: list[int] = []

    @classmethod
    def from_objdump(cls, dump: str, norm) -> "LineTable":
        t = cls()
        cur = None
        for ln in dump.splitlines():
            s = ln.strip()
            # `0000000000201210 <write_str>:` —— **函数边界只能从这儿拿**，
            # `llvm-objdump -l` 的行注释里没有函数名。单步要"按行走"就非它不可：
            # `next` 得知道"这条 call 进的是别的函数"，`fn_at` 得能报真名。
            m = re.match(r"^([0-9a-f]+) <(.+)>:$", s)
            if m:
                t.funcs.append((int(m.group(1), 16), m.group(2)))
                # **在函数边界上清空"当前的源行"**：`; file:line` 是一行注释，管到
                # 下一条注释为止。不清的话上一个函数最后那条标注会漏到下一个函数的
                # 序言上 —— 实测 `_start` 的 `subq $0x18,%rsp` 被算成了 `exit` 的第 17 行。
                cur = None
                continue
            if s.startswith(";"):
                src = s.lstrip("; ").strip()
                m = re.match(r"^(.*):(\d+)$", src)
                if m:
                    cur = (norm(m.group(1)), int(m.group(2)))
                continue
            m = re.match(r"^([0-9a-f]+):", s)
            if m:
                a = int(m.group(1), 16)
                t.addrs.append(a)
                if cur is not None:
                    t.rows.append((a, cur[0], cur[1]))
        t.rows.sort()
        t.funcs.sort()
        t.addrs.sort()
        t._fill()
        return t

    def _fill(self) -> None:
        """按**函数边界**把没有行注释的指令补上行号（只给 `at()` 用）。

        `llvm-objdump -l` 只标一部分指令：函数的序言（`subq`、把参数存栈那几条）
        和结尾的填充（`nopw`）都没有注释。不补的话帧上就会报出**别的函数**的行，
        或者干脆报不出来 —— 实测 `write_str` 的序言一条都没有，于是"单步之后停在哪"
        退回了兜底的第 1 行（一个假装）。

        补的规则：函数内部哪条指令没行号，就取**同一函数里最近的那条有行号的**指令的行。
        **补出来的行只给 `at()`**：断点仍旧只落在真被标注过的地址上（`addrs_for`），
        所以"这一行能不能设断点"不会因为补行而变松。
        """
        self.at_map = {a: (f, l) for a, f, l in self.rows}
        rstarts = [r[0] for r in self.rows]
        fstarts = [a for a, _ in self.funcs]
        for i, start in enumerate(fstarts):
            end = fstarts[i + 1] if i + 1 < len(fstarts) else None
            lo = bisect.bisect_left(rstarts, start)
            hi = len(self.rows) if end is None else bisect.bisect_left(rstarts, end)
            inside = self.rows[lo:hi]
            if not inside:
                continue                               # 这个函数一条行号都没有
            keys = [r[0] for r in inside]
            ai = bisect.bisect_left(self.addrs, start)
            while ai < len(self.addrs) and (end is None or self.addrs[ai] < end):
                a = self.addrs[ai]
                ai += 1
                if a in self.at_map:
                    continue
                j = bisect.bisect_right(keys, a) - 1
                f, l = inside[j][1:] if j >= 0 else inside[0][1:]
                self.at_map[a] = (f, l)
        self.at_keys = sorted(self.at_map)

    def at(self, addr: int) -> tuple[str, int] | None:
        """addr 属于哪一行。**精确**：只在指令地址上有答案（缺口已经按函数补过）。"""
        if not self.at_keys:
            return None
        i = bisect.bisect_right(self.at_keys, addr) - 1
        return self.at_map[self.at_keys[i]] if i >= 0 else None

    def addrs_for(self, file: str, line: int) -> list[int]:
        """某个文件的某一行 -> 它的指令地址（断点就设在这些地址上）。"""
        return [a for (a, f, l) in self.rows if f == file and l == line]

    def fn_at(self, addr: int) -> str:
        """这一条指令属于哪个函数（取「起点 <= addr」里最大的那个标签）。

        拿不到标签时退回文件基名 —— 与其编一个，不如说清楚它是什么。
        """
        lo, hi, best = 0, len(self.funcs) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.funcs[mid][0] <= addr:
                best = self.funcs[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        if best is not None:
            return best[1]
        hit = self.at(addr)
        return Path(hit[0]).stem if hit else "?"


# ---------------------------------------------------------------- ptrace


_PTRACE_TRACEME = 0
_PTRACE_PEEKTEXT = 1
_PTRACE_POKETEXT = 4
_PTRACE_CONT = 7
_PTRACE_SINGLESTEP = 9
_PTRACE_GETREGS = 12
_PTRACE_SETREGS = 13
_PTRACE_SETOPTIONS = 0x4200
_PTRACE_O_EXITKILL = 0x00100000

#: 单步走一行/一个函数时的上限。走满就**停在原地**（按行走的 `step_line` 碰上
#: "一行里塞了个循环"就会走满）。定这么大是因为一次 ptrace 单步只要几十微秒，
#: 4096 步不到 0.1 秒；而定小了会让正常的 `stepOut` 穿越一个长函数时半路停住。
_STEP_CAP = 4096


class _Regs(ctypes.Structure):
    """x86-64 的 `user_regs_struct`（**字段顺序是内核 ABI 的一部分**，别改）。"""
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9", "r8",
        "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip", "cs", "eflags",
        "rsp", "ss", "fs_base", "gs_base", "ds", "es", "fs", "gs")]


class Ptrace:
    """一层薄封装。**所有系统调用都在这里**，别处不碰 ctypes。"""

    def __init__(self) -> None:
        try:
            self.libc = ctypes.CDLL("libc.so.6", use_errno=True)
        except OSError as e:                                  # pragma: no cover
            raise RuntimeError(f"加载 libc 失败: {e}") from e
        # **这两行不写就是一颗静默的雷**：ctypes 默认把返回值当 `c_int`（32 位），
        # 而 `ptrace` 返回的是 `long`。`PEEKTEXT` 读一个机器字（8 字节）被截成 4 字节，
        # 而 `POKETEXT` 写回去的是完整 8 字节 —— 于是**每个断点后面那 4 个字节被改写**
        # （符号位的延展值，不是内存里原来的东西）。断点照样响（`0xCC` 在），
        # 但那 4 个字节上的指令已经坏了：单步/继续过去就是 SIGSEGV。
        # 实测症状：断点能停，`continue` 之后 `signal 11`。
        self.libc.ptrace.restype = ctypes.c_long
        self.libc.ptrace.argtypes = [ctypes.c_ulong, ctypes.c_ulong,
                                     ctypes.c_void_p, ctypes.c_void_p]

    def __call__(self, req: int, pid: int, addr: int = 0, data: int = 0) -> int:
        ctypes.set_errno(0)
        r = self.libc.ptrace(ctypes.c_ulong(req), ctypes.c_ulong(pid),
                             ctypes.c_void_p(addr), ctypes.c_void_p(data))
        err = ctypes.get_errno()
        # `PEEKTEXT` 正常也可能返回 -1（那 8 个字节就是全 1）—— 只有 errno 非零才算错。
        if r == -1 and err != 0:
            raise OSError(err, os.strerror(err))
        return int(r)

    def peek(self, pid: int, addr: int) -> int:
        return self(_PTRACE_PEEKTEXT, pid, addr, 0) & 0xFFFFFFFFFFFFFFFF

    def poke(self, pid: int, addr: int, word: int) -> None:
        self(_PTRACE_POKETEXT, pid, addr, word & 0xFFFFFFFFFFFFFFFF)

    def getregs(self, pid: int) -> _Regs:
        r = _Regs()
        self(_PTRACE_GETREGS, pid, 0, ctypes.addressof(r))
        return r

    def setregs(self, pid: int, r: _Regs) -> None:
        self(_PTRACE_SETREGS, pid, 0, ctypes.addressof(r))

    def cont(self, pid: int, sig: int = 0) -> None:
        self(_PTRACE_CONT, pid, 0, sig)

    def step(self, pid: int, sig: int = 0) -> None:
        self(_PTRACE_SINGLESTEP, pid, 0, sig)


# ---------------------------------------------------------------- 被调试的目标


class Target:
    """一个被 `ptrace` 着的进程。"""

    def __init__(self, elf: Path, argv: list[str], cwd: str, on_output) -> None:
        self.elf = elf
        self.argv = argv
        self.cwd = cwd
        self.on_output = on_output
        self.pt = Ptrace()
        self.pid: int = 0
        self.bps: dict[int, int] = {}          # addr -> 被换掉的原始字节
        self.pending: set[int] = set()          # 用户设的断点（重新落地时要用）
        self.stop_addr: int | None = None       # 此刻停在哪个地址（`_frame`/`_resume` 都读它）
        self.pre_stop: tuple[int, str] | None = None   # 已经停住了，`wait_stop` 直接交出去
        self.r_w = -1
        self.lines = LineTable()

    # ---- 起
    def launch(self) -> None:
        r, w = os.pipe()
        self.r_w = r
        pid = os.fork()
        if pid == 0:                                            # 子进程
            try:
                os.close(r)
                os.dup2(w, 1)
                os.dup2(w, 2)
                os.close(w)
                # **必须在 execve 之前**：`TRACEME` 让内核在 exec 之后给父进程一个
                # SIGTRAP —— 那一下是父进程"接管"的时机（也是 elf 已经加载完的时机）。
                self.pt(_PTRACE_TRACEME, 0)
                os.chdir(self.cwd)
                os.execv(str(self.elf), self.argv)
            except Exception:                                   # pragma: no cover
                os._exit(127)
        os.close(w)
        self.pid = pid
        os.waitpid(pid, 0)                                      # 等 execve 后的 SIGTRAP
        self.pt(_PTRACE_SETOPTIONS, pid, 0, _PTRACE_O_EXITKILL)
        threading.Thread(target=self._pump, args=(r,), daemon=True).start()

    def _pump(self, r: int) -> None:
        """把子进程的 stdout/stderr 读出来，转成 DAP 的 `output` 事件。"""
        buf = b""
        while True:
            try:
                chunk = os.read(r, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.on_output(line.decode("utf-8", "replace") + "\n")
        if buf:
            self.on_output(buf.decode("utf-8", "replace"))

    # ---- 断点
    def _insert(self, addr: int) -> None:
        if addr in self.bps:
            return
        word = self.pt.peek(self.pid, addr)
        self.bps[addr] = word
        self.pt.poke(self.pid, addr, (word & ~0xFF) | 0xCC)

    def _remove(self, addr: int) -> None:
        if addr not in self.bps:
            return
        self.pt.poke(self.pid, addr, self.bps.pop(addr))

    def set_breakpoints(self, addrs: list[int]) -> list[int]:
        """**重新落地**：先撤掉所有已落地的，再装新的。

        不这么做的话，被换掉的那些字节会留在那里 —— 程序下一步就被自己的断点咬住。
        """
        for a in list(self.bps):
            self._remove(a)
        self.pending = set(addrs)
        for a in addrs:
            self._insert(a)
        return addrs

    # ---- 走
    def _here(self) -> int:
        """此刻停顿处的地址。断点停下时 `rip` 指向断点**后一格**，所以减一。"""
        if self.stop_addr is not None:
            return self.stop_addr
        rip = self.pt.getregs(self.pid).rip
        return rip - 1 if (rip - 1) in self.bps else rip

    def _bp_addr(self) -> int | None:
        """停在一条断点上吗？是就给出那条断点的地址。

        两副面孔都要认，因为它们对应两件事：

        * `stop_addr` 就在断点上 —— `int3` **已经响过**，那条真指令还没执行
          （`wait_stop` 的 breakpoint 分支）；
        * `rip - 1` 在断点上 —— 没走 `wait_stop`（例如刚 `_insert` 完）时的兜底。

        **单步正好踩在一条断点的地址上，也算"停在那儿"** —— 这是有意的：单步不
        触发 `int3`（那条指令还没执行），可用户看到的就是"停在这一行"，此时继续
        本来就该执行它、而不是再报一次命中。断点在这之后会被装回去（见
        `_leave_breakpoint`），所以循环里下一轮照样会响。
        """
        if self.stop_addr is not None and self.stop_addr in self.bps:
            return self.stop_addr
        rip = self.pt.getregs(self.pid).rip
        return rip - 1 if (rip - 1) in self.bps else None

    def _line_here(self) -> int:
        hit = self.lines.at(self._here())
        return hit[1] if hit else -1

    def _step1(self) -> bool:
        """单步一条指令。返回"进程是不是就此结束了"。"""
        self.pt.step(self.pid)
        return self._reap_step()

    def _reap_step(self) -> bool:
        """收掉刚发出的那次单步的停。返回"进程是不是就此结束了"。

        **单步之后进程已经停住了** —— 所以答案记进 `pre_stop`，不是再等一次。
        """
        pid, status = os.waitpid(self.pid, 0)
        if os.WIFEXITED(status) or os.WIFSIGNALED(status):
            self.pre_stop = (0, "exit")
            return True
        sig = os.WSTOPSIG(status)
        self.stop_addr = self.pt.getregs(self.pid).rip
        self.pre_stop = (sig, "step")
        return False

    def _leave_breakpoint(self) -> None:
        """要从断点上往前走时：把那条真指令**执行掉**，再把断点装回去。

        顺序不能反 —— 先装回去再单步，取到的就是 `0xCC` 那一个字节。

        不撤干净会怎样，实测过：`rip` 停在 `0xCC` 上，一继续又咬自己一次，
        于是"continue 永远停在同一个断点"（表现像死循环，但它每次都真的停）。
        """
        addr = self._bp_addr()
        self.stop_addr = None
        if addr is None:
            return
        self._remove(addr)
        regs = self.pt.getregs(self.pid)
        regs.rip = addr
        self.pt.setregs(self.pid, regs)
        self._step1()
        if self.pre_stop is not None and self.pre_stop[1] == "exit":
            return
        if addr in self.pending:                   # 用户的点装回去，不然它再不响
            self._insert(addr)

    def _resume(self) -> None:
        """继续跑（`continue`）。先把当前那条断点处理干净，见 `_leave_breakpoint`。"""
        self._leave_breakpoint()
        if self.pre_stop is not None:
            if self.pre_stop[1] == "exit":
                return                             # 那一步就把进程走完了
            self.pre_stop = None                   # 那一步的记录作废 —— 我们要继续跑
        self.pt.cont(self.pid)

    # ---- 按行走（`stepIn` / `next` / `stepOut`）
    #
    # **一行不是一条指令**：`user_hello.lomt:22` 那句 `write_str(1, msg)` 编译成
    # `movq/movq/movl/callq` 四条。只单步一条，行号没变 —— 用户按 F11 看到指针纹丝不动，
    # 会以为调试器坏了。所以三个动作都走"单步到行（或函数）变了为止"，
    # 上限 `_STEP_CAP` 条；走满了就**停在原地报出来**，不假装。

    def step_line(self) -> None:
        """`stepIn`：走到**源行变了**为止（会走进被调函数）。"""
        start = self._line_here()
        self._leave_breakpoint()
        if self.pre_stop is not None and self.pre_stop[1] == "exit":
            return
        if self.pre_stop is not None and self._line_here() != start:
            return                                 # 那一步就换行了
        for _ in range(_STEP_CAP):
            if self._step1() or self._line_here() != start:
                return

    def step_over(self) -> None:
        """`next`：走过一整行，**跨过 call** —— 进了别的函数就等它回来。

        不能用"在别的源行上设临时断点"那一招：被调函数的第一行也是"别的源行"，
        于是 `next` 和 `stepIn` 变成一回事。
        """
        start_line = self._line_here()
        start_fn = self.lines.fn_at(self._here())
        self._leave_breakpoint()
        if self.pre_stop is not None and self.pre_stop[1] == "exit":
            return
        if self._line_here() != start_line:
            return
        for _ in range(_STEP_CAP):
            if self._step1():
                return
            if self._line_here() != start_line and \
                    self.lines.fn_at(self._here()) == start_fn:
                return                             # 换行了，而且没停在别人的函数里

    def step_out(self) -> None:
        """`stepOut`：走到**函数变了**为止（回到调用者）。

        真做法是读返回地址、在那儿设临时断点；那要能可靠地找到帧，这里没有帧信息。
        于是老实单步到函数变了为止 —— 走满上限就停在原地，不假装已经出来了。
        """
        start_fn = self.lines.fn_at(self._here())
        self._leave_breakpoint()
        if self.pre_stop is not None and self.pre_stop[1] == "exit":
            return
        if self.lines.fn_at(self._here()) != start_fn:
            return
        for _ in range(_STEP_CAP):
            if self._step1() or self.lines.fn_at(self._here()) != start_fn:
                return

    def cont(self) -> None:
        self._resume()

    def wait_stop(self) -> tuple[int, str]:
        """等下一次停。返回 `(signal, 停的理由)`；理由 ∈ breakpoint/temp/entry/step/exit。

        `_resume` 里那次单步已经停住了，答案在 `pre_stop` 里 —— 直接交出去，
        不然这里会**再等一次**，而进程正停着不动（表现是"单步按下没反应"）。
        """
        if self.pre_stop is not None:
            sig, why = self.pre_stop
            self.pre_stop = None
            return (sig, why)
        while True:
            pid, status = os.waitpid(self.pid, 0)
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                self.stop_addr = None
                return (0, "exit")
            sig = os.WSTOPSIG(status)
            rip = self.pt.getregs(self.pid).rip
            # int3 停下时 `rip` 指向断点后面一格 —— 所以要减一才回到断点地址。
            # **不在这里拨 `rip`**：拨了 `_resume` 就得知道"是我拨的"（见 `_bp_addr`）。
            if sig == 5 and (rip - 1) in self.bps:
                self.stop_addr = rip - 1
                return (sig, "breakpoint")
            self.stop_addr = rip
            if sig == 5:
                return (sig, "step")
            # 别的信号（SIGSEGV 之类）交回给程序 —— 除非我们想拦它
            return (sig, "signal")

    def step_in(self) -> None:
        self.step_line()

    def regs_named(self) -> list[tuple[str, int]]:
        r = self.pt.getregs(self.pid)
        return [(n, getattr(r, n)) for n, _ in _Regs._fields_]

    def read_mem(self, addr: int, n: int) -> bytes:
        out = b""
        while len(out) < n:
            out += self.pt.peek(self.pid, addr + len(out)).to_bytes(8, "little")
        return out[:n]


# ---------------------------------------------------------------- 适配器


class Adapter:
    def __init__(self) -> None:
        self.dap = Dap(sys.stdin.buffer, sys.stdout.buffer)
        self.root = Path(__file__).resolve().parent.parent
        self.target: Target | None = None
        self.lines = LineTable()
        self.elf: Path | None = None
        self.program: str | None = None
        self.entry = False
        self._win = self._wslpath_w()

    # ---- 路径：Windows <-> WSL
    def _wslpath_w(self):
        def conv(p: str) -> str:
            r = subprocess.run(["wslpath", "-w", p], capture_output=True, text=True)
            return r.stdout.strip() or p
        return conv

    @staticmethod
    def _norm(p: str) -> str:
        """行表里的路径归一成 `/mnt/d/...` 形式，好与 VS Code 传来的路径比。

        DWARF 里存的是**绝对 Windows 路径**（`D:/Dev/...`，见 `lomentc._difile`），
        而 VS Code 给的 `source.path` 也是 Windows 形式 —— 两边都用这一条归一，就不再
        靠拼串去猜。
        """
        s = p.replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/(.*)$", s)
        if m:
            return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
        return s

    # ---- 构建
    def build(self, program: str) -> tuple[Path, LineTable]:
        sys.path.insert(0, str(self.root / "tools"))
        import lomentc                                        # noqa: PLC0415
        clang = _first_exe(_CLANG_CANDIDATES)
        objdump = _first_exe(_OBJDUMP_CANDIDATES)
        if not clang or not objdump:
            raise RuntimeError(
                "找不到 Windows 侧的 clang / llvm-objdump"
                f"（找过 {'、'.join(_CLANG_CANDIDATES)}）。"
                " 调试要它们出带 DWARF 的目标文件；装 LLVM 之后重启 VS Code。")
        # **路径两副面孔**：VS Code 给的是 **Windows 路径**（它那边的文档 URI），
        # 而本进程在 **WSL** 里 —— 读文件要用 `/mnt/d/...`。两边都由 `_norm` 归一，
        # 所以不靠拼串去猜。
        p = Path(self._norm(program))
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, self.root, p.parent, entry=p)
        ir = lomentc.emit_llvm(mod, self.root, deps, debug=True)
        td = Path(tempfile.mkdtemp(prefix="loment-dbg-"))
        (td / "m.ll").write_text(ir, encoding="utf-8")
        tgt = ["--target=x86_64-unknown-linux-gnu"]
        for args, out in (
            ([*tgt, "-ffreestanding", "-g", "-c", "m.ll", "-o", "m.o"], "m.o"),
            ([*tgt, "-nostdlib", "-ffreestanding", "-static", "-fuse-ld=lld",
              "-g", "m.o", "-o", "m.elf"], "m.elf"),
        ):
            r = subprocess.run([clang, *[self._win(a) if a.endswith((".ll", ".o", ".elf"))
                                         else a for a in args]],
                               cwd=str(td), capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"编译失败: {r.stderr.strip()[-400:]}")
        elf = td / "m.elf"
        dump = subprocess.run([objdump, "-d", "-l", self._win(str(elf))],
                              capture_output=True, text=True).stdout
        return elf, LineTable.from_objdump(dump, self._norm)

    # ---- 请求
    def handle(self, req: dict) -> bool:
        cmd = req.get("command", "")
        if cmd == "initialize":
            self.dap.respond(req, {
                "supportsConfigurationDoneRequest": True,
                "supportsSetVariable": False,
                "supportsConditionalBreakpoints": False,
                # **不支持的都报 false** —— 报 true 而没实现，用户点下去就是"没反应"。
                "supportsFunctionBreakpoints": False,
                "supportsEvaluateForHovers": False,
                "exceptionBreakpointFilters": [],
            })
            self.dap.event("initialized")
            return True
        if cmd == "launch":
            try:
                self.do_launch(req.get("arguments") or {})
                self.dap.respond(req)
            except Exception as e:                            # noqa: BLE001
                self.dap.respond(req, ok=False, message=str(e))
                self.dap.event("terminated")
            return True
        if cmd == "setBreakpoints":
            self.do_set_breakpoints(req)
            return True
        if cmd == "configurationDone":
            self.dap.respond(req)
            if not self.target:
                return True
            # **到这儿才放它跑**。顺序不能反：VS Code 的次序是
            # `launch -> setBreakpoints -> setExceptionBreakpoints -> configurationDone`
            # ——在 `launch` 里就继续的话，等它来设断点时进程已经在跑（甚至跑完了），
            # 于是 poke 到一个不存在的进程（实测 `[Errno 3] No such process`）。
            if self.entry:
                self.dap.event("stopped", {"reason": "entry", "threadId": 1,
                                           "allThreadsStopped": True})
            else:
                self.target.cont()
                self._report_stop()
            return True
        if cmd in ("continue", "next", "stepIn", "stepOut"):
            self.do_resume(req, cmd)
            return True
        if cmd == "threads":
            self.dap.respond(req, {"threads": [{"id": 1, "name": "main"}]})
            return True
        if cmd == "stackTrace":
            self.do_stack(req)
            return True
        if cmd == "scopes":
            self.dap.respond(req, {"scopes": [
                {"name": "Registers", "variablesReference": 1, "expensive": False}]})
            return True
        if cmd == "variables":
            self.do_variables(req)
            return True
        if cmd == "disconnect" or cmd == "terminate":
            self.do_kill()
            self.dap.respond(req)
            self.dap.event("terminated")
            return False
        if cmd in ("source", "setExceptionBreakpoints", "setFunctionBreakpoints",
                   "loadedSources", "modules"):
            self.dap.respond(req, {})
            return True
        self.dap.respond(req, ok=False, message=f"不支持的请求: {cmd}")
        return True

    def do_launch(self, a: dict) -> None:
        program = a.get("program")
        if not program:
            raise RuntimeError("launch 少了 `program`")
        self.program = program
        elf, lines = self.build(program)
        # **拷到 Linux 原生路径再执行** —— DrvFs(`/mnt/...`) 上不能直接 exec。
        run = Path("/tmp") / f"loment-dbg-{os.getpid()}"
        run.write_bytes(elf.read_bytes())
        run.chmod(0o755)
        self.elf = run
        self.lines = lines
        self.entry = bool(a.get("stopOnEntry", False))
        self.target = Target(run, [str(run)] + [str(x) for x in (a.get("args") or [])],
                             os.getcwd(), self._output)
        self.target.lines = lines
        self.target.launch()
        # **不在这里继续** —— 见 `configurationDone` 那条注释（时序）。

    def _output(self, text: str) -> None:
        self.dap.event("output", {"category": "stdout", "output": text})

    def do_set_breakpoints(self, req: dict) -> None:
        a = req.get("arguments") or {}
        src = self._norm((a.get("source") or {}).get("path", ""))
        out = []
        addrs = []
        for bp in a.get("breakpoints") or []:
            line = int(bp.get("line", 0))
            hits = self.lines.addrs_for(src, line)
            if hits:
                addrs.append(hits[0])
                out.append({"verified": True, "line": line})
            else:
                # **没落上的必须说** —— 一个"绿点但不生效"的断点比没有更坏。
                out.append({"verified": False, "line": line,
                            "message": f"{src}:{line} 没有对应的指令（行表里没有这一行）"})
        if self.target:
            self.target.set_breakpoints(addrs)
        self.dap.respond(req, {"breakpoints": out})

    def do_resume(self, req: dict, cmd: str) -> None:
        if not self.target:
            self.dap.respond(req, ok=False, message="还没 launch")
            return
        t = self.target
        try:
            if cmd == "continue":
                t.cont()
            elif cmd == "next":
                t.step_over()
            elif cmd == "stepOut":
                t.step_out()
            else:
                t.step_in()
        except OSError as e:
            self.dap.respond(req, ok=False, message=str(e))
            return
        self.dap.respond(req, {"allThreadsContinued": True} if cmd == "continue" else {})
        self._report_stop()

    def _report_stop(self) -> None:
        """等下一次停，把那件事报成 DAP 事件。**`configurationDone` 与 `do_resume` 共用**。"""
        t = self.target
        assert t is not None
        sig, why = t.wait_stop()
        if why == "exit":
            self.dap.event("exited", {"exitCode": 0})
            self.dap.event("terminated")
            return
        if why == "signal":
            self.dap.event("output", {"category": "stderr",
                                      "output": f"[loment] 收到信号 {sig}（可能是段错误）\n"})
        reason = {"breakpoint": "breakpoint", "step": "step",
                  "signal": "exception"}.get(why, "step")
        self.dap.event("stopped", {"reason": reason, "threadId": 1,
                                   "allThreadsStopped": True,
                                   "description": f"signal {sig}" if why == "signal" else ""})

    def _frame(self) -> dict:
        t = self.target
        assert t is not None
        addr = t._here()
        hit = self.lines.at(addr)
        if hit is None:
            return {"id": 1, "name": "?", "line": 1, "column": 1,
                    "source": {"path": self.program or ""}}
        f, l = hit
        # 帧名是**函数名**（调用栈里要看的就是它）；文件名在 `source.name` 里，不重复。
        return {"id": 1, "name": self.lines.fn_at(addr), "line": l, "column": 1,
                "source": {"name": Path(f).name, "path": self._back_to_win(f)}}

    @staticmethod
    def _back_to_win(p: str) -> str:
        """`/mnt/d/...` -> `D:/...`（VS Code 那边要 Windows 形式才能对上打开的文档）。"""
        m = re.match(r"^/mnt/([a-z])/(.*)$", p)
        return f"{m.group(1).upper()}:/{m.group(2)}" if m else p

    def do_stack(self, req: dict) -> None:
        if not self.target:
            self.dap.respond(req, {"stackFrames": [], "totalFrames": 0})
            return
        self.dap.respond(req, {"stackFrames": [self._frame()], "totalFrames": 1})

    def do_variables(self, req: dict) -> None:
        if not self.target:
            self.dap.respond(req, {"variables": []})
            return
        vars_ = [{"name": n, "value": f"0x{v:x}", "variablesReference": 0}
                 for n, v in self.target.regs_named()]
        self.dap.respond(req, {"variables": vars_})

    def do_kill(self) -> None:
        if self.target and self.target.pid:
            try:
                os.kill(self.target.pid, 9)
                os.waitpid(self.target.pid, 0)
            except OSError:
                pass

    def run(self) -> int:
        while True:
            req = self.dap.read()
            if req is None:
                break
            if req.get("type") != "request":
                continue
            if not self.handle(req):
                break
        self.do_kill()
        return 0


def main() -> int:
    try:
        return Adapter().run()
    except Exception as e:                                    # noqa: BLE001
        print(f"[loment_dap] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
