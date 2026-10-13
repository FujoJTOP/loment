#!/usr/bin/env python3
# loment_internet_test.py — Internet 标准库 (`lompi/store/internet/0.1.0/`) 的判据
#
# 这是 LibL 家族第三个包的判据, 口径与 `loment_libl_test.py` 一致: **对着真东西比**。
#   * ip / uri / http 的对照物是 CPython 标准库 (`ipaddress` / `urllib.parse` /
#     `http.client`) —— 真的实现, 不是我们手写的期望值;
#   * 端到端那条**真起一个 Python http.server**, 让库自己的 tcp+http 去连它 ——
#     打桩等于把被测的那一段换掉 (与 `loment_lompi_test._serve_registry` 同一理由);
#   * dns 那一层**没有真解析器可用** (工具链里就没有, docs/236), 所以它的线格式判据对的是
#     **按 RFC 1035 独立推出来的字节**, 文件里明写这一点, 不假装对过真解析器。
#
# 探针编成**本机原生**可执行文件 (参考编译器出 IR + 仓内 `lomelf` 链), 于是真发系统调用
# 也能跑 —— 自检那条 Loment->Rust 的路走不了 socket (那里的 syscall 是 Linux 的 asm)。
#
# 运行: python tools/loment_internet_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import http.client
import http.server
import ipaddress
import socketserver
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "lompi" / "store" / "internet" / "0.1.0"
STD = ROOT / "lompi" / "store" / "std" / "0.1.0"
PROBE = ROOT / "loment" / "build" / "internet_probe.lomt"
IS_WIN = sys.platform == "win32"
MODULES = ("ip", "uri", "http", "dns", "sock", "tcp", "udp")

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 编 / 跑

def _prelude() -> str:
    """每个探针共用的头: 输出助手 (没有 printf, 十进制与十六进制自己写)。"""
    return """
fn w(s: str) -> u32 {
    return syscall4(1, 1, str_ptr(s) as u64, str_len(s) as u64) as u32;
}

fn wmem(p: ptr, o: u32, n: u32) -> u32 {
    return syscall4(1, 1, (p as u64) + o, n as u64) as u32;
}

fn wd(v: u32) -> u32 {
    let b: ptr = alloc(16);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(b, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(b, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(b, i) as u8;
        store8(b, i, load8(b, n - 1 - i) as u8);
        store8(b, n - 1 - i, lo);
        i = i + 1;
    }
    return syscall4(1, 1, b as u64, n as u64) as u32;
}
"""


def _compile(src: str, name: str, td: Path) -> Path:
    """把探针编成**本机原生**可执行文件 (PE on Windows / ELF else), 返回它的路径。"""
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    with PROBE.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        mod = lomentc.load(PROBE)
        deps = lomentc.resolve_deps(mod, ROOT, PROBE.parent, entry=PROBE)
        errs = lomentc.check(mod, deps=deps, _dep_pass=True)
        assert not errs, f"探针检查不过: {errs[:3]}"
        ll = lomentc.emit_llvm(mod, ROOT, deps)
    finally:
        PROBE.unlink(missing_ok=True)
    tool = lomelf.compile_pe if IS_WIN else lomelf.compile_ll
    raw = tool(ll)[0]
    exe = td / (name + (".exe" if IS_WIN else ".elf"))
    exe.write_bytes(raw)
    exe.chmod(0o755)
    return exe


