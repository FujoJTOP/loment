#!/usr/bin/env python3
# vscode_ext.py — 打包 Loment 的 VS Code 扩展 (.vsix)
#
# 为什么自己打包而不用 vsce: 离线可复现 + 确定性字节 (固定 mtime), 于是 .vsix 能进校验和清单。
# VSIX 就是一个 zip: [Content_Types].xml + extension.vsixmanifest + extension/** (含 node_modules)。
#
# 用法:
#   python tools/vscode_ext.py --check                    # 结构校验 (不打包)
#   python tools/vscode_ext.py --emit loment/build/loment-vscode.vsix
#   python tools/vscode_ext.py --install                  # 重打包 + 侧载进本机 VS Code
#   python tools/vscode_ext.py --doctor                   # 体检已装的那一份
# 退出码: 0 = 成功 / 1 = 校验失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
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


def ext_dir() -> Path:
    return Path.home() / ".vscode" / "extensions"


def ident_and_version() -> tuple[str, str]:
    pkg = load_pkg()
    return f"{pkg['publisher'].lower()}.{pkg['name']}", str(pkg.get("version", ""))


def vs_uri(path: Path) -> dict:
    """VS Code 在 `extensions.json` 里写的那个 URI 字面量。

    **形状照抄它自己写出来的**：只有 `$mid` / `path` / `scheme` 三个键，正斜杠；
    Windows 上盘符小写并顶一个 `/`（`C:/Users/...` → `/c:/Users/...`）。别自己发明
    `fsPath` / `external` 那几样 —— 那是更早的写法的残留，VS Code 认的是 `path`。

    "顶一个 `/`" 只对**盘符路径**成立：别的平台上路径本来就是 `/` 开头，再加一个就成了
    `//home/...`（2026-09-22 在 CI 的 Linux runner 上露出来的）。
    """
    s = str(path).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        s = f"/{s[0].lower()}{s[1:]}"
    return {"$mid": 1, "path": s, "scheme": "file"}


def fix_registration(entries: list, ident: str, dest: Path) -> tuple[list, int]:
    """把 `extensions.json` 里这个扩展的登记改到 `dest`。返回 `(新表, 改了几条)`。

    **为什么要改而不是删**：删了之后要等 VS Code 下次启动**重扫目录**才回来；
    改对则在"Developer: Reload Window"时就生效 —— 少一步依赖。

    这条修的是 2026-09-18 实测的那个坑：侧载时把旧副本挪成 `<ident>-<ver>.old`
    **留在了 `extensions/` 里**，VS Code 扫到它、把扩展登记到了那个名字上，而它随后
    被删掉 —— 于是磁盘上一切"看着在"，高亮/命令/F5 全部消失。见 `install()`。
    """
    n = 0
    for e in entries:
        if (e.get("identifier") or {}).get("id") != ident:
            continue
        e["relativeLocation"] = dest.name
        e["location"] = vs_uri(dest)
        n += 1
    return entries, n


def doctor() -> list[str]:
    """体检 VS Code **已安装副本**: 语法高亮出问题时, 90% 的原因在这四件事上。

    1. 扩展没装 (或装的是旧版) —— 语法文件与源不同哈希;
    2. 装到磁盘上但没进 `extensions.json` 索引 (手拷目录的典型后果) ⇒ 语法不生效;
       **或者进了索引但指向别处** —— 那和没索引一样糟, 而且更像"一切都对";
    3. 别的扩展也认 `.lomt`/`.lom`, 抢了语言 id;
    4. 装了但**没重载窗口** —— 语法在启动时加载, 这一步只能由人来做。
    """
    out: list[str] = []
    extdir = ext_dir()
    ident, ver = ident_and_version()
    inst = extdir / f"{ident}-{ver}"
    if not inst.is_dir():
        cands = sorted(p.name for p in extdir.glob(f"{ident}-*")) if extdir.is_dir() else []
        out.append(f"没装 ({inst.name} 不存在)"
                   + (f"; 磁盘上有 {cands} — 版本对不上?" if cands else ""))
        return out
    pkg = load_pkg()
    # 语法文件必须与源逐字节一致 (否则装的是旧语法)
    for g in pkg["contributes"]["grammars"]:
        rel = str(g["path"]).lstrip("./")
        src, dst = EXT / rel, inst / rel
        if not dst.is_file():
            out.append(f"已装副本缺语法文件 {rel}")
        elif src.read_text(encoding="utf-8") != dst.read_text(encoding="utf-8"):
            out.append(f"已装副本的 {rel} 与源不一致 (装的是旧语法?)")
    # 注册索引: 装了但没索引 = 语法不生效; **索引指着别处**同理 —— 而且更难看出来
    idx = extdir / "extensions.json"
    if idx.is_file():
        try:
            entries = json.loads(idx.read_text(encoding="utf-8"))
            hit = [e for e in entries if (e.get("identifier") or {}).get("id") == ident]
            if not hit:
                out.append(f"扩展在磁盘上但 extensions.json 里没有 {ident} 的登记 —— "
                           f"用 `python tools/vscode_ext.py --install` 重装")
            else:
                loc = hit[0].get("location") or {}
                p = str(loc.get("fsPath") or loc.get("path") or "")
                # `/c:/Users/...` -> `C:/Users/...`
                if re.match(r"^/[a-z]:", p):
                    p = p[1].upper() + p[2:]
                if not p or not Path(p).is_dir():
                    out.append(f"extensions.json 把 {ident} 登记在 `{p or '?'}`，但那儿"
                               f"**没有东西** —— 高亮/命令/F5 会整个不生效，而磁盘上"
                               f"一切看着都在（`--install` 可修）")
                elif Path(p).resolve() != inst.resolve():
                    out.append(f"extensions.json 登记的路径与预期不符: `{p}` ≠ `{inst}`")
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


