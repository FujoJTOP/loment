#!/usr/bin/env python3
# loment_filetype.py — 让 Windows 把 .lomt/.lom 当成**已知文件类型** (docs/157)
#
# 目标: "打开方式"里 Loment 常驻可选 (而不是只有一次性的"仅一次"), 能设成默认,
# 类型名与图标都是 Loment 的, 双击直接进编辑器。
#
# 原理 (全部只写 HKCU, **不需要管理员**):
#   HKCU\Software\Classes\.lomt                     默认值 = ProgID
#   HKCU\Software\Classes\.lomt\OpenWithProgids     登记 ProgID  <- "打开方式"常驻靠这条
#   HKCU\Software\Classes\<ProgID>                  FriendlyTypeName / DefaultIcon / shell\open\command
#   HKCU\...\Explorer\FileExts\.lomt\OpenWithProgids  双保险 (Explorer 也会看这里)
# 注意: `FileExts\.lomt\UserChoice` 在 Win10+ 带哈希保护, **程序改不了** —— 那一条只能由
# 用户在"打开方式"里点"始终"。本工具把前面四条写好, 让"始终"这一步有意义。
#
# 用法:
#   python tools/loment_filetype.py --status                     # 看现状 (只读)
#   python tools/loment_filetype.py --register --dry-run --json  # 打印将要写的注册表项
#   python tools/loment_filetype.py --register                   # 写 (自动找 VS Code)
#   python tools/loment_filetype.py --register --editor "X:\path\to\Code.exe"
#   python tools/loment_filetype.py --unregister                 # 撤销 (只删本工具建的键)
#   python tools/loment_filetype.py --emit-icon                  # 重新生成 editors/loment.ico
# 退出码: 0 = 成功 / 1 = 失败 (编辑器找不到等) / 2 = 用法错误。

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "editors" / "loment.ico"

#: 扩展名 -> (ProgID, 显示名, MIME)。改这里就同时改了注册表计划与文档。
EXT_MAP: dict[str, tuple[str, str, str]] = {
    ".lomt": ("Loment.Source", "Loment 源文件", "text/x-loment"),
    ".lom": ("Loment.L0", "Loment L0 声明文件", "text/x-lom"),
}
ICON_SIZE = 256
#: 图标配色 (FUI 主题的 accent 蓝; 想换橘色改这一行 + 重跑 --emit-icon)
ICON_BG = (60, 92, 200)
ICON_BG2 = (110, 139, 255)
ICON_FG = (255, 255, 255)


# ---------------------------------------------------------------- 编辑器发现

def _candidates() -> list[Path]:
    """VS Code 的常见位置 (只查固定位置, 不扫盘; 找不到就让用户给 --editor)。"""
    out: list[Path] = []
    for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if not base:
            continue
        out.append(Path(base) / "Programs" / "Microsoft VS Code" / "Code.exe")
        out.append(Path(base) / "Microsoft VS Code" / "Code.exe")
    return out


def find_editor(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for p in _candidates():
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------- 注册表计划

def plan(editor: Path, icon: Path) -> list[dict]:
    """返回要写的注册表项 (纯数据, 便于 --dry-run 与门禁核对)。"""
    ops: list[dict] = []
    cmd = f'"{editor}" "%1"'
    for ext, (progid, friendly, mime) in EXT_MAP.items():
        ops += [
            {"key": f"Software\\Classes\\{ext}", "name": "", "value": progid},
            {"key": f"Software\\Classes\\{ext}\\OpenWithProgids", "name": progid, "value": ""},
            {"key": f"Software\\Classes\\{ext}", "name": "PerceivedType", "value": "text"},
            {"key": f"Software\\Classes\\{progid}", "name": "", "value": friendly},
            {"key": f"Software\\Classes\\{progid}", "name": "FriendlyTypeName",
             "value": friendly},
            {"key": f"Software\\Classes\\{progid}", "name": "Content Type", "value": mime},
            {"key": f"Software\\Classes\\{progid}", "name": "PerceivedType", "value": "text"},
            {"key": f"Software\\Classes\\{progid}\\DefaultIcon", "name": "",
             "value": f'"{icon}",0'},
            {"key": f"Software\\Classes\\{progid}\\shell\\open\\command", "name": "",
             "value": cmd},
            # Explorer 的"打开方式 → 更多应用"也看这条 (与上面 OpenWithProgids 双保险)
            {"key": f"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{ext}"
                    f"\\OpenWithProgids", "name": progid, "value": ""},
        ]
    return ops


def created_subkeys(editor: Path, icon: Path) -> list[str]:
    """本工具**新建**的键 (撤销时按这些删; DefaultIcon/command 这类值不单独删键之外的东西)。"""
    keys = ["Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts"
            + "\\" + ext + "\\OpenWithProgids" for ext in EXT_MAP]
    for ext, (progid, _f, _m) in EXT_MAP.items():
        keys.append(f"Software\\Classes\\{progid}")
        keys.append(f"Software\\Classes\\{progid}\\DefaultIcon")
        keys.append(f"Software\\Classes\\{progid}\\shell")
        keys.append(f"Software\\Classes\\{progid}\\shell\\open")
        keys.append(f"Software\\Classes\\{progid}\\shell\\open\\command")
        keys.append(f"Software\\Classes\\{ext}")
        keys.append(f"Software\\Classes\\{ext}\\OpenWithProgids")
    return keys


# ---------------------------------------------------------------- Windows 注册表

def _winreg():
    import winreg  # 仅 Windows 有
    return winreg


def apply_ops(ops: list[dict], remove: bool = False) -> int:
    """写 (或删值) 注册表。remove=True 时只删值, 不删键 (键由 cleanup 负责)。"""
    winreg = _winreg()
    n = 0
    for op in ops:
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, op["key"], 0,
                                    winreg.KEY_SET_VALUE) as k:
                if remove:
                    try:
                        winreg.DeleteValue(k, op["name"])
                    except FileNotFoundError:
                        pass
                else:
                    # 一律 REG_SZ: OpenWithProgids 的"标记值"用空串即可 —— 值类型无关紧要,
                    # Explorer 只看**值名**是否存在 (REG_NONE 配空串会被 winreg 拒)。
                    winreg.SetValueEx(k, op["name"], 0, winreg.REG_SZ, op["value"])
            n += 1
        except OSError as e:
            print(f"[ERR] {op['key']}\\{op['name']}: {e}", file=sys.stderr)
    return n


