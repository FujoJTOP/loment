#!/usr/bin/env python3
"""fujoci — QEMU 无头启动 + 日志断言自动化 (M78)

CI 流水线:
  1) cargo build --release (kernel)
  2) flatten (--pad 0x1A0000)
  3) 用例矩阵: 兼容矩阵 (fujoregress 9) + 里程碑 demo (m61..m77 抽样,
     每个 initrd → QEMU 无头 → 注入 'os run hermes' → 日志断言
     'MXX RESULT: PASS' 或专用关键字)
  4) 报告: 控制台 + JSON (--json out.json); 退出码 = 全 PASS 0 / 有 FAIL 1

用法: python tools/ci.py [--only IX] [--json out.json] [--fast]
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from _safepath import safe_open

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KERNEL = os.path.join(ROOT, "kernel", "fujo-kernel.bin")
# 端口可覆盖: 同机并行跑多个 agent 时避免互抢 (FUJO_MON_PORT / FUJO_SER_PORT)
MON_PORT = int(os.environ.get("FUJO_MON_PORT", "4568"))
SER_PORT = int(os.environ.get("FUJO_SER_PORT", "4001"))
KEYS = ["o", "s", "spc", "r", "u", "n", "spc", "h", "e", "r", "m", "e", "s", "ret"]

# 兼容矩阵 (9) —— 复用 fujoregress 的断言面
COMPAT = [
    ("elf-linux", "sdk/linux/m30_linux.elf", "M30 RESULT: PASS", 14.0),
    ("elf-linux2", "sdk/linux/m33_trace.elf", "M33 RESULT: PASS", 14.0),
    ("run-fujopack", "sdk/build/m31_res.run", "M31 RESULT: PASS", 14.0),
    ("multi-fujorun", "sdk/build/m32_multi.initrd", "M32 RESULT: PASS", 14.0),
    ("macho-darwin", "sdk/mac/m29_darwin.macho", "M29 RESULT: PASS", 14.0),
    ("pe-m3", "sdk/win/hello_win.exe", "M3 verified", 14.0),
    ("pe-m26", "sdk/win/m26_win.exe", "M26 RESULT: PASS", 14.0),
    ("pe-m27", "sdk/win/m27_mingw.exe", "M27 RESULT: PASS", 14.0),
    ("pe-m30", "sdk/win/m30_win.exe", "M30 RESULT: PASS", 14.0),
]

# 里程碑 log 断言抽样 (Wave2-5 关注面)
MILESTONES = [
    ("m61-blit", "sdk/linux/m61_blit.elf", "M61 RESULT: PASS"),
    ("m62-shader", "sdk/linux/m62_shader.elf", "M62 RESULT: PASS"),
    ("m63-mix", "sdk/linux/m63_mix.elf", "M63 RESULT: PASS"),
    ("m64-smp", "sdk/linux/m64_smp.elf", "M64 RESULT: PASS"),
    ("m65-tss", "sdk/linux/m65_tss.elf", "M65 RESULT: PASS"),
    ("m66-pcache", "sdk/linux/m66_pcache.elf", "M66 RESULT: PASS"),
    ("m67-irq", "sdk/linux/m67_irq.elf", "M67 RESULT: PASS"),
    ("m68-perf", "sdk/linux/m68_perf.elf", "M68 RESULT: PASS"),
    ("m69-game2", "sdk/linux/m69_game2.elf", "M69 RESULT: PASS"),
    ("m71-asm", "sdk/linux/m71_asm.elf", "M71 RESULT: PASS"),
    ("m72-ld", "sdk/linux/m72_ld.elf", "M72 RESULT: PASS"),
    ("m73-edit", "sdk/linux/m73_edit.elf", "M73 RESULT: PASS"),
    ("m74-cc", "sdk/linux/m74_cc.elf", "M74 RESULT: PASS"),
    ("m75-dbg", "sdk/linux/m75_dbg.elf", "M75 RESULT: PASS"),
    ("m76-trace", "sdk/linux/m76_trace.elf", "M76 RESULT: PASS"),
    ("m77-win", "sdk/linux/m77_win.elf", "M77 RESULT: PASS"),
    ("m82-ut", "sdk/linux/m82_ut.elf", "M82 RESULT: PASS"),
    ("m83-leak", "sdk/linux/m83_leak.elf", "M83 RESULT: PASS"),
    ("m84-dump", "sdk/linux/m84_dump.elf", "M84 RESULT: PASS"),
    ("m86-wmap", "sdk/linux/m86_wmap.elf", "M86 RESULT: PASS"),
    ("m87-mcard", "sdk/linux/m87_mcard.elf", "M87 RESULT: PASS"),
    ("m88-sess", "sdk/linux/m88_sess.elf", "M88 RESULT: PASS"),
    ("m89-ctx", "sdk/linux/m89_ctx.elf", "M89 RESULT: PASS"),
    ("m90-cctx", "sdk/linux/m90_ctx.elf", "M90 RESULT: PASS"),
    ("m91-cap", "sdk/linux/m91_cap.elf", "M91 RESULT: PASS"),
    ("m92-route", "sdk/linux/m92_route.elf", "M92 RESULT: PASS"),
    ("m93-infer", "sdk/linux/m93_infer.elf", "M93 RESULT: PASS"),
    ("m94-fupm", "sdk/linux/m94_fupm.elf", "M94 RESULT: PASS"),
    ("m95-life", "sdk/linux/m95_life.elf", "M95 RESULT: PASS"),
]

# 非竞速用例 (键盘 sleep 快速, boot 慢): 全部用例 boot 7.5s + 输入 1.5s
BOOT_S = 7.5
KEY_HZ = 0.10

# L0/L1 静态门禁 (docs/141, docs/143): 先于 QEMU 矩阵, 失败即中止 — 接口漂移不该等到运行期。
STATIC_CHECKS = ("lomc_test", "lom_audit", "lomentc_test", "potato_test", "potato_cross",
                 # 编译期子集解释器 (S4.0, docs/184 §9)
                 "loment_ct_test",
                 # `comefor` 的 token 层展开 (S4.1, docs/184 §9)
                 "loment_comefor_test",
                 # 外部代码块的字节保真 (S1, docs/185)
                 "loment_extblock_test",
                 # 多语言程序 (docs/183 §8.2 的 S2 判据)
                 "loment_multilang_test",
                 # C 翻成 Loment: 翻译出来的跑出的数 == clang 编那份 C 跑出的数 (docs/186)
                 "loment_ctrans_test",
                 # Python 翻成 Loment: 跑出的数 == CPython 跑那份 Python 的数 (docs/187)
                 "loment_pytrans_test",
                 # Java 翻成 Loment: 跑出的数 == javac 编那份 Java 跑出的数 (docs/188 §7.1)
                 "loment_jtrans_test",
                 # C# 翻成 Loment: 跑出的数 == dotnet 编那份 C# 跑出的数 (docs/188 §7.1)
                 "loment_cstrans_test",
                 # C++ 翻成 Loment: 跑出的数 == g++ 编那份 C++ 跑出的数 (docs/188 §7.1)
                 "loment_cpptrans_test",
                 # Go 翻成 Loment: 跑出的数 == go build 那份 Go 跑出的数 (docs/188 §7.1)
                 "loment_gotrans_test",
                 # 自然语言写法 (docs/197): 同一个程序的两种拼法 -> **逐字节同一份** Loment,
                 # 而且翻出来的那一份真编真跑, 跑出的数 == 独立推出来的期望值
                 "loment_nltrans_test",
                 # Potato 形式对象 -> L1 接口单元 (docs/179) 的 Loment 版 (发射那一半)
                 "loment_lomtfrom_test",
                 # 花括号族语法树 -> Loment 源码 (docs/186/189 第十八格) 的 Loment 版
                 "loment_trans_test",
                 # 自举侧的 Potato v5 发射 (docs/189 第十九格): `stage1 --emit-potato`
                 # 与 `lomentc.emit_potato` **逐字节**比, 子集外点名拒绝
                 "loment_potato_emit_test",
                 # `choose write grammar`: 读法由声明决定、出厂锁的取值表、声明先抹掉
                 "loment_grammar_test",
                 "loment_tools_test", "loment_p7_test", "loment_p8_test", "loment_p9_test",
                 "loment_rule_parity", "loment_seed_test", "loment_fmt_test", "loment_doc_test",
                 "loment_json_test", "loment_pkg_test", "loment_lomc_test", "loment_lsp_test",
                 "loment_elf_test", "loment_pe_test", "loment_genesis_test", "loment_status_test",
                 # FFI 第 1 阶段 (docs/173): 外部目标文件 + C ABI + lomelf 单独链接, 跑出结果
                 "loment_ffi_test",
                 "loment_rel_test",
                 "loment_editors_test",
                 "vscode_ext_test", "loment_filetype_test", "loment_status", "loment_release", "loment_manual",
                 # 调试器 (docs/190): 无头 DAP 往返 —— 断点命中源行、单步按行走、落不上的要说
                 "loment_dap_test",
                 "fuai_contract_check", "loment_eol", "loment_dist_test",
                 # S1 第一格 (docs/189 §3): `lomeol.lomt` 与 `loment_eol.py` 逐字节相同
                 "loment_eol_test",
                 # S1 第二格: `lomsyscalls.lomt` 与 `loment_syscalls.py` 逐字节相同
                 "loment_syscalls_test",
                 # S1 第五格 (docs/189 §3): `lomcapasserts.lomt` 与 `potato_assert.py` 逐字节
                 # 相同（含 `--emit-rust` 的**落盘字节** —— 那一条抓到参考实现随宿主换行的问题）
                 "loment_capasserts_test",
                 "loment_sign_test", "loment_src", "loment_lib_test", "loment_cli_test",
                 # 报错器 (docs/182 §6/§8): 渲染的字段、两条报错通道、缺席时的兜底
                 "loment_err_test",
                 # std 核的行为判据 (docs/180): lib/ 里的函数算得对不对
                 "loment_std_test",
                 # LumtUI (docs/196): GUI 库的三把尺子 —— .fuc 与 lom/fuc.lom 逐字节、
                 # 布局/命中/焦点与独立推出的期望一致、字体与 FreeType 对
                 "loment_lumtui_test",
                 "loment_lompi_test", "lompi_sync", "loment_publish",
                 # 多语法前端 (docs/179, docs/175 §6 第 4 条): 外源源码 -> 接口单元
                 # -> L1 调用 -> 链外部目标文件 -> 跑出预期退出码
                 "loment_multisyntax_test")


#: **不能与别的检查同时跑**的那几条 —— 它们**写仓库里的共享位置**。
#: `lompi_sync` 会把正本同步进 `lompi/`(只在不等时写, 但"只在不等时"靠不住:
#: 一次脏树就够), 而好几条判据要读 `lompi/`。并行调度时这一组单独串行跑。
#: (`loment_dist_test` 用的 `loment/build/dist` 只有它自己碰, 不必独占。)
EXCLUSIVE_STATIC = ("lompi_sync",)


def _sweep_wsl_tmp() -> None:
    """清掉 WSL `/tmp` 里**旧的**门禁残留 (`/tmp/loment-*`) —— 尽力而为, 出错不报。

    **为什么会有残留**: 各判据的 WSL 临时文件名带**进程号** (并行时同名文件会互相踩,
    见那些模块里的 `_T`), 于是每次跑都留一批新名字, 不再被下次覆写。几轮下来能吃掉
    几十上百 MB 的 tmpfs。

    **为什么只清 60 分钟前的**: 同一个 `/tmp` 是**共享**的, 直接 `rm -f /tmp/loment-*`
    会把**另一台并发在跑的门禁**正在用的文件删掉 (本仓明确考虑过"同机并行多个 agent",
    见 `FUJO_MON_PORT`)。按时间清就没有这个口子。
    """
    try:
        subprocess.run(["wsl", "-e", "bash", "-lc",
                        "find /tmp -maxdepth 1 -name 'loment-*' -mmin +60 -delete "
                        "2>/dev/null || true"],
                       capture_output=True, timeout=120, shell=False)
    except Exception:                                                # noqa: BLE001
        pass


def _report(r: tuple[str, bool, str, float]) -> None:
    """一行结果。**只在一处打** —— 两个路径各打一次会变成打两遍 (上一版就是这么错的)。"""
    name, ok, tail, dt = r
    print(f":: [static] {name:14s} {'PASS' if ok else 'FAIL'}  {dt:6.1f}s  {tail}", flush=True)


def _run_one_check(name: str) -> tuple[str, bool, str, float]:
    """在**当前进程**里跑一条检查。`-j` 时它是进程池的工作单元 (所以必须在模块级)。"""
    import contextlib
    import io

    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        mod = __import__(name)
        old_argv, sys.argv = sys.argv, [name]  # 防 argparse 读 ci.py 的 argv
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = mod.main()
        finally:
            sys.argv = old_argv
    except BaseException as e:  # noqa: BLE001  (SystemExit/argparse 也算失败)
        rc = 1
        buf = io.StringIO(f"{type(e).__name__}: {e}")
    dt = time.perf_counter() - t0
    tail = buf.getvalue().strip().splitlines()
    return name, rc == 0, (tail[-1] if tail else ""), dt


def run_static(only: tuple[str, ...] = (), jobs: int = 1):
    """跑各检查脚本的 main()。返回 [(name, ok, tail, dt)], **顺序 = STATIC_CHECKS 顺序**。

    **每条都计时**: 不看这个数就没法谈"门禁太慢"是慢在哪 —— 41 条里往往几条吃掉大半。
    计时结果按耗时排序打出来 (见 `main`), 于是"该优化哪条"是个实测结论而不是猜。

    `only` 非空时只跑名字里含这些子串的那几条 —— 优化门禁时得能单独把一条跑起来看。

    **`jobs > 1` 时并行跑**。41 条彼此独立, 串行跑纯是浪费; 墙钟时间从"和"变成
    "最慢的那条"。两条前提:
      * 每条自己在**独立进程**里跑, 所以某条的全局状态 (`sys.argv`、模块级缓存) 不会
        串到别条上 —— 这是并行**更安全**的一面, 不只是更快;
      * 但 WSL 的 `/tmp` 与仓库是**共享**的, 所以 (a) 各模块的 WSL 临时文件名都带了
        进程号 (见那些模块里的 `_T`), (b) `EXCLUSIVE_STATIC` 里那几条独占着跑。
    """
    names = [n for n in STATIC_CHECKS if not only or any(o in n for o in only)]
    if only:
        print(f"fujoci: --only-static 只跑 {len(names)}/{len(STATIC_CHECKS)} 条", flush=True)

    if jobs <= 1 or len(names) <= 1:
        out = []
        for n in names:
            r = _run_one_check(n)
            _report(r)
            out.append(r)
        return out

    import concurrent.futures as cf

    res: dict[str, tuple] = {}
    excl = [n for n in names if any(e in n for e in EXCLUSIVE_STATIC)]
    par = [n for n in names if n not in excl]
    if excl:
        print(f"fujoci: 独占跑的 {len(excl)} 条: {' '.join(excl)}", flush=True)
        for n in excl:
            res[n] = _run_one_check(n)
            _report(res[n])
    print(f"fujoci: 并行 {len(par)} 条, {jobs} 路", flush=True)
    with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(_run_one_check, n): n for n in par}
        for f in cf.as_completed(futs):
            r = f.result()
            res[r[0]] = r
            _report(r)              # **谁先完先打** —— 长跑时看得见进度
    return [res[n] for n in names]  # 返回值仍按 STATIC_CHECKS 顺序, 便于比对


def kill_qemu():
    subprocess.run(["taskkill", "/F", "/IM", "qemu-system-x86_64.exe"],
                   capture_output=True)


def run_one(kernel, rel, needle, timeout_s):
    initrd = os.path.join(ROOT, rel)
    if not os.path.exists(initrd):
        return ("MISS", f"initrd not found: {initrd}", "")
    kill_qemu()
    time.sleep(0.8)
    tmpd = tempfile.mkdtemp(prefix="fujoci-")
    log = os.path.join(tmpd, "qemu.log")
    p = subprocess.Popen([
        shutil.which("qemu-system-x86_64"), "-m", "256M",
        "-kernel", kernel, "-initrd", initrd,
        "-serial", f"file:{log}",
        "-serial", f"tcp:127.0.0.1:{SER_PORT},server=on,wait=off",
        "-monitor", f"telnet:127.0.0.1:{MON_PORT},server,nowait",
        "-display", "none", "-no-reboot",
    ])
    time.sleep(BOOT_S)
    try:
        s = socket.create_connection(("127.0.0.1", MON_PORT), timeout=3)
        f = s.makefile("w")
        for k in KEYS:
            f.write(f"sendkey {k}\n")
            f.flush()
            time.sleep(KEY_HZ)
        s.close()
    except OSError:
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if p.poll() is not None:
                break
        except Exception:
            break
        time.sleep(0.5)
    time.sleep(1.0)
    try:
        p.kill()
    except Exception:
        pass
    log_txt = ""
    if os.path.exists(log):
        log_txt = open(log, errors="replace").read()
    if needle in log_txt:
        return ("PASS", "", log_txt)
    tail = "\n".join(log_txt.splitlines()[-6:])
    return ("FAIL", f"needle '{needle}' not found", tail)


def main():
    ap = argparse.ArgumentParser(prog="fujoci")
    ap.add_argument("-k", "--kernel", default=KERNEL)
    ap.add_argument("--only", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--static-only", action="store_true",
                    help="只跑 L0 静态门禁 (lomc_test / lom_audit / fuai_contract), 不启 QEMU")
    ap.add_argument("--only-static", action="append", default=[], metavar="SUBSTR",
                    help="只跑静态门禁里名字含 SUBSTR 的那几条 (可重复) —— 优化门禁用")
    default_jobs = max(1, min(8, (os.cpu_count() or 4)))
    ap.add_argument("-j", "--jobs", type=int, default=default_jobs,
                    help=f"静态门禁并行度 (默认 {default_jobs}; 1 = 串行同进程)")
    a = ap.parse_args()

    if a.static_only:
        _sweep_wsl_tmp()
    wall0 = time.perf_counter()
    static = run_static(tuple(a.only_static), a.jobs)     # 每条结果它自己打 (见 _report)
    wall = time.perf_counter() - wall0
    # **耗时榜**: 优化门禁只能优化"实测最慢的那几条"。41 条里往往 5 条吃掉八成 ——
    # 没有这张榜，"哪条慢"就永远是个印象 (而印象通常错)。
    # **"总和"与"墙钟"分开报**: 并行之后这两个数不是一回事, 而"还能不能再快"看的是墙钟
    # 与**最慢那条**的差 —— 差越小, 说明并行已经榨干, 该去优化那条本身了。
    slow_total = sum(dt for *_x, dt in static)
    slowest = max((dt for *_x, dt in static), default=0.0)
    print("-" * 60)
    print(f"fujoci: 静态门禁 墙钟 {wall:.1f}s / 各条合计 {slow_total:.1f}s "
          f"/ 最慢一条 {slowest:.1f}s（-j {a.jobs}）")
    print("  最慢的 8 条：")
    for name, _ok, _tail, dt in sorted(static, key=lambda r: -r[3])[:8]:
        pct = (dt / slow_total * 100) if slow_total else 0
        print(f"    {name:24s} {dt:7.1f}s  {pct:4.1f}%")
    if any(not ok for _, ok, _t, _d in static):
        print("-" * 60)
        print("fujoci: 静态门禁失败 — 中止 (接口漂移优先于运行时回归)")
        return 1
    if a.static_only:
        print("-" * 60)
        print(f"fujoci: 静态门禁 {sum(1 for _, ok, _t, _d in static if ok)}/{len(static)} PASS")
        return 0

    cases = []
    for name, rel, needle, to in COMPAT:
        cases.append((name, rel, needle, to))
    for name, rel, needle in MILESTONES:
        cases.append((name, rel, needle, a.timeout))

    results = []
    for i, (name, rel, needle, to) in enumerate(cases):
        if a.only is not None and i != a.only:
            continue
        print(f":: [{i:02d}] {name:16s} ...", flush=True)
        st, err, logtxt = run_one(a.kernel, rel, needle, to)
        print(f":: [{i:02d}] {name:16s} {st} {err}", flush=True)
        if st != "PASS" and logtxt:
            print(logtxt)
        results.append({"case": name, "rel": rel, "status": st, "needle": needle})
    ok = sum(1 for r in results if r["status"] == "PASS")
    print("-" * 60)
    print(f"fujoci: {ok}/{len(results)} PASS")
    if a.json:
        json.dump(results, safe_open(a.json, "w"), indent=1)
    return 0 if ok == len(results) and len(results) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