def install(vsix: Path) -> list[str]:
    """重打包 + **侧载**进本机 VS Code。返回问题列表（空 = 成功）。

    ## 为什么不走 `code --install-extension`

    在这台机器上起不来：`Code.exe` 从沙箱里报"系统找不到指定的文件"，而 `cmd.exe`
    正常 —— 所以**不是权限问题**。而 VS Code 本来就支持"把扩展目录按它的布局丢进
    `~/.vscode/extensions/`"，照着解压即可（[reference 记忆] 记着这条）。

    ## 备份**必须**挪到 `extensions/` 外面

    那个目录下**任何**带 `package.json` 的文件夹都会被当成一个扩展扫进去。2026-09-18
    实测踩到：把旧副本挪成 `<ident>-<ver>.old` 留在原处，VS Code 正好在那一瞬扫到它，
    于是把扩展**登记到了那个马上要删掉的名字**上 —— 磁盘上一切都"看着在"，而高亮、
    命令、F5 全部消失。所以：备份去系统临时目录，装完再把登记**主动改回真目录**
    （`fix_registration`），不指望它下次启动重扫。

    装完**必须**由人做一步：`Developer: Reload Window`（语法与贡献在窗口启动时加载）。
    """
    out: list[str] = []
    ident, ver = ident_and_version()
    extdir = ext_dir()
    dest = extdir / f"{ident}-{ver}"
    if not vsix.is_file():
        return [f"没有 {vsix}（先 --emit，或直接跑 `--install`）"]
    extdir.mkdir(parents=True, exist_ok=True)
    stash = Path(tempfile.mkdtemp(prefix="loment-vscode-"))     # **在 extensions/ 外面**
    try:
        if dest.exists():
            shutil.move(str(dest), str(stash / dest.name))
        dest.mkdir(parents=True)
        with zipfile.ZipFile(vsix) as z:
            for n in z.namelist():
                if n == "extension.vsixmanifest":
                    with z.open(n) as src, open(dest / ".vsixmanifest", "wb") as f:
                        shutil.copyfileobj(src, f)
                    continue
                if not n.startswith("extension/"):
                    continue
                rel = n[len("extension/"):]
                if not rel:
                    continue
                dst = dest / rel
                if n.endswith("/"):
                    dst.mkdir(parents=True, exist_ok=True)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(dst, "wb") as f:
                    shutil.copyfileobj(src, f)
        idx = extdir / "extensions.json"
        if idx.is_file():
            entries = json.loads(idx.read_text(encoding="utf-8"))
            entries, n = fix_registration(entries, ident, dest)
            if n:
                idx.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(stash, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vscode_ext", description="Loment VS Code 扩展打包")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--emit", metavar="PATH")
    ap.add_argument("--verify", metavar="PATH")
    ap.add_argument("--install", action="store_true",
                    help="重打包并侧载进本机 VS Code (然后 Developer: Reload Window)")
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
    if a.install:
        # **每次都重打包**：拿一份已有的 .vsix 直接装，源码改了也看不出来 ——
        # 那是"装的是旧版"的静默（`--doctor` 能查出来，但不该先制造它）。
        n, size = emit(DEFAULT_OUT)
        bad = verify_vsix(DEFAULT_OUT)
        if bad:
            for b in bad:
                print(f"[ERR] {b}")
            print(f"[FAIL] 打包就有问题，没装 ({len(bad)} 个)")
            return 1
        bad = install(DEFAULT_OUT)
        for b in bad:
            print(f"[ERR] {b}")
        if bad:
            return 1
        ident, ver = ident_and_version()
        print(f"[OK] 已装 {ident}-{ver}（{n} 个文件, {size} 字节）")
        print("  **现在去按 `Ctrl+Shift+P` -> Developer: Reload Window**"
              "（贡献与语法在窗口启动时加载，不重载看不到）")
        print("  体检: python tools/vscode_ext.py --doctor")
        return 0
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