def cleanup(keys: list[str]) -> int:
    """从深到浅删本工具建的键 (只删空键? 不 —— 键是我们建的, 直接删)。"""
    winreg = _winreg()
    n = 0
    for key in sorted(keys, key=len, reverse=True):
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, key)
            n += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[WARN] 删不掉 {key}: {e}", file=sys.stderr)
    return n


def _read(key: str, name: str) -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v)
    except OSError:
        return None


def status() -> int:
    print(f"图标: {ICON} {'存在' if ICON.is_file() else '缺失 (跑 --emit-icon)'}")
    rows = []
    for ext, (progid, friendly, _m) in EXT_MAP.items():
        cur = _read(f"Software\\Classes\\{ext}", "")
        owp = _read(f"Software\\Classes\\{ext}\\OpenWithProgids", progid)
        cmd = _read(f"Software\\Classes\\{progid}\\shell\\open\\command", "")
        icon = _read(f"Software\\Classes\\{progid}\\DefaultIcon", "")
        choice = _read("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer"
                       f"\\FileExts\\{ext}\\UserChoice", "ProgId")
        ok_editor = bool(cmd) and Path(cmd.split('"')[1]).is_file() if '"' in (cmd or "") \
            else False
        rows.append({"ext": ext, "progid": progid, "friendly": friendly,
                     "registered": cur == progid, "openwith": owp is not None,
                     "command": cmd, "has_command": bool(cmd),
                     "editor_exists": ok_editor, "icon": icon,
                     "user_choice": choice})
    for r in rows:
        hint = "(未设 —— 想设默认就在“打开方式”里点“始终”)"
        print(f"\n{r['ext']}  ->  {r['progid']}  ({r['friendly']})")
        print(f"  扩展名默认值: {'已指向本 ProgID' if r['registered'] else '未设置 (跑 --register)'}")
        print(f"  打开方式常驻: {'是' if r['openwith'] else '否'}")
        print(f"  默认图标:     {r['icon'] or '(无)'}")
        print(f"  打开命令:     {r['command'] or '(无)'}"
              f"{'' if (not r['has_command'] or r['editor_exists']) else '  <-- 编辑器不存在!'}")
        print(f"  用户选择:     {r['user_choice'] or hint}")
    return 0


# ---------------------------------------------------------------- 图标

