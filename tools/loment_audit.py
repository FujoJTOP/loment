#!/usr/bin/env python3
# loment_audit.py — 审计包 (M100, docs/160)
#
# 给**第三方**复核对 Loment 0.1.3.4 Alpha 的主张: 一条命令跑完全部判据, 打印/落盘一份
# 可附在审计报告后的证据 (含版本、提交、工件哈希、每条主张的结果)。
#
#   python tools/loment_audit.py            # 跑全部, 人类可读
#   python tools/loment_audit.py --json     # 落盘 loment/build/audit-report.json
#   python tools/loment_audit.py --list     # 只列主张与对应命令 (不跑)
#
# 设计原则: 这里**只调用已有的门禁**, 不新写判据 —— 审计工具自己长成第二套判据是
# 审计里最常见的坑。退出码: 0 = 全部通过 / 1 = 有红 / 2 = 用法错误。

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_release  # noqa: E402  (版本名取它的 RELEASE: 单一真源)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "build" / "audit-report.json"

#: 主张 -> (跑它的工具模块, 传给 main 的参数)。工具名必须出现在 tools/ci.py 的
#: STATIC_CHECKS 里 (loment_tools_test::test_audit_claims_match_ci 会核这条,
#: 防止审计工具偷偷跑别的东西)。
CLAIMS: list[tuple[str, str, str, list[str]]] = [
    ("C1", "自举 checker 与参考实现规则等价 (棘轮门禁, 假阳性/漂移必须为 0)",
     "loment_rule_parity", []),
    ("C2", "两个后端逐字节等价 + 三阶段自举定点 + 40/40 语料零诊断 + driver 负例被拒",
     "loment_p8_test", []),
    ("C3", "参考实现自身的 91 条判据 (含 Rust/IR/原生后端形状)",
     "lomentc_test", []),
    ("C4", "工具侧: 诊断分类完整 / 内建表一致 / 增量缓存等 15 条",
     "loment_tools_test", []),
    ("C5", "无 Python 自举: 种子与参考逐字符一致 + 启动脚本无解释器 + 定点",
     "loment_seed_test", []),
    ("C6", "Loment 版格式化器与 Python 版逐字节相同 (42 语料 + 边界 + 幂等)",
     "loment_fmt_test", []),
    ("C7", "示例集/交叉编译 (25 个示例 + aarch64 目标发射)",
     "loment_p9_test", []),
    ("C8", "VS Code 宿主与 LSP 往返 (无头验收)",
     "vscode_ext_test", []),
    ("C9", "发布工件 sha256 可复现",
     "loment_release", []),
    ("C10", "状态矩阵与 docs/145 一致 (账本不是手改的)",
     "loment_status", []),
    ("C11", "LSP 去 Python: Loment 版语言服务 (补全/跳转/诊断/--check) 与工具等价性",
     "loment_lsp_test", []),
    ("C12", "工具链等价性: 格式化器与文档生成器与 Python 版逐字节相同",
     "loment_doc_test", []),
    ("C13", "JSON 库与 Python json 逐字节一致 (LSP 的 JSON-RPC 靠它)",
     "loment_json_test", []),
    ("C14", "Windows 文件类型注册 (.lomt/.lom 常驻打开方式) 与启动脚本无解释器",
     "loment_filetype_test", []),
    ("C15", "发行包: 命令安装 (sh/ps1) 与自解压安装包, 装出来的编译器产物与参考逐字节相同",
     "loment_dist_test", []),
    ("C16", "发行包签名: Authenticode (发布者可读/篡改可验) + SHA256SUMS 分离签名",
     "loment_sign_test", []),
]


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, shell=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _clang_path() -> str | None:
    import shutil
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


