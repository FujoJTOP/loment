#!/usr/bin/env python3
# vscode_ext.py — 打包 Loment 的 VS Code 扩展 (.vsix)
#
# 为什么自己打包而不用 vsce: 离线可复现 + 确定性字节 (固定 mtime), 于是 .vsix 能进校验和清单。
# VSIX 就是一个 zip: [Content_Types].xml + extension.vsixmanifest + extension/** (含 node_modules)。
#
# 用法:
#   python tools/vscode_ext.py --check                    # 结构校验 (不打包)
#   python tools/vscode_ext.py --emit loment/build/loment-vscode.vsix
# 退出码: 0 = 成功 / 1 = 校验失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "editors" / "vscode"
DEFAULT_OUT = ROOT / "loment" / "build" / "loment-vscode.vsix"

# 打进 VSIX 的目录/文件 (相对 editors/vscode); node_modules 是运行时依赖, 必须带
INCLUDE_DIRS = ("src", "syntaxes", "node_modules")
INCLUDE_FILES = ("package.json", "language-configuration.json", "README.md")
SKIP_NAMES = (".map", ".ts", ".md.bak")

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".vsixmanifest" ContentType="text/xml" />
  <Default Extension=".json" ContentType="application/json" />
  <Default Extension=".js" ContentType="application/javascript" />
  <Default Extension=".md" ContentType="text/markdown" />
  <Default Extension=".txt" ContentType="text/plain" />
</Types>
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Tags>{tags}</Tags>
    <Categories>{categories}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
  </Assets>