def _run(exe: Path, timeout: int = 60) -> str:
    """跑探针, 返回 stdout。

    **按字节取回再手工解码**, 不用 `text=True` —— 那个默认做**通用换行转换**, 会把探针
    写出的 `\\r\\n` 悄悄变成 `\\n`。于是"逐字节比请求头"那条判据其实**没在比 CRLF**,
    在 Windows 上还能靠"两边一起被换行"侥幸过 (参考那份同样被转换)。这条是 CI 的 Linux
    上才现形的 —— 那边 CPython 的表和本地不是一版, 顺藤摸出来的。
    """
    r = subprocess.run([str(exe)], capture_output=True, shell=False, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in text.splitlines():
        if "|" in ln:
            k, v = ln.split("|", 1)
            out[k] = v
    return out


def _first_diff(got: str, want: str) -> str:
    g, w = got.splitlines(), want.splitlines()
    for i in range(max(len(g), len(w))):
        gl = g[i] if i < len(g) else "<无>"
        wl = w[i] if i < len(w) else "<无>"
        if gl != wl:
            return f"第 {i + 1} 行: 本库={gl!r} 参考={wl!r}"
    return ""


def _head(use: str) -> str:
    """探针的头: `module` 必须在文件最前 (Loment 的硬要求), 预置助手跟在它后面。"""
    return "\n".join(["module internet_probe", "choose runtime", use, ""]) + _prelude()


def _lit(s: str) -> str:
    """Loment 字符串字面量 —— 转义集只有 `\\n \\t \\" \\\\`, 所以只放 ASCII 可见字符。"""
    assert all(32 <= ord(c) < 127 and c not in '"\\' for c in s), f"字面量里有要转义的字符: {s!r}"
    return '"' + s + '"'


# ---------------------------------------------------------------- 结构

@test
def test_internet_modules_are_checkable():
    """每个模块**自己**必须是合法的 L1 单元 (不编可执行, 永远跑)。

    `_dep_pass=True` 的理由与 `loment_libl_test` 相同: 绕开 issue #246 那趟把"其余依赖
    整份"当符号表递下去的假报错。本判据只主张"这几个模块自己是合法单元"。
    """
    for name in MODULES:
        p = PKG / f"{name}.lomt"
        assert p.exists(), f"缺模块 {p}"
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps, _dep_pass=True)
        assert not errs, f"{p.name}: {errs[:2]}"
    assert (PKG / "internet.lomt").exists(), "缺包入口 internet.lomt"
    assert (PKG / "pkg.lomp").exists(), "缺 pkg.lomp"
    print(f"      {len(MODULES)} 个模块 + 入口 + pkg.lomp 都过检查")


@test
def test_internet_package_is_one_unit_without_name_collisions():
    """门面 (7 个模块进同一个单元) 不许有重名 —— 顶层名字是**平的**, 不做 mangling。"""
    entry = PKG / "internet.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    errs = lomentc.check(mod, deps=deps, _dep_pass=True)
    assert not errs, f"门面检查不过: {errs[:3]}"
    print("      门面 (7 个模块一个单元) 检查通过")


def _top_names(path: Path) -> set[str]:
    m = lomentc.load(path)
    out: set[str] = set()
    for group in (m.funcs, m.consts, m.structs, m.enums, m.traits, m.externs):
        for d in group:
            out.add(d.name)
    return out


@test
def test_internet_names_do_not_collide_with_std():
    """**internet 的顶层名字与 std 一个都不能撞。**

    这条钉住的是一条**隐性硬规则**: `resolve_name` 在包内模块层 (`store/<包>/<版本>/<名>.ext`)
    遇到**两处命中就直接报错** (lomentc 的 `_pkg_module_hits`), 而顶层名字在一个单元里是
    **平的**。所以两个包的同名顶层声明同时被 `use` 到时, 后果不是"后者覆盖前者", 是**编译
    错误** —— 在这条判据之前没有任何东西看着它。取了一个 std 已经用掉的内部前缀
    (例如 `u_`, 那是 `std/url.lomt` 的) 就会踩到。
    """
    mine: set[str] = set()
    for name in MODULES:
        mine |= _top_names(PKG / f"{name}.lomt")
    theirs: set[str] = set()
    for f in sorted(STD.glob("*.lomt")):
        theirs |= _top_names(f)
    # `Option` / `Result` 是**语言预置泛型**, 每个模块的名册里都有 —— 不是 std 声明的,
    # 所以先比掉它们 (否则这条判据对任何包都恒红)。
    builtin = {"Option", "Result"}
    clash = sorted((mine & theirs) - builtin)
    assert not clash, (
        f"internet 有 {len(clash)} 个顶层名字与 std 撞了: {clash[:8]} —— "
        "换掉公开前缀或内部前缀 (顶层名字在一个单元里是平的)")
    print(f"      internet {len(mine)} 个顶层名字与 std {len(theirs)} 个无一相撞")


# ---------------------------------------------------------------- ip 对 ipaddress

_IP_V4 = ["0.0.0.0", "1.2.3.4", "255.255.255.255", "255.1.0.9", "192.168.1.1",
          "127.0.0.1", "01.2.3.4", "1.2.3", "1.2.3.4.5", "256.1.1.1", "1.2.3.256",
          "1..2.3", "a.b.c.d", "1.2.3.04", "10.0.0.0", "", "1.2.3.4 "]