def provenance() -> dict:
    cc = _clang_path()
    ver = ""
    if cc:
        try:
            ver = (subprocess.run([cc, "--version"], capture_output=True, text=True,
                                  shell=False).stdout.splitlines() or [""])[0]
        except Exception:  # noqa: BLE001
            ver = ""
    return {
        "commit": _git("rev-parse", "HEAD"),
        "describe": _git("describe", "--tags", "--always"),
        "tag": _git("tag", "--points-at", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "clang": ver or "(没找到 clang —— 需要它的判据会 SKIP)",
        "python": sys.version.split()[0],
    }


def run_claim(module_name: str, argv: list[str]) -> tuple[bool, str]:
    """进程内调用工具 main() —— 与 ci.py 同一套调用方式 (不经 shell)。"""
    tail = ""
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:  # noqa: BLE001
        return False, f"import 失败: {type(e).__name__}: {e}"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            fn = getattr(mod, "main", None)
            rc = int(fn(argv)) if fn is not None and _accepts_argv(fn) else _main_noargs(mod)
    except SystemExit as e:  # 工具用 sys.exit(main())
        rc = int(e.code or 0)
    except Exception as e:  # noqa: BLE001
        rc, tail = 1, f"{type(e).__name__}: {e}"
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    if lines:
        # 红的时候别只留最后一行: 把失败/差异/错误行挑出来, 报告里才有得归因
        bad = [ln for ln in lines
               if "FAIL" in ln or "DIFF" in ln or "STALE" in ln or ln.lstrip().startswith("[ERR]")]
        tail = (bad[-1] if bad else lines[-1])[:200]
    return rc == 0, tail


def _accepts_argv(fn) -> bool:
    import inspect
    try:
        return len(inspect.signature(fn).parameters) >= 1
    except Exception:  # noqa: BLE001
        return True


def _main_noargs(mod) -> int:
    return int(mod.main())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_audit", description="审计包 (docs/160)")
    ap.add_argument("--json", action="store_true", help="落盘 loment/build/audit-report.json")
    ap.add_argument("--list", action="store_true", help="只列主张")
    a = ap.parse_args(argv)

    if a.list:
        for cid, what, tool, args in CLAIMS:
            print(f"{cid}  {what}\n      -> python tools/{tool}.py {' '.join(args)}")
        return 0

    prov = provenance()
    print("Loment 审计包 (docs/160) —— 只调用已有门禁, 不新增判据")
    print(f"  提交 {prov['commit'][:12]}  {prov['describe']}"
          f"{' [工作区脏]' if prov['dirty'] else ''}")
    print(f"  {prov['clang']}  ·  python {prov['python']}")
    print(f"  tag: {prov['tag'] or '(HEAD 上没有 tag)'}")
    print()

    results = []
    t0 = time.time()
    for cid, what, tool, args in CLAIMS:
        t = time.time()
        ok, tail = run_claim(tool, args)
        results.append({"id": cid, "claim": what, "tool": f"tools/{tool}.py",
                        "argv": args, "ok": ok, "detail": tail,
                        "seconds": round(time.time() - t, 1)})
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid} {what}")
        if tail:
            print(f"         {tail}")

    bad = [r for r in results if not r["ok"]]
    report = {
        "release": loment_release.RELEASE,  # 单一真源: 别再抄一遍字面量
        "provenance": prov,
        "claims": results,
        "passed": len(results) - len(bad),
        "total": len(results),
        "seconds": round(time.time() - t0, 1),
        "non_claims": [
            "冻结面内判据都是**自证**的: 两个实现由同一作者编写, 本报告不等于外部确认",
            "三处刻意保守偏离: 类型未知时 checker 可能少报 (docs/158 §4)",
            "同名 let 双 alloca: 两个后端在该写法上语义不同 (docs/158 §4)",
            "自举驱动整条链没有 parser: 解析期错误只有参考实现能报 (docs/158 §4)",
            "aarch64 只验证到发射 (无 qemu-user/无真机执行) (docs/158 §4)",
            "自举性能 12.8s (参考 1.2s); 无 DWARF 变量信息; 工具链仍有 Python 成分",
            "Mimosa 扫描器未给出完整结论 (scanner_enobufs) —— 不宣称项目安全",
        ],
    }
    if a.json:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"\n[OK] {OUT.relative_to(ROOT)}")
    print(f"\nloment_audit: {report['passed']}/{report['total']} 通过 ({report['seconds']}s)"
          + ("" if not bad else " —— 有红: " + ", ".join(r["id"] for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
