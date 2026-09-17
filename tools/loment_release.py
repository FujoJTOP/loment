#!/usr/bin/env python3
# loment_release.py — 发布清单与可复现包 (M95/M99/M100, docs/152)
#
# 判据: 发布清单覆盖全部 Loment 工件, 每条带 sha256; --check 可被第三方机器复现。
#   python tools/loment_release.py --emit
#   python tools/loment_release.py --check
# 退出码: 0 = 一致 / 1 = 有差异 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "build" / "release-manifest.json"
# 版本名的**唯一真源** (2026-09-12 由 `loment-1.0-pre` 改名): 机器可读的标识符用连字符形式,
# 人读的显示名是 `0.1.4 Alpha`; 对外 tag = `v0.1.4-alpha` (git ref 不许带空格)。
# 2026-09-13 由 0.1.3.4-alpha 升到 0.1.4-alpha: 去 Python 收口 (lompkg + L0 生成器 lomc)。
# 2026-09-15 升到 **0.1.4-alpha2**: 三条腿全部落地 —— 去 WSL + 去 clang(产品/构建路径)
# + 包内链接器换成自举镜像; 发行包首次自带 agent skill。tag 会另打 `v0.1.4-alpha2`。
# 2026-09-15 再升到 **0.1.4-alpha2.2**: 库系统 (docs/168) —— 依赖从源码的 use 推导、
# 实例身份=递归哈希、同名多实例真能共存、能力需求沿闭包推导、.lomp 清单 (Loment 自己);
# 并补上 agent 指南与 lomlib 的 Loment 孪生 (id)。tag 会另打 `v0.1.4-alpha2.2`。
# 2026-09-15 再升到 **0.1.4-alpha2.3**: CLI 命令面 (docs/169) —— `loment` 从 9 条命令扩到 38 条,
# 命令前端本身就是 Loment 写的 (loment/tools/lomcli.lomt), 两个启动器各加一行转发。
# tag 会另打 `v0.1.4-alpha2.3`。
# 2026-09-16 升到 **0.1.4-pre1**: 语言的两处开口交给使用者 —— ①**后缀不再是语言的一部分**
# (只有 `.lom` 是 L0, 别的后缀都是 L1 源; 项目用 `loment.conf` 的 `source_ext` 定自己的后缀),
# ②**自定义 `loment` 命令** (`loment foo` -> PATH 上的 `loment-foo`, 像 `git foo`)。
# 名字形式 `use <名字>` 也从"只认仓库四根"改成按层搜 (deps/ -> 自带 store -> 内置根)。
# alpha 系列到此为止: 这两条是给**别人**用这个语言的口子, pre 之后不再改语言面。
# 2026-09-16 升到 **0.1.4-pre2**: **FFI** (docs/173) —— `extern fn` 进语言, 两个编译器都发
# `declare` 且逐字节一致; `lomelf` 能读外部 ELF 目标文件并**按 C ABI 传参**链接, 于是 Loment
# 程序真的调到了 C / C++ / Rust 的库; 运行期那一族 (Python / JS / Java) 走新加的进程桥
# `loment/lib/proc.lomt`。见 docs/174。
RELEASE = "0.1.4-pre2"
RELEASE_NAME = "0.1.4 Pre2"  # 人读显示名 (发行包/文档用同一个真源)
GLOBS = [
    "tools/lomc.py", "tools/lom_audit.py", "tools/lomc_test.py", "tools/lomentc.py",
    "tools/lomentc_test.py", "tools/potato.py", "tools/potato_test.py",
    "tools/potato_cross.py", "tools/potato_from.py", "tools/potato_measure.py",
    "tools/potato_llm_arm.py",
    "tools/potato_assert.py", "tools/loment.py", "tools/lomfmt.py", "tools/lomdoc.py",
    "tools/lompkg.py", "tools/lomlib.py", "tools/loment_lib_test.py",
    "tools/loment_cli_test.py",
    # lompi (随包发行的独立命令, docs/170): 源码快照 + 它外面的正本与仓内副本的同步/校验
    "lompi/*.lomt",
    # lompi 的标准库: 随 Loment 一起装 (std 127 模块 + host, 共 137 个文件, 用户 2026-09-16)。
    # **版本号是故意写死的**: 自举那边 (loment/tools/lomrel.lomt) 的 glob 只认"一段目录 +
    # 一个名字模式", `**` 在那边展开不出递归 (实测只给一层, 于是把 std/ host/ 两个**目录**
    # 当文件收了进来)。升版本要同时改两份清单 —— `loment_lompi_test` 里那条
    # `test_release_manifest_covers_every_store_file` 就是钉这个的: 库改版而清单没跟上,
    # `loment_release --check` 会**看不见文件而照样绿**, 那条会红。
    "lompi/store/std/0.1.0/*", "lompi/store/host/0.1.0/*",
    "tools/lompi_sync.py", "tools/loment_lompi_test.py",
    # 发布口 (docs/171): 把单仓里的 Loment / lompi 切出来推到各自的私有库
    "tools/loment_publish.py",
    "tools/loment_diag.py", "tools/loment_build.py",
    "tools/loment_lsp.py", "tools/loment_tools_test.py", "tools/loment_boot.py",
    "tools/loment_p7_test.py", "tools/loment_p8_test.py", "tools/loment_p9_test.py",
    "tools/loment_syscalls.py", "tools/loment_manual.py", "tools/ci.py",
    "tools/vscode_ext.py", "tools/vscode_ext_test.py", "tools/mono_trace.py",
    "tools/loment_ir_diff.py", "tools/loment_rule_parity.py",
    "tools/loment_seed.py", "tools/loment_seed_test.py",
    "tools/loment_fmt_test.py", "tools/loment_audit.py",
    "tools/loment_doc_test.py", "tools/loment_json_test.py",
    "tools/lomelf.py", "tools/loment_elf_test.py", "tools/loment_pe_test.py",
    # PE 目标的 shim 机器码（tools/lomelf.py --dump-win-shim 重建；自举镜像照抄这一份）
    "loment/build/win_shim.bin",
    "tools/loment_genesis.py", "tools/loment_genesis_test.py",
    "tools/loment_status_test.py", "tools/loment_rel_test.py",
    "tools/loment_pkg_test.py", "tools/loment_lomc_test.py", "loment/lib/*.lomt",
    "tools/loment_lsp_test.py", "tools/loment_editors_test.py",
    "editors/vim/*.md", "editors/vim/syntax/*.vim", "editors/vim/ftdetect/*.vim",
    "editors/vim/ftplugin/*.vim",
    "tools/loment_filetype.py", "tools/loment_filetype_test.py",
    # 行尾门禁 (docs/161): 本清单的 sha 对 CRLF 免疫, 但自举判据按原始字节读源码 —— 两者配对
    "tools/loment_eol.py",
    # 发行包 (docs/162): 命令安装 + 自解压安装包
    "tools/loment_dist.py", "tools/loment_dist_test.py",
    # 签名 (docs/163): Authenticode + SHA256SUMS 分离签名
    "tools/loment_sign.py", "tools/loment_sign_test.py",
    # 源码包 (docs/164): 语言源码 + 编辑器工具, 只从 git 索引取
    "tools/loment_src.py",
    # 无 Python 自举 (docs/159): 启动脚本 + 种子 (参考实现发射的驱动 IR) + Loment 版格式化器
    "loment/bootstrap.sh", "scripts/lomc.ps1", "scripts/install-lsp.ps1",
    "loment/build/selfhost_driver.ll", "loment/build/genesis/*", "loment/tools/*.lomt",
    "editors/loment.ico",
    "editors/vscode/package.json", "editors/vscode/language-configuration.json",
    "editors/vscode/README.md", "editors/vscode/src/*.js",
    "editors/vscode/syntaxes/*.json",
    "loment/examples/*.lomt", "loment/selfhost/*.lomt", "loment/corpus.json",
    "lom/*.lom",
    # 设计文档: 这份清单是 **Loment 线**的, 而本仓就是 Loment 的开发口 —— `docs/` 里
    # 全是这条线的文档, 所以一条 `docs/*.md` 就够, 不必再按名字分段收。
    #
    # 原文是 `docs/14*.md` + `docs/15*-loment-*.md` + `docs/16*-loment-*.md` —— 那是
    # **旧树**（`docs/` 里 FujoOS 与 Loment 两线混放）留下的写法。它在那边就已经两头不讨好:
    # `14*` 捞进了内核文档 (`docs/14-tss-irq.md`、`144/145/146/149-内核-*`), 而 `15*`/`16*`
    # 的 `-loment-` 收紧又**漏掉**了 110/142/147/17x 那几篇名字里没有 loment 的。
    # 搬到开发口之后前一半变成 5 条**指向不存在文件**的陈旧项 —— 2026-09-17 归因
    # `loment_rel_test` 那两条红时查出来的。
    "docs/*.md",
    "docs/manual/*.md",
    "docs/manual/api/*.md",
]