</PackageManifest>
"""


def load_pkg() -> dict:
    return json.loads((EXT / "package.json").read_text(encoding="utf-8"))


def check() -> list[str]:
    """结构校验: 清单字段 / 入口文件 / 语法文件 / 语言与语法一一对应。"""
    bad: list[str] = []
    try:
        pkg = load_pkg()
    except Exception as e:  # noqa: BLE001
        return [f"package.json 无法解析: {e}"]
    for key in ("name", "version", "publisher", "engines", "main"):
        if key not in pkg:
            bad.append(f"package.json 缺字段: {key}")
    main = EXT / str(pkg.get("main", ""))
    if not main.is_file():
        bad.append(f"入口不存在: {pkg.get('main')}")
    langs = {lang["id"] for lang in pkg.get("contributes", {}).get("languages", [])}
    grammars = pkg.get("contributes", {}).get("grammars", [])
    if not langs:
        bad.append("contributes.languages 为空")
    if {g.get("language") for g in grammars} != langs:
        bad.append("grammars 与 languages 不一一对应")
    for g in grammars:
        p = EXT / str(g.get("path", ""))
        if not p.is_file():
            bad.append(f"语法文件缺失: {g.get('path')}")
        else:
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                bad.append(f"语法文件无法解析: {g.get('path')}: {e}")
                continue
            if not doc.get("scopeName"):
                bad.append(f"语法缺 scopeName: {g.get('path')}")
    for c in pkg.get("contributes", {}).get("commands", []):
        if not c.get("command") or not c.get("title"):
            bad.append(f"命令缺 command/title: {c}")
    if not (EXT / "node_modules" / "vscode-languageclient" / "package.json").is_file():
        bad.append("缺 node_modules/vscode-languageclient（先跑 npm install --omit=dev）")
    return bad


def _walk() -> list[tuple[Path, str]]:
    """返回 (磁盘路径, zip 内相对 extension/ 的 posix 路径)。"""
    items: list[tuple[Path, str]] = []
    for name in INCLUDE_FILES:
        p = EXT / name
        if p.is_file():
            items.append((p, name))
    for d in INCLUDE_DIRS:
        base = EXT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if any(str(p).endswith(s) for s in SKIP_NAMES):
                continue
            items.append((p, p.relative_to(EXT).as_posix()))
    return items


def emit(out: Path) -> tuple[int, int]:
    pkg = load_pkg()
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = MANIFEST.format(
        name=pkg["name"], version=pkg["version"], publisher=pkg["publisher"],
        display=pkg.get("displayName", pkg["name"]),
        description=pkg.get("description", ""),
        tags=",".join(pkg.get("keywords", [])),
        categories=",".join(pkg.get("categories", [])),
        engine=pkg["engines"]["vscode"],
    )
    items = _walk()
    # 确定性: 固定时间戳 + 固定压缩级, 同一输入 => 同一字节
    stamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, text in (("[Content_Types].xml", CONTENT_TYPES),
                           ("extension.vsixmanifest", manifest)):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, text)
        for p, rel in items:
            info = zipfile.ZipInfo(f"extension/{rel}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())
    return len(items), out.stat().st_size


def verify_vsix(path: Path) -> list[str]:
    bad: list[str] = []
    if not path.is_file():
        return [f"VSIX 不存在: {path}"]
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for need in ("[Content_Types].xml", "extension.vsixmanifest",
                     "extension/package.json", "extension/src/extension.js",
                     "extension/src/server-path.js",
                     "extension/node_modules/vscode-languageclient/package.json"):
            if need not in names:
                bad.append(f"VSIX 缺 {need}")
        if "extension/package.json" in names:
            pkg = json.loads(z.read("extension/package.json").decode("utf-8"))
            if not pkg.get("main"):
                bad.append("VSIX 内 package.json 缺 main")
    return bad


def doctor() -> list[str]:
    """体检 VS Code **已安装副本**: 语法高亮出问题时, 90% 的原因在这四件事上。

    1. 扩展没装 (或装的是旧版) —— 语法文件与源不同哈希;
    2. 装到磁盘上但没进 `extensions.json` 索引 (手拷目录的典型后果) ⇒ 语法不生效;
    3. 别的扩展也认 `.lomt`/`.lom`, 抢了语言 id;
    4. 装了但**没重载窗口** —— 语法在启动时加载, 这一步只能由人来做。
    """
    out: list[str] = []
    home = Path.home() / ".vscode"
    extdir = home / "extensions"
    pkg = load_pkg()
    ident = f"{pkg['publisher'].lower()}.{pkg['name']}"
    ver = str(pkg.get("version", ""))
    inst = extdir / f"{ident}-{ver}"
    if not inst.is_dir():
        cands = sorted(p.name for p in extdir.glob(f"{ident}-*")) if extdir.is_dir() else []
        out.append(f"没装 ({inst.name} 不存在)"
                   + (f"; 磁盘上有 {cands} — 版本对不上?" if cands else ""))
        return out
    # 语法文件必须与源逐字节一致 (否则装的是旧语法)
    for g in pkg["contributes"]["grammars"]:
        rel = str(g["path"]).lstrip("./")
        src, dst = EXT / rel, inst / rel
        if not dst.is_file():
            out.append(f"已装副本缺语法文件 {rel}")
        elif src.read_text(encoding="utf-8") != dst.read_text(encoding="utf-8"):
            out.append(f"已装副本的 {rel} 与源不一致 (装的是旧语法?)")
    # 注册索引: 装了但没索引 = 语法不生效
    idx = extdir / "extensions.json"
    if idx.is_file():
        try:
            entries = json.loads(idx.read_text(encoding="utf-8"))
            if not any((e.get("identifier") or {}).get("id") == ident for e in entries):
                out.append(f"扩展在磁盘上但 extensions.json 里没有 {ident} 的登记 —— "
                           f"用 code --install-extension 重装")
        except (json.JSONDecodeError, AttributeError) as e:
            out.append(f"extensions.json 读不了: {e}")
    else:
        out.append("找不到 extensions.json")
    # 抢语言 id
    for other in sorted(extdir.glob("*/package.json")):
        if other.parent.name.startswith(ident):
            continue
        try:
            opkg = json.loads(other.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for lang in (opkg.get("contributes") or {}).get("languages") or []:
            if any(ext in (".lomt", ".lom") for ext in (lang.get("extensions") or [])):
                out.append(f"{other.parent.name} 也声明了 {lang.get('extensions')} "
                           f"(语言 id {lang.get('id')}) —— 会抢 .lomt/.lom")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vscode_ext", description="Loment VS Code 扩展打包")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--emit", metavar="PATH")
    ap.add_argument("--verify", metavar="PATH")
    ap.add_argument("--doctor", action="store_true",
                    help="体检 VS Code 里已装的副本 (语法高亮出问题先跑这个)")
    a = ap.parse_args(argv)
    if a.doctor:
        bad = doctor()
        for b in bad:
            print(f"[ERR] {b}")
        print(f"[{'OK' if not bad else 'FAIL'}] vscode_ext --doctor ({len(bad)} 个问题)")
        print("  提醒: 语法在窗口启动时加载 —— 装完/更新完要 Developer: Reload Window;")
        print("  文件仍无高亮时看右下角语言模式是不是 Loment (Ctrl+K M 可改),")
        print("  再用 Developer: Inspect Editor Tokens and Scopes 看光标处的 scope。")
        return 0 if not bad else 1
    if a.check:
        bad = check()
        for b in bad:
            print(f"[ERR] {b}")
        print(f"[{'OK' if not bad else 'FAIL'}] vscode_ext --check"
              f" ({len(bad)} 个问题)")
        return 0 if not bad else 1
    if a.emit:
        n, size = emit(Path(a.emit))
        bad = verify_vsix(Path(a.emit))
        for b in bad:
            print(f"[ERR] {b}")
        print(f"[{'OK' if not bad else 'FAIL'}] {a.emit} ({n} 个文件, {size} 字节)")
        return 0 if not bad else 1
    if a.verify:
        bad = verify_vsix(Path(a.verify))
        for b in bad:
            print(f"[ERR] {b}")
        print(f"[{'OK' if not bad else 'FAIL'}] vscode_ext --verify ({len(bad)} 个问题)")
        return 0 if not bad else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