def emit_icon(path: Path = ICON) -> int:
    """生成多尺寸 .ico (Pillow 只在**重新生成**时需要; 仓库里已提交成品)。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("[ERR] 需要 Pillow 才能重新生成图标: python -m pip install pillow", file=sys.stderr)
        print(f"      (仓库里已有 {ICON.name}; 平时不需要重新生成)", file=sys.stderr)
        return 1
    S = 4  # 每尺寸 4 倍超采样, 边缘平滑
    frames = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        n = size * S
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = int(n * 0.06)
        radius = int(n * 0.22)
        # 竖直渐变底 (上浅下深) + 圆角
        for y in range(pad, n - pad):
            t = (y - pad) / max(1, n - 2 * pad)
            col = tuple(int(ICON_BG2[i] + (ICON_BG[i] - ICON_BG2[i]) * t) for i in range(3))
            d.line([(pad, y), (n - pad, y)], fill=col + (255,))
        mask = Image.new("L", (n, n), 0)
        ImageDraw.Draw(mask).rounded_rectangle([pad, pad, n - pad - 1, n - pad - 1],
                                               radius=radius, fill=255)
        img.putalpha(mask)
        # 白色 "L": 竖笔 + 横笔 (16px 下也认得出)
        w = max(2, int(n * 0.16))
        x0 = int(n * 0.30)
        y0 = int(n * 0.24)
        y1 = int(n * 0.74)
        d.rectangle([x0, y0, x0 + w, y1], fill=ICON_FG + (255,))
        d.rectangle([x0, y1 - w, int(n * 0.72), y1], fill=ICON_FG + (255,))
        frames.append(img.resize((size, size), Image.LANCZOS))
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(path, format="ICO",
                    sizes=[(f.width, f.height) for f in frames])
    print(f"[OK] {path.relative_to(ROOT)} ({path.stat().st_size} 字节, "
          f"{len(frames)} 个尺寸)")
    return 0


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_filetype",
                                 description="Windows 文件类型注册 (.lomt/.lom)")
    ap.add_argument("--register", action="store_true", help="写 HKCU\\Software\\Classes")
    ap.add_argument("--unregister", action="store_true", help="撤销本工具的注册")
    ap.add_argument("--status", action="store_true", help="只读: 打印当前关联状态")
    ap.add_argument("--emit-icon", action="store_true", help="重新生成 editors/loment.ico")
    ap.add_argument("--editor", metavar="PATH", help="编辑器可执行文件 (默认自动找 VS Code)")
    ap.add_argument("--dry-run", action="store_true", help="不写注册表, 只打印将要做的操作")
    ap.add_argument("--json", action="store_true", help="与 --dry-run 合用: 操作计划打成 JSON")
    a = ap.parse_args(argv)

    if a.emit_icon:
        return emit_icon()
    if a.status:
        if os.name != "nt":
            print("[SKIP] 文件类型注册是 Windows 专有 (本机不是 Windows)")
            return 0
        return status()
    if not (a.register or a.unregister):
        ap.print_help()
        return 2
    if os.name != "nt":
        print("[ERR] 文件类型注册是 Windows 专有 (本机不是 Windows)", file=sys.stderr)
        return 2

    if a.unregister:
        plan_ops = plan(Path("x"), ICON)
        if a.dry_run:
            print("将删除的值:")
            for op in plan_ops:
                print(f"  HKCU\\{op['key']}  ({op['name'] or '默认值'})")
            print("将删除的键:")
            for k in created_subkeys(Path("x"), ICON):
                print(f"  HKCU\\{k}")
            return 0
        n = apply_ops(plan_ops, remove=True)
        m = cleanup(created_subkeys(Path("x"), ICON))
        print(f"[OK] 已撤销 (.lomt/.lom): 清值 {n} 处, 删键 {m} 个")
        print("  提醒: 若你之前在“打开方式”里点过“始终”, 那条 UserChoice 仍指向 Loment ——")
        print("        资源管理器里右键 → 打开方式 → 选择其它应用 即可改回。")
        return 0

    editor = find_editor(a.editor)
    if editor is None:
        if a.dry_run:
            # `--dry-run` 只打印**计划**, 不该要求本机装了编辑器 (干净检出/别的机器上可能没有):
            # 打开命令用占位符, 并在 stderr 说明 —— 真要注册必须给 --editor。
            print("[WARN] 没找到编辑器 (VS Code 默认位置) —— 计划里的打开命令用占位符; "
                  '要真注册请加 --editor "C:\\path\\to\\your-editor.exe"', file=sys.stderr)
            editor = Path("<在这里填编辑器可执行文件>")
        else:
            print("[ERR] 找不到编辑器。用 --editor \"C:\\path\\to\\Code.exe\" 指定 "
                  "(VS Code 默认在 %LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe)",
                  file=sys.stderr)
            return 1
    plan_ops = plan(editor, ICON)
    if a.dry_run:
        if a.json:
            print(json.dumps({"editor": str(editor), "icon": str(ICON),
                              "ops": plan_ops,
                              "keys": created_subkeys(editor, ICON)},
                             ensure_ascii=False, indent=1))
        else:
            print(f"编辑器: {editor}")
            print(f"图标:   {ICON}")
            for op in plan_ops:
                print(f"  HKCU\\{op['key']}  [{op['name'] or '默认值'}] = {op['value']!r}")
        return 0
    n = apply_ops(plan_ops)
    print(f"[OK] 已注册 {len(EXT_MAP)} 个扩展名 ({', '.join(EXT_MAP)}), 写了 {n} 处值")
    print(f"  编辑器: {editor}")
    print(f"  图标:   {ICON}")
    print("  下一步 (一次就够): 资源管理器里右键一个 .lomt → 打开方式 → 选 Loment → 始终;")
    print("  之后双击就进编辑器, '打开方式' 里 Loment 也会常驻。")
    print("  撤销: python tools/loment_filetype.py --unregister")
    return 0


if __name__ == "__main__":
    sys.exit(main())