_IP_V6 = ["::", "::1", "1::", "2001:db8::1", "2001:0:0:1:0:0:0:1",
          "1:2:3:4:5:6:7:8", "::ffff:1.2.3.4", "1:2:3:4:5:6:1.2.3.4",
          "1::2::3", "1:2:3:4:5:6:7:8:9", "fe80::a:b:c:d", "2001:DB8::1",
          "0:0:0:0:0:0:0:0", "::ffff:0:0", "1:2:3:4:5:6:7"]
_IN_NET = [("10.1.2.3", "10.0.0.0", 8), ("10.1.2.3", "10.1.0.0", 16),
           ("10.1.2.3", "10.1.2.0", 24), ("10.1.2.3", "11.0.0.0", 8),
           ("192.168.5.9", "192.168.0.0", 16), ("1.2.3.4", "0.0.0.0", 0),
           ("1.2.3.4", "0.0.0.0", 32)]


def _ip_probe() -> str:
    b = [_head("use ip")]
    b.append("""
fn rd32(p: ptr) -> u32 {
    return load8(p, 0) | (load8(p, 1) << 8) | (load8(p, 2) << 16) | (load8(p, 3) << 24);
}

fn pv4(s: str) -> u32 {
    let v: ptr = alloc(4);
    let ok: u32 = ip_v4_parse(str_ptr(s), 0, str_len(s), v);
    w(s);
    w("|");
    if ok == 0 { w("NO"); } else { wd(rd32(v)); }
    w("\\n");
    return 0;
}

fn pv6(s: str) -> u32 {
    let a: ptr = alloc(16);
    let buf: ptr = alloc(64);
    let ok: u32 = ip_v6_parse(str_ptr(s), 0, str_len(s), a);
    w(s);
    w("|");
    if ok == 0 { w("NO\\n"); return 0; }
    let n: u32 = ip_v6_fmt(buf, 0, a);
    wmem(buf, 0, n);
    w("\\n");
    return 0;
}

fn innet(s: str, net: u32, bits: u32) -> u32 {
    let v: ptr = alloc(4);
    let ok: u32 = ip_v4_parse(str_ptr(s), 0, str_len(s), v);
    let r: u32 = 0;
    if ok == 1 && ip_v4_in_net(rd32(v), net, bits) { r = 1; }
    w(s);
    w("|");
    wd(r);
    w("\\n");
    return 0;
}

fn _start() {
""")
    for s in _IP_V4:
        b.append(f"    pv4({_lit(s)});")
    for s in _IP_V6:
        b.append(f"    pv6({_lit(s)});")
    for a, net, bits in _IN_NET:
        b.append(f"    innet({_lit(a)}, {int(ipaddress.IPv4Address(net))}, {bits});")
    b.append("    syscall4(60, 0, 0, 0);")
    b.append("}")
    return "\n".join(b) + "\n"


@test
def test_ip_matches_python_ipaddress():
    """ip 的解析 / 格式化 / 网段归属与 CPython `ipaddress` 逐字节一致。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = _run(_compile(_ip_probe(), "ip_probe", td))
    want: list[str] = []
    for s in _IP_V4:
        try:
            want.append(f"{s}|{int(ipaddress.IPv4Address(s))}")
        except ValueError:
            want.append(f"{s}|NO")
    for s in _IP_V6:
        try:
            want.append(f"{s}|{ipaddress.IPv6Address(s)}")
        except ValueError:
            want.append(f"{s}|NO")
    for a, net, bits in _IN_NET:
        n = ipaddress.IPv4Network(f"{net}/{bits}")
        want.append(f"{a}|{1 if ipaddress.IPv4Address(a) in n else 0}")
    diff = _first_diff(got, "\n".join(want))
    assert not diff, f"ip 与 ipaddress 不一致 @ {diff}"
    print(f"      ip: {len(want)} 项与 CPython ipaddress 逐字节一致")


# ---------------------------------------------------------------- uri 对 urllib

_URI = ["http://user@host:8080/a/b?q=1#f", "http://a/b/c/d;p?q", "https://x.y/",
        "ftp://ftp.is.co.za/rfc/rfc1808.txt", "http://[::1]:80/x", "mailto:a@b.c",
        "//host/p", "/abs/path", "rel/path", "?only=query", "#frag",
        "http://h/p?q#f", "http://h", "http://h/p?", "urn:isbn:0451450523"]
_JOIN = [("http://a/b/c/d;p?q", "g"), ("http://a/b/c/d;p?q", "./g"),
         ("http://a/b/c/d;p?q", "g/"), ("http://a/b/c/d;p?q", "/g"),
         ("http://a/b/c/d;p?q", "../g"), ("http://a/b/c/d;p?q", "../../g"),
         ("http://a/b/c/d;p?q", "?y"), ("http://a/b/c/d;p?q", "g?y"),
         ("http://a/b/c/d;p?q", ""), ("http://a/b/c/d;p?q", "."),
         ("http://a/b/c/d;p?q", ".."), ("http://a/b/c/d;p?q", ";x"),
         ("http://a/b/c/d;p?q", "g;x"), ("http://a/b/c/d;p?q", "#s"),
         ("http://a/b/c/d;p?q", "http://z/"), ("http://a/b/c/d;p?q", "//z/w"),
         ("http://a/b/c/d;p?q", "g#s"), ("http://a/b/c/d;p?q", "/./g"),
         ("http://a/b/c/d;p?q", "/../g")]


def _uri_probe() -> str:
    b = [_head("use uri")]
    b.append("""
