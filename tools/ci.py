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
                 "loment_tools_test", "loment_p7_test", "loment_p8_test", "loment_p9_test",
                 "loment_rule_parity", "loment_seed_test", "loment_fmt_test", "loment_doc_test",
                 "loment_json_test", "loment_pkg_test", "loment_lomc_test", "loment_lsp_test",
                 "loment_elf_test", "loment_pe_test", "loment_genesis_test", "loment_status_test",
                 # FFI 第 1 阶段 (docs/173): 外部目标文件 + C ABI + lomelf 单独链接, 跑出结果
                 "loment_ffi_test",
                 "loment_rel_test",
                 "loment_editors_test",
                 "vscode_ext_test", "loment_filetype_test", "loment_status", "loment_release", "loment_manual",
                 "fuai_contract_check", "loment_eol", "loment_dist_test",
                 "loment_sign_test", "loment_src", "loment_lib_test", "loment_cli_test",
                 # std 核的行为判据 (docs/180): lib/ 里的函数算得对不对
                 "loment_std_test",
                 "loment_lompi_test", "lompi_sync", "loment_publish",
                 # 多语法前端 (docs/179, docs/175 §6 第 4 条): 外源源码 -> 接口单元
                 # -> L1 调用 -> 链外部目标文件 -> 跑出预期退出码
                 "loment_multisyntax_test")


def run_static():
    """进程内调用各检查脚本的 main(), 不经 shell/子进程。返回 [(name, ok, tail)]。"""
    import contextlib
    import io

    out = []
    for name in STATIC_CHECKS:
        buf = io.StringIO()
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
        tail = buf.getvalue().strip().splitlines()
        out.append((name, rc == 0, tail[-1] if tail else ""))
    return out


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
    a = ap.parse_args()

    static = run_static()
    for name, ok, tail in static:
        print(f":: [static] {name:14s} {'PASS' if ok else 'FAIL'}  {tail}", flush=True)
    if any(not ok for _, ok, _ in static):
        print("-" * 60)
        print("fujoci: 静态门禁失败 — 中止 (接口漂移优先于运行时回归)")
        return 1
    if a.static_only:
        print("-" * 60)
        print(f"fujoci: 静态门禁 {sum(1 for _, ok, _ in static if ok)}/{len(static)} PASS")
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