def sha(p: Path) -> str:
    """内容哈希。文本按**通用换行**归一后哈希 (同一文件在 LF/CRLF 检出下哈希相同);
    二进制 (图标/压缩包) 按原始字节哈希 —— 清单里两类可以混, 消费者只比 sha256。"""
    raw = p.read_bytes()
    if b"\x00" in raw:                 # 二进制: 按原始字节哈希
        return hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build() -> dict:
    files = []
    for g in GLOBS:
        for p in sorted(ROOT.glob(g)):
            if p.is_file():
                files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)})
    return {"release": RELEASE, "files": files}


def checksums_text(doc: dict) -> str:
    """SHA256SUMS 风格的校验和清单 (M88)。"""
    return "".join(f"{x['sha256']}  {x['path']}\n" for x in doc["files"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_release")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--checksums", metavar="PATH", help="写 SHA256SUMS 风格清单 (M88)")
    a = ap.parse_args(argv)
    want = build()
    if a.checksums:
        # 显式 LF: 校验清单会被 sha256sum -c 之类逐行解析, CRLF 会让文件名带上 \r
        Path(a.checksums).write_text(checksums_text(want), encoding="utf-8", newline="\n")
        print(f"[OK] {a.checksums} ({len(want['files'])} 行)")
        return 0
    if a.emit:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        # 显式 LF: 清单是机器读的工件 (行尾不该随宿主变), 见 loment_manual 同处注释
        OUT.write_text(json.dumps(want, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8", newline="\n")
        print(f"[OK] {OUT.relative_to(ROOT)} ({len(want['files'])} 个工件)")
        return 0
    if a.check or not (a.emit or a.checksums):
        # 无参数 = 门禁模式 (与仓库其它工具同一约定: ci.py 的静态门禁按 main() 调用)
        if not OUT.exists():
            print(f"[ERR] {OUT.relative_to(ROOT)} 缺失 (运行 --emit)")
            return 1
        got = json.loads(OUT.read_text(encoding="utf-8"))
        gmap = {x["path"]: x["sha256"] for x in got.get("files", [])}
        wmap = {x["path"]: x["sha256"] for x in want["files"]}
        bad = [p for p in wmap if gmap.get(p) != wmap[p]]
        extra = [p for p in gmap if p not in wmap]
        for p in bad[:8]:
            print(f"[DIFF] {p}")
        for p in extra[:8]:
            print(f"[STALE] {p}")
        print(f"loment_release: {len(wmap) - len(bad)}/{len(wmap)} 一致"
              f"{f' (+{len(extra)} 陈旧)' if extra else ''}")
        return 1 if bad or extra else 0
    print("[ERR] 需要 --emit 或 --checksums", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
