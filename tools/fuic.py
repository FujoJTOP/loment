#!/usr/bin/env python3
"""fuic.py — Fujo UI 编译器 (docs/138 U3)。

.fui (界面文档) + .fus (设计令牌) -> .fuc (内核可载入的确定性二进制)。

用法:
  python tools/fuic.py ui/desktop.fui -o ui/build/desktop.fuc
  python tools/fuic.py --emit-rust kernel/src/fui/spec_gen.rs   # 词汇表 -> Rust 常量
  python tools/fuic.py --check                                   # 全部源 vs 已提交产物
  python tools/fuic.py --dump ui/build/desktop.fuc               # 反解打印

格式定义见 docs/138 §1.3; 词汇表单一来源 ui/fui_spec.json。
"""
import argparse
import json
import os
import struct
import sys

from _safepath import safe_open, safe_path, safe_write_bytes, safe_write_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(ROOT, "ui", "fui_spec.json")

# L0 单源: .fuc 布局来自 lom/fuc.lom (docs/141) — 生成物 lom/build/fuc.py。
import importlib.util as _ilu

_fuc_spec = _ilu.spec_from_file_location("lom_fuc_gen", os.path.join(ROOT, "lom", "build", "fuc.py"))
_fuc = _ilu.module_from_spec(_fuc_spec)
_fuc_spec.loader.exec_module(_fuc)
MAGIC = _fuc.MAGIC
VERSION = _fuc.VERSION
NODE_SIZE = _fuc.NODE_SIZE
MAX_NODES = 256
MAX_DEPTH = 16

# ---------------------------------------------------------------------------
# 词汇表
# ---------------------------------------------------------------------------