fn part(d: ptr, rec: ptr, which: u32) -> u32 {
    let oo: ptr = alloc(4);
    let ol: ptr = alloc(4);
    if uri_part(rec, which, oo, ol) == 0 { return 0; }
    let o: u32 = load8(oo, 0) | (load8(oo, 1) << 8) | (load8(oo, 2) << 16) | (load8(oo, 3) << 24);
    let l: u32 = load8(ol, 0) | (load8(ol, 1) << 8) | (load8(ol, 2) << 16) | (load8(ol, 3) << 24);
    wmem(d, o, l);
    return 0;
}

fn sp(s: str) -> u32 {
    let d: ptr = str_ptr(s);
    let rec: ptr = alloc(URI_REC);
    if uri_parse(d, 0, str_len(s), rec) == 0 { w(s); w("|BAD\\n"); return 0; }
    w(s); w("|");
    part(d, rec, 0); w("|");
    part(d, rec, 2); w("|");
    let pt: u32 = uri_port(rec);
    if pt == URI_NO { w("-"); } else { wd(pt); }
    w("|");
    part(d, rec, 3); w("|");
    part(d, rec, 4); w("|");
    part(d, rec, 5);
    w("\\n");
    return 0;
}

fn join(base: str, ref: str) -> u32 {
    let bd: ptr = str_ptr(base);
    let rd: ptr = str_ptr(ref);
    let brec: ptr = alloc(URI_REC);
    let rrec: ptr = alloc(URI_REC);
    let out: ptr = alloc(512);
    w(base); w("|"); w(ref); w("|");
    if uri_parse(bd, 0, str_len(base), brec) == 0 { w("BAD\\n"); return 0; }
    if uri_parse(rd, 0, str_len(ref), rrec) == 0 { w("BAD\\n"); return 0; }
    let n: u32 = uri_resolve(bd, brec, rd, rrec, out, 512);
    if n == URI_NO { w("NO\\n"); return 0; }
    wmem(out, 0, n);
    w("\\n");
    return 0;
}