def load_spec():
    with safe_open(SPEC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


SPEC = load_spec()
WIDGETS = {w["name"]: w for w in SPEC["widgets"]}
TONES = {k: v["id"] for k, v in SPEC["tones"].items()}
TONES_RUST = {k: v["rust"] for k, v in SPEC["tones"].items()}
ICONS = SPEC["icons"]
FLAGS = SPEC["flags"]
MODES = SPEC["modes"]
ALIGNS = SPEC["aligns"]
ANCHORS = SPEC["anchors"]


# ---------------------------------------------------------------------------
# 词法
# ---------------------------------------------------------------------------

class Tok:
    def __init__(self, kind, val, line):
        self.kind = kind  # ident/str/num/color/percent/punct/eof
        self.val = val
        self.line = line

    def __repr__(self):
        return f"{self.kind}({self.val!r})@{self.line}"


PUNCT = "{}="


def lex(src):
    toks = []
    i = 0
    line = 1
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        # 注释: 行首 '#' 或 '# ' (避开颜色字面量 #RRGGBB)
        if c == "#" and (i + 1 >= n or src[i + 1] in " \t\n"):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c in PUNCT:
            toks.append(Tok("punct", c, line))
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and src[j] != '"':
                if src[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(src[j])
                j += 1
            if j >= n:
                raise SyntaxError(f"line {line}: unterminated string")
            toks.append(Tok("str", "".join(buf), line))
            i = j + 1
            continue
        if c == "#":
            j = i + 1
            while j < n and src[j] in "0123456789abcdefABCDEF":
                j += 1
            val = src[i + 1:j]
            if len(val) not in (6, 8):
                raise SyntaxError(f"line {line}: bad color #{val}")
            toks.append(Tok("color", val, line))
            i = j
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            # 1024x768: 数字紧跟 'x'+数字 -> 拆成 num, ident(x), num
            if j < n and src[j] == "x" and j + 1 < n and src[j + 1].isdigit():
                toks.append(Tok("num", int(src[i:j]), line))
                toks.append(Tok("ident", "x", line))
                i = j + 1
                continue
            if j < n and src[j] == "%":
                toks.append(Tok("percent", float(src[i:j]), line))
                j += 1
            else:
                txt = src[i:j]
                toks.append(Tok("num", float(txt) if "." in txt else int(txt), line))
            i = j
            continue
        if c.isalpha() or c in "_-":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_-."):
                j += 1
            toks.append(Tok("ident", src[i:j], line))
            i = j
            continue
        raise SyntaxError(f"line {line}: unexpected char {c!r}")
    toks.append(Tok("eof", None, line))
    return toks


# ---------------------------------------------------------------------------
# 语法 -> 节点树
# ---------------------------------------------------------------------------

class Node:
    def __init__(self, kind_name, line):
        self.kind_name = kind_name
        self.kind = WIDGETS[kind_name]["id"]
        self.line = line
        self.attrs = {}
        self.text = ""
        self.children = []
        self.idx = -1
        self.first_child = 0


class Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def peek(self):
        return self.t[self.p]

    def next(self):
        v = self.t[self.p]
        self.p += 1
        return v

    def expect(self, kind, val=None):
        v = self.next()
        if v.kind != kind or (val is not None and v.val != val):
            raise SyntaxError(f"line {v.line}: expected {kind} {val!r}, got {v}")
        return v

    def parse_doc(self):
        self.expect("ident", "doc")
        title = self.expect("str").val
        w, h = 1024, 768
        if self.peek().kind == "num":
            w = int(self.next().val)
            self.expect("ident", "x")
            h = int(self.next().val)
        self.expect("punct", "{")
        theme = None
        root = None
        while not (self.peek().kind == "punct" and self.peek().val == "}"):
            if self.peek().kind == "eof":
                raise SyntaxError("unexpected EOF in doc")
            if self.peek().kind == "ident" and self.peek().val == "theme":
                self.next()
                theme = self.expect("str").val
                continue
            n = self.parse_node()
            if root is None:
                root = n
            else:
                raise SyntaxError(f"line {n.line}: doc may have only one root")
        self.expect("punct", "}")
        if root is None:
            raise SyntaxError("doc has no root node")
        return title, w, h, theme, root

    def parse_node(self):
        name = self.expect("ident").val
        if name not in WIDGETS:
            raise SyntaxError(f"unknown widget {name!r}")
        node = Node(name, self.t[self.p - 1].line)
        # 位置参数/属性 (同一行; 子块在 '{' 后换行)
        start_line = self.t[self.p - 1].line
        while True:
            t = self.peek()
            if t.line != start_line:
                break
            if t.kind == "str" and "text" not in node.attrs:
                node.text = self.next().val
                continue
            if t.kind != "ident":
                break
            if t.val in ("hidden", "disabled", "clip", "grow"):
                node.attrs[self.next().val] = True
                continue
            if node.kind_name == "icon" and "icon" not in node.attrs and t.val in ICONS:
                node.attrs["icon"] = self.next().val
                continue
            key = self.next().val
            if self.peek().kind == "punct" and self.peek().val == "=":
                self.next()
                node.attrs[key] = self.parse_value()
            else:
                node.attrs[key] = True
        if self.peek().kind == "punct" and self.peek().val == "{":
            self.next()
            while not (self.peek().kind == "punct" and self.peek().val == "}"):
                if self.peek().kind == "eof":
                    raise SyntaxError(f"line {node.line}: unexpected EOF in block")
                node.children.append(self.parse_node())
            self.expect("punct", "}")
        return node

    def parse_value(self):
        t = self.next()
        if t.kind == "str":
            return ("str", t.val)
        if t.kind == "num":
            return ("num", t.val)
        if t.kind == "percent":
            return ("percent", t.val)
        if t.kind == "color":
            return ("color", t.val)
        if t.kind == "ident":
            return ("ident", t.val)
        raise SyntaxError(f"line {t.line}: bad value {t}")


# ---------------------------------------------------------------------------
# 令牌 (.fus)
# ---------------------------------------------------------------------------

class Token:
    def __init__(self, name, kind, value):
        self.name = name
        self.kind = kind  # 0=color 1=number
        self.value = value


def parse_fus(path):
    toks = []
    with safe_open(path, "r", encoding="utf-8") as f:
        src = f.read()
    for line in src.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("theme ") or s.endswith("{"):
            continue
        if s == "}":
            continue
        parts = s.split()
        if parts[0] == "color" and len(parts) >= 3:
            toks.append(Token(parts[1], 0, int(parts[2].lstrip("#"), 16)))
        elif parts[0] == "metric" and len(parts) >= 3:
            toks.append(Token(parts[1], 1, int(float(parts[2]))))
        elif parts[0] == "motion" and len(parts) >= 3:
            toks.append(Token(parts[1], 1, int(float(parts[2]))))
    return toks


# ---------------------------------------------------------------------------
# 节点 -> 64 字节记录
# ---------------------------------------------------------------------------

def size_mode(v, axis):
    """返回 (mode, val)。axis: 'w'|'h'"""
    if v is True:
        return MODES["grow"], 1
    if isinstance(v, tuple):
        kind, val = v
        if kind == "ident":
            if val == "grow":
                return MODES["grow"], 1
            if val == "fill":
                return MODES["fill"], 0
            raise SyntaxError(f"bad size value {val!r}")
        if kind == "percent":
            return MODES["percent"], int(val)
        if kind == "num":
            return MODES["fixed"], int(val)
    if isinstance(v, (int, float)):
        return MODES["fixed"], int(v)
    raise SyntaxError(f"bad size {v!r}")


def color_val(v, color_tokens):
    if isinstance(v, tuple) and v[0] == "color":
        h = v[1]
        if len(h) == 6:
            return 0x00000000 | int(h, 16)
        return int(h, 16)  # AARRGGBB
    if isinstance(v, tuple) and v[0] == "num":
        return int(v[1])
    if isinstance(v, tuple) and v[0] == "ident":
        if v[1] == "none":
            return 0
        if v[1] in color_tokens:
            return color_tokens[v[1]]
        raise SyntaxError(f"unknown color token {v[1]!r}")
    raise SyntaxError(f"bad color {v!r}")


def ident_of(v, default):
    if isinstance(v, tuple) and v[0] == "ident":
        return v[1]
    if isinstance(v, str):
        return v
    return default


def num_of(v, default=0):
    """属性值 -> 整数 (num/percent 取值; 其他回退默认)。"""
    if v is None or v is True or v is False:
        return default
    if isinstance(v, tuple):
        if v[0] in ("num", "percent"):
            return int(v[1])
        return default
    return int(v)


def pack_node(node, strings, color_tokens):
    a = node.attrs
    flags = 0
    if a.get("hidden"):
        flags |= FLAGS["hidden"]
    if a.get("disabled"):
        flags |= FLAGS["disabled"]
    if a.get("clip"):
        flags |= FLAGS["clip"]
    if a.get("scroll"):
        flags |= FLAGS["scroll"]
    if a.get("weight") == ("ident", "bold") or a.get("bold"):
        flags |= FLAGS["bold"]
    if a.get("abs"):
        flags |= FLAGS["abs"]
    # 锚点编码在 flags 高 4 位 (docs/138 §9): 0=tl 1=tr 2=bl 3=br 4=c
    flags |= (ANCHORS.get(ident_of(a.get("anchor"), "tl"), 0) & 0xF) << 8

    w_mode, w_val = size_mode(a.get("w", 0), "w")
    h_mode, h_val = size_mode(a.get("h", 0), "h")

    pad = num_of(a.get("pad"))
    pad_x = num_of(a.get("pad-x"), pad)
    pad_y = num_of(a.get("pad-y"), pad)
    pl = num_of(a.get("pad-l"), pad_x)
    pr = num_of(a.get("pad-r"), pad_x)
    pt = num_of(a.get("pad-t"), pad_y)
    pb = num_of(a.get("pad-b"), pad_y)

    tone = TONES.get(ident_of(a.get("tone"), "text"), 0)
    size = num_of(a.get("size"))
    radius = num_of(a.get("radius"))
    elev = num_of(a.get("elev"))
    gap = num_of(a.get("gap"))
    opacity = 255
    if "opacity" in a:
        ov = a["opacity"]
        opacity = int(ov[1] * 255 / 100) if isinstance(ov, tuple) and ov[0] == "percent" else num_of(ov, 255)

    # 容器默认交叉轴 stretch (flexbox 语义): 列的子节点默认填满列宽
    align_default = "stretch" if WIDGETS[node.kind_name]["container"] else "start"
    align = ALIGNS.get(ident_of(a.get("align"), align_default), 0)
    justify = ALIGNS.get(ident_of(a.get("justify"), "start"), 0)

    bg = color_val(a["bg"], color_tokens) if "bg" in a else 0
    fg = color_val(a["fg"], color_tokens) if "fg" in a else 0

    extra = 0
    if "value" in a:
        f = a["value"][1] if isinstance(a["value"], tuple) else a["value"]
        f = float(f)
        extra = int(round(f * 1000)) if f <= 1.0 else int(f * 10)
    elif node.kind_name == "icon" and "icon" in a:
        extra = ICONS.get(ident_of(a["icon"], ""), 0)
    elif a.get("checked") in (True, ("ident", "true")):
        extra = 1000
    elif a.get("selected") in (True, ("ident", "true")):
        extra = 1000

    tid = 0
    if node.text:
        if node.text not in strings:
            strings[node.text] = len(strings)
        tid = strings[node.text]

    rec = _fuc.NODE_STRUCT.pack(
        node.kind & 0xFFFF,
        num_of(a.get("id")) & 0xFFFF,
        flags & 0xFFFF,
        len(node.children) & 0xFFFF,
        num_of(a.get("x")),
        num_of(a.get("y")),
        w_mode & 0xFFFF,
        w_val & 0xFFFF,
        h_mode & 0xFFFF,
        h_val & 0xFFFF,
        pl & 0xFFFF, pt & 0xFFFF, pr & 0xFFFF, pb & 0xFFFF,
        gap & 0xFFFF, radius & 0xFFFF, elev & 0xFFFF, align & 0xFFFF,
        justify & 0xFFFF, size & 0xFFFF,
        bg & 0xFFFFFFFF,
        fg & 0xFFFFFFFF,
        tid & 0xFFFFFFFF,
        tone & 0xFFFF,
        opacity & 0xFFFF,
        node.first_child & 0xFFFFFFFF,
        extra & 0xFFFFFFFF,
    )
    assert len(rec) == NODE_SIZE, len(rec)
    return rec


def flatten(root):
    """BFS: 每个节点的子节点连续存放。"""
    nodes = [root]
    root.idx = 0
    q = [root]
    while q:
        n = q.pop(0)
        n.first_child = len(nodes)
        for c in n.children:
            c.idx = len(nodes)
            nodes.append(c)
            q.append(c)
    if len(nodes) > MAX_NODES:
        raise SyntaxError(f"too many nodes: {len(nodes)} > {MAX_NODES}")
    return nodes


def check_depth(n, d=0):
    if d > MAX_DEPTH:
        raise SyntaxError(f"node depth > {MAX_DEPTH}")
    for c in n.children:
        check_depth(c, d + 1)


def fnv1a(data):
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def compile_doc(fui_path, fus_paths):
    with safe_open(fui_path, "r", encoding="utf-8") as f:
        src = f.read()
    title, w, h, theme_name, root = Parser(lex(src)).parse_doc()
    check_depth(root)
    nodes = flatten(root)

    toks = []
    for p in fus_paths:
        toks.extend(parse_fus(p))
    color_tokens = {t.name: t.value for t in toks if t.kind == 0}

    # 保留 0 号为空串: text_id=0 表示"无文本" (而非取到第一个字符串)
    strings = {"": 0}
    recs = [pack_node(n, strings, color_tokens) for n in nodes]

    # 字符串表
    strtab = bytearray()
    strtab += struct.pack("<I", len(strings))
    ordered = sorted(strings.items(), key=lambda kv: kv[1])
    for s, _ in ordered:
        b = s.encode("utf-8")
        strtab += struct.pack("<H", len(b)) + b

    # 令牌表
    tokblob = bytearray()
    for t in toks:
        nb = t.name.encode("utf-8")
        tokblob += struct.pack("<H", len(nb)) + nb + struct.pack("<BI", t.kind, t.value & 0xFFFFFFFF)

    header = bytearray()
    header += struct.pack("<IHH", MAGIC, VERSION, 0)
    header += struct.pack("<I", len(nodes))
    header += struct.pack("<I", 48)  # node_off (header 48B, fnv 在文件尾)
    str_off = 48 + len(nodes) * NODE_SIZE
    header += struct.pack("<I", str_off)
    header += struct.pack("<I", len(strtab))
    tok_off = str_off + len(strtab)
    header += struct.pack("<I", tok_off)
    header += struct.pack("<I", len(toks))
    header += struct.pack("<II", 0, 0)  # anim_off/count (v1 保留)
    header += struct.pack("<HHHH", w & 0xFFFF, h & 0xFFFF, 0, 0)
    assert len(header) == 48, len(header)
    body = header + b"".join(recs) + bytes(strtab) + bytes(tokblob)
    blob = body + struct.pack("<I", fnv1a(body))
    return blob, title, w, h, theme_name, nodes, strings, toks


# ---------------------------------------------------------------------------
# 反解 (验证/调试)
# ---------------------------------------------------------------------------

def dump(path):
    with safe_open(path, "rb") as f:
        b = f.read()
    magic, ver, _flags, ncount, noff, soff, slen, toff, tcount, aoff, acount, w, h, root, _r = \
        struct.unpack_from("<IHHIIIIIIIIHHHH", b, 0)
    stored = struct.unpack_from("<I", b, len(b) - 4)[0]
    print(f"FUIC v{ver} {w}x{h} nodes={ncount} root={root} toks={tcount} "
          f"size={len(b)} fnv=0x{stored:08X} check={'OK' if fnv1a(b[:-4]) == stored else 'BAD'}")
    for i in range(ncount):
        o = noff + i * NODE_SIZE
        (kind, nid, flags, cc, x, y, wm, wv, hm, hv, pl, pt, pr, pb, gap, rad, elev,
         align, just, size, bg, fg, tid, tone, op, fc, extra) = struct.unpack_from(
            "<HHHHhhHHHHHHHHHHHHHHIIIHHII", b, o)
        print(f"  [{i:2}] kind={kind:2} id={nid:3} flags={flags:3} cc={cc} "
              f"xy=({x},{y}) w={wm}:{wv} h={hm}:{hv} pad=({pl},{pt},{pr},{pb}) "
              f"gap={gap} r={rad} elev={elev} align={align}/{just} size={size} "
              f"bg={bg:08X} fg={fg:08X} txt={tid} tone={tone} fc={fc} extra={extra}")


# ---------------------------------------------------------------------------
# Rust 常量生成
# ---------------------------------------------------------------------------

def emit_rust(out_path):
    L = []
    L.append("// @generated by tools/fuic.py --emit-rust — DO NOT EDIT.")
    L.append("// 词汇表单一来源: ui/fui_spec.json (docs/138 §1)")
    L.append("")
    for w in SPEC["widgets"]:
        L.append(f'pub const K_{w["name"].upper()}: u16 = {w["id"]};')
    L.append("")
    for name, rust in TONES_RUST.items():
        L.append(f'pub const {rust}: u16 = {TONES[name]};')
    L.append("")
    for name, v in FLAGS.items():
        L.append(f'pub const F_{name.upper()}: u16 = 1 << {v.bit_length() - 1};')
    L.append("")
    for name, v in MODES.items():
        L.append(f'pub const M_{name.upper()}: u16 = {v};')
    L.append("")
    for name, v in ALIGNS.items():
        L.append(f'pub const A_{name.upper()}: u16 = {v};')
    L.append("")
    L.append(f'pub const N_ICONS: u16 = {max(ICONS.values())};')
    fp = safe_write_text(safe_path(out_path), "")
    with fp:
        fp.write("\n".join(L) + "\n")
    print(f"wrote {out_path}")


def main():
    ap = argparse.ArgumentParser(prog="fuic")
    ap.add_argument("input", nargs="?", help=".fui 源文件")
    ap.add_argument("-o", "--out", help=".fuc 输出路径")
    ap.add_argument("--theme", action="append", default=[], help=".fus 主题文件 (可多次)")
    ap.add_argument("--emit-rust", metavar="PATH", help="生成 spec_gen.rs")
    ap.add_argument("--emit-c", metavar="PATH", help="生成 C 头 (SDK 内嵌用)")
    ap.add_argument("--dump", metavar="PATH", help="反解 .fuc")
    ap.add_argument("--check", action="store_true", help="重编译全部源并与产物比对")
    args = ap.parse_args()

    if args.emit_rust:
        emit_rust(args.emit_rust)
        return 0
    if args.dump:
        dump(args.dump)
        return 0
    if args.check:
        return check_all()
    if not args.input or not args.out:
        ap.error("需要 input 与 -o out (或 --emit-rust/--check/--dump)")

    blob, title, w, h, theme, nodes, strings, toks = compile_doc(args.input, args.theme)
    out = safe_path(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    safe_write_bytes(out, blob)
    print(f"fuic: {args.input} -> {args.out}  {w}x{h} nodes={len(nodes)} "
          f"strs={len(strings)} toks={len(toks)} bytes={len(blob)}")
    if args.emit_c:
        emit_c(args.emit_c, blob, title)
    return 0


def emit_c(out_path, blob, title):
    sym = os.path.splitext(os.path.basename(out_path))[0].upper().replace("-", "_")
    L = ["/* @generated by tools/fuic.py --emit-c */", "#pragma once", ""]
    L.append(f"/* {title} — {len(blob)} bytes */")
    L.append(f"static const unsigned char FUI_{sym}[] = {{")
    for i in range(0, len(blob), 16):
        L.append("  " + ",".join(str(b) for b in blob[i:i + 16]) + ",")
    L.append("};")
    L.append(f"static const unsigned int FUI_{sym}_LEN = {len(blob)}u;")
    fp = safe_write_text(safe_path(out_path), "")
    with fp:
        fp.write("\n".join(L) + "\n")
    print(f"wrote {out_path}")


def check_all():
    """重编译 ui/*.fui × 每个主题, 与 ui/build/*.fuc 逐字节比对 (漂移检测)。"""
    ui_dir = os.path.join(ROOT, "ui")
    build = os.path.join(ui_dir, "build")
    themes = sorted(fn for fn in os.listdir(ui_dir) if fn.endswith(".fus"))
    ok = True
    for fn in sorted(os.listdir(ui_dir)):
        if not fn.endswith(".fui"):
            continue
        src = os.path.join(ui_dir, fn)
        for th in themes:
            suffix = "" if th == "aurora-dark.fus" else "-" + th[:-4].replace("aurora-", "")
            out = os.path.join(build, fn[:-4] + suffix + ".fuc")
            blob, *_ = compile_doc(src, [os.path.join(ui_dir, th)])
            if not os.path.exists(out):
                print(f"CHECK FAIL: missing {out}")
                ok = False
                continue
            with safe_open(out, "rb") as f:
                old = f.read()
            if old != blob:
                print(f"CHECK FAIL: {fn} [{th}] drifted ({len(old)} vs {len(blob)} bytes)")
                ok = False
            else:
                print(f"CHECK OK  : {fn} [{th}] ({len(blob)} bytes)")
    if ok:
        print("fuic --check: all documents up to date")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