fn _start() {
""")
    for s in _URI:
        b.append(f"    sp({_lit(s)});")
    for base, ref in _JOIN:
        b.append(f"    join({_lit(base)}, {_lit(ref)});")
    b.append("    syscall4(60, 0, 0, 0);")
    b.append("}")
    return "\n".join(b) + "\n"


@test
def test_uri_matches_python_urllib():
    """uri 的拆解对 `urllib.parse.urlsplit`, 相对引用解析对 `urljoin`。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = _run(_compile(_uri_probe(), "uri_probe", td))
    want: list[str] = []
    for s in _URI:
        u = urllib.parse.urlsplit(s)
        port = str(u.port) if u.port is not None else "-"
        want.append("|".join([s, u.scheme, u.hostname or "", port, u.path,
                              u.query, u.fragment]))
    for base, ref in _JOIN:
        want.append("|".join([base, ref, urllib.parse.urljoin(base, ref)]))
    diff = _first_diff(got, "\n".join(want))
    assert not diff, f"uri 与 urllib.parse 不一致 @ {diff}"
    print(f"      uri: {len(want)} 项与 CPython urllib.parse 逐字节一致")


# ---------------------------------------------------------------- http 对 http.client

_HTTP_CODES = [100, 101, 102, 103, 200, 201, 202, 203, 204, 205, 206, 207, 208, 226,
               300, 301, 302, 303, 304, 305, 307, 308, 400, 401, 402, 403, 404, 405,
               406, 407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 421, 422,
               423, 424, 425, 426, 428, 429, 431, 451, 500, 501, 502, 503, 504, 505,
               506, 507, 508, 510, 511]

#: 一条合成的应答: 头 + 体。用来对 http.client 的分帧与头查找。
_HTTP_RESP = (b"HTTP/1.1 404 Not Found\r\n"
              b"Content-Length: 42\r\n"
              b"X-Mixed-Case:   spaced value  \r\n"
              b"Connection: close\r\n"
              b"\r\n"
              b"the body follows here, exactly forty-two b!")


def _http_probe() -> str:
    b = [_head("use http")]
    b.append("""
fn reason(code: u32) -> u32 {
    let out: ptr = alloc(64);
    let n: u32 = http_reason(code, out, 64);
    wd(code);
    w("|");
    if n == HTTP_NO { w("NO"); } else { wmem(out, 0, n); }
    w("\\n");
    return 0;
}

fn crlf(out: ptr, o: u32) -> u32 {
    store8(out, o, 13 as u8);
    store8(out, o + 1, 10 as u8);
    return o + 2;
}

fn putln(out: ptr, o: u32, s: str) -> u32 {
    let n: u32 = str_len(s);
    let i: u32 = 0;
    while i < n {
        store8(out, o + i, str_byte(s, i) as u8);
        i = i + 1;
    }
    return crlf(out, o + n);
}

fn _start() {
""")
    for c in _HTTP_CODES:
        b.append(f"    reason({c});")
    b.append("""
    let rq: ptr = alloc(1024);
    let rn: u32 = http_req_build(rq, 1024, "GET", "/x", "h.example", 8080, 0);
    w("REQ|"); wmem(rq, 0, rn); w("\\n");
    let rq2: ptr = alloc(1024);
    let rn2: u32 = http_req_build(rq2, 1024, "GET", "/", "h.example", 80, 1);
    w("REQ2|"); wmem(rq2, 0, rn2); w("\\n");
    let resp: ptr = alloc(1024);
    let rp: u32 = 0;
    let code_out: ptr = alloc(4);
    rp = putln(resp, rp, "HTTP/1.1 404 Not Found");
    rp = putln(resp, rp, "Content-Length: 42");
    rp = putln(resp, rp, "X-Mixed-Case:   spaced value  ");
    rp = putln(resp, rp, "Connection: close");
    rp = crlf(resp, rp);
    w("HDREND|"); wd(http_hdr_end(resp, 0, rp)); w("\\n");
    let bs: u32 = http_body_start(resp, 0, rp);
    w("BODYSTART|"); wd(bs); w("\\n");
    let so: u32 = http_status_line(resp, 0, rp, code_out);
    w("STATUSOK|"); wd(so); w("|");
    wd(load8(code_out, 0) | (load8(code_out, 1) << 8)); w("\\n");
    let oo: ptr = alloc(4);
    let ol: ptr = alloc(4);
    let found: u32 = http_hdr_find(resp, 0, bs, "content-length", oo, ol);
    w("CL|"); wd(found); w("|");
    let clo: u32 = load8(oo, 0) | (load8(oo, 1) << 8);
    let cll: u32 = load8(ol, 0) | (load8(ol, 1) << 8);
    wmem(resp, clo, cll); w("\\n");
    let found2: u32 = http_hdr_find(resp, 0, bs, "x-mixed-case", oo, ol);
    w("XM|"); wd(found2); w("|");
    let xo: u32 = load8(oo, 0) | (load8(oo, 1) << 8);
    let xl: u32 = load8(ol, 0) | (load8(ol, 1) << 8);
    wmem(resp, xo, xl); w("\\n");
    let absent: u32 = http_hdr_find(resp, 0, bs, "nope", oo, ol);
    w("ABSENT|"); wd(absent); w("\\n");
    syscall4(60, 0, 0, 0);
}
""")
    return "\n".join(b) + "\n"


#: RFC 9110（2022）给这几个码**改过名**，模块带的是**现行**写法，而 CPython 的表随版本走
#: （3.11 是旧写法、更新的那几版是新写法）。所以这里**点名**列出被取代的旧写法，只容忍这四条；
#: 别的一律逐字节比 —— 将来再有改名，这条会红，那是该看见的信号。
_SUPERSEDED = {
    413: "Request Entity Too Large",
    414: "Request-URI Too Long",
    416: "Requested Range Not Satisfiable",
    422: "Unprocessable Entity",
}


@test
def test_http_matches_python_client():
    """http 的原因短语对 `http.client.responses`; 请求构建与头查找对 `http.client`。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = _run(_compile(_http_probe(), "http_probe", td))
    want = [f"{c}|{http.client.responses[c]}" for c in _HTTP_CODES]
    # 请求头那两条**不能走按行解析**: 值本身就含 CRLF, 会被切碎。整串子串比对。
    for k, v in (("REQ", "GET /x HTTP/1.1\r\nHost: h.example:8080\r\nConnection: close\r\n\r\n"),
                 ("REQ2", "GET / HTTP/1.1\r\nHost: h.example\r\nConnection: keep-alive\r\n\r\n")):
        assert (k + "|" + v) in got, f"{k} 与期望的字节不同 (期望 {v!r})"
    head, _, _body = _HTTP_RESP.partition(b"\r\n\r\n")
    want.append(f"HDREND|{len(head)}")
    want.append(f"BODYSTART|{len(head) + 4}")
    want.append("STATUSOK|1|404")
    want.append("CL|1|42")
    want.append("XM|1|spaced value")
    want.append("ABSENT|0")
    g = _kv(got)
    for line in want:
        k, _, v = line.partition("|")
        assert k in g, f"探针没打出 {k}"
        ok = g[k] == v
        if not ok and k.isdigit():
            # 只容忍 RFC 9110 已公布的改名 (旧写法仍然出现在旧版 CPython 的表里)
            ok = v == _SUPERSEDED.get(int(k))
        assert ok, f"{k}: 本库={g[k]!r} CPython={v!r}"
    print(f"      http: 原因短语 {len(_HTTP_CODES)} 条 + 报文框架 7 项与 CPython 一致")


# ---------------------------------------------------------------- 端到端 (真服务器)

def _serve(root: Path) -> tuple[object, int]:
    """起一个**真的** HTTP 服务器 (127.0.0.1, 端口由内核分配)。打桩等于把被测段换掉。"""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _e2e_probe(port: int) -> str:
    return _head("use internet") + f"""
fn _start() {{
    let sa: ptr = alloc(16);
    let ip4: u32 = {int(ipaddress.IPv4Address("127.0.0.1"))};
    let fd: i64 = tcp_connect4(ip4, {port}, sa);
    if fd < 0 {{
        w("CONNECT-FAIL\\n");
        syscall4(60, 1, 0, 0);
    }}
    let rq: ptr = alloc(1024);
    let rn: u32 = http_req_build(rq, 1024, "GET", "/hello.txt", "127.0.0.1", {port}, 0);
    if rn == HTTP_NO {{
        w("REQ-FAIL\\n");
        syscall4(60, 1, 0, 0);
    }}
    let snt: i64 = tcp_send_all(fd, rq, rn as u64);
    if snt < 0 {{
        w("SEND-FAIL\\n");
        syscall4(60, 1, 0, 0);
    }}
    let buf: ptr = alloc(32768);
    let got: u64 = 0;
    let run: u32 = 1;
    while run == 1 && got < 32768 {{
        let dst: ptr = ((buf as u64) + got) as ptr;
        let r: i64 = tcp_recv(fd, dst, 32768 - got);
        if r <= 0 {{
            run = 0;
        }} else {{
            got = got + (r as u64);
        }}
    }}
    sock_close(fd);
    let code: ptr = alloc(4);
    let so: u32 = http_status_line(buf, 0, got as u32, code);
    w("STATUS|");
    if so == 0 {{ w("BAD\\n"); }} else {{ wd(load8(code, 0) | (load8(code, 1) << 8)); w("\\n"); }}
    let bs: u32 = http_body_start(buf, 0, got as u32);
    w("BODY|");
    if bs == HTTP_NO {{ w("BAD\\n"); }} else {{ wmem(buf, bs, (got as u32) - bs); w("\\n"); }}
    syscall4(60, 0, 0, 0);
}}
"""


@test
def test_http_client_against_real_server():
    """端到端: 库自己的 tcp + http **真连**一个 Python http.server, 取回真文件。"""
    body = b"hello from a real http.server\n"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        root = td / "www"
        root.mkdir()
        (root / "hello.txt").write_bytes(body)
        httpd, port = _serve(root)
        try:
            exe = _compile(_e2e_probe(port), "e2e_probe", td)
            out = _run(exe)
        finally:
            httpd.shutdown()
            httpd.server_close()
    want = f"STATUS|200\nBODY|{body.decode()}\n"
    assert out == want, f"端到端不一致: 本库={out!r} 期望={want!r}"
    print("      端到端: tcp+http 真连 Python http.server 取回 200 + 真文件内容")


# ---------------------------------------------------------------- dns 对 RFC 1035

_DNS_NAMES = ["example.com", "www.example.com", "a.b", "localhost", "a",
              "x.y.z.w", "sub.domain.example.org"]


def _dns_probe() -> str:
    b = [_head("use dns")]
    b.append("""
fn hex(p: ptr, n: u32) -> u32 {
    let tbl: str = "0123456789abcdef";
    let o: ptr = alloc(2048);
    let i: u32 = 0;
    while i < n {
        let v: u32 = load8(p, i);
        store8(o, i * 2, str_byte(tbl, (v >> 4) & 15) as u8);
        store8(o, i * 2 + 1, str_byte(tbl, v & 15) as u8);
        i = i + 1;
    }
    wmem(o, 0, n * 2);
    return 0;
}

fn enc(s: str) -> u32 {
    let out: ptr = alloc(256);
    let n: u32 = dns_name_encode(s, out, 256);
    w(s); w("|");
    if n == DNS_NO { w("NO\\n"); return 0; }
    hex(out, n);
    w("\\n");
    return 0;
}

fn _start() {
""")
    for s in _DNS_NAMES:
        b.append(f"    enc({_lit(s)});")
    b.append("""
    let q: ptr = alloc(512);
    let qn: u32 = dns_query_build(q, 512, 4660, "example.com", DNS_A, 1);
    w("QUERY|"); hex(q, qn); w("\\n");
    syscall4(60, 0, 0, 0);
}
""")
    return "\n".join(b) + "\n"


def _rfc1035_name(name: str) -> bytes:
    """**独立按 RFC 1035 §3.1 推**出来的线格式 (不是拿本库的输出当期望值)。"""
    out = b""
    for label in name.split("."):
        if not label:
            raise ValueError(name)
        out += bytes([len(label)]) + label.encode()
    return out + b"\x00"


def _rfc1035_query(name: str, qid: int, qtype: int) -> bytes:
    """独立推一条查询: 12 字节头 + 问题。"""
    hdr = qid.to_bytes(2, "big") + (0x0100).to_bytes(2, "big") \
        + (1).to_bytes(2, "big") + b"\x00\x00\x00\x00\x00\x00"
    return hdr + _rfc1035_name(name) + qtype.to_bytes(2, "big") + (1).to_bytes(2, "big")


@test
def test_dns_wire_format():
    """域名编码与查询报文对**独立按 RFC 1035 推出来的字节**。

    **边界如实说**: 工具链里没有解析器 (docs/236), 所以这里**没有**真解析器当对照物;
    期望值是本文件里按 RFC 1035 手推的 (`_rfc1035_name` / `_rfc1035_query`)。这条判据能
    证"我们的编码 == RFC 的编码", **不能**证"我们和某个真实现对上"。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        got = _run(_compile(_dns_probe(), "dns_probe", td))
    want = [f"{n}|{_rfc1035_name(n).hex()}" for n in _DNS_NAMES]
    want.append("QUERY|" + _rfc1035_query("example.com", 4660, 1).hex())
    diff = _first_diff(got, "\n".join(want))
    assert not diff, f"dns 线格式与 RFC 1035 推出来的字节不一致 @ {diff}"
    print(f"      dns: {len(_DNS_NAMES)} 个域名的编码 + 一条查询报文与 RFC 1035 推导一致")


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    only = argv[0] if argv else None
    failed = []
    for name, fn in TESTS:
        if only and only not in name:
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    n = len([x for x, _ in TESTS if not only or only in x])
    print(f"\nloment_internet_test: {n - len(failed)}/{n} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
