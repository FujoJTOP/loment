#!/usr/bin/env python3
"""fujopack — FujoOS `.run` 容器命令行工具链 (M31)

FUJR v0.1 容器格式 (与 kernel/src/fujr.rs 一致):
  64B 头 [FUJR][ver u32][count u32][pad]
  + 32B×count 节表: [tag u32][pad u32][off u64][size u64][fnv1a u32][pad u32]
  + payload (4096 对齐)
  节 tag: 1=MANIFEST(json)  4=EMBED(可执行体)  5=DATA(资源)

用法:
  fujopack.py pack   -e EXEC [-m manifest.json] [-r name:file ...] -o out.run
  fujopack.py info   FILE.run
  fujopack.py check  FILE.run
"""
import argparse
import json
import sys

from _safepath import safe_open

# L0 单源: FUJR 布局来自 lom/fujr.lom (docs/141) — 生成物 lom/build/fujr.py。
# **生成物不在索引里**（`docs/189` §3.0 的 S3 决定）：缺了就现场生成一次，
# 在就原样用 —— 所以这句的代价只付一次，而且只在真缺的时候付。
import importlib.util as _ilu
import os as _os

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_FUJR_GEN = _os.path.join(_ROOT, "lom", "build", "fujr.py")
if not _os.path.exists(_FUJR_GEN):
    import lomc
    lomc.ensure_python(_os.path.join(_ROOT, "lom", "fujr.lom"), _FUJR_GEN)

_spec = _ilu.spec_from_file_location("lom_fujr_gen", _FUJR_GEN)
_fujr = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_fujr)


def fnv1a(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def pack(exec_bytes: bytes, resources, manifest: str | None, out: str, name: str = "fujo-program", type_: str = "app", verbose: bool = False):
    sections = []
    sections.append((_fujr.TAG_EMBED, exec_bytes))  # EMBED
    man = None
    if manifest:
        man = manifest.encode()
    else:
        man = json.dumps({
            "name": name,
            "type": type_,
            "resources": [{"name": n} for (n, _) in resources],
            "perms": ["runres:read"],
        }).encode()
    sections.append((_fujr.TAG_MANIFEST, man))
    for (name_, data) in resources:
        sections.append((_fujr.TAG_DATA, data))
    if verbose:
        print(f"fujopack: sections={len(sections)} exec={len(exec_bytes)}b resources={len(resources)}")

    count = len(sections)
    hdr_len = _fujr.HEADER_SIZE + _fujr.SECTION_SIZE * count
    off = hdr_len
    entries = []
    payload = bytearray()
    for (tag, data) in sections:
        aligned = (off + _fujr.SECTION_ALIGN - 1) & ~(_fujr.SECTION_ALIGN - 1)
        payload.extend(b"\0" * (aligned - off))
        off = aligned
        payload.extend(data)
        entries.append((tag, aligned, len(data), fnv1a(data)))
        off += len(data)

    hdr = bytearray()
    hdr.extend(_fujr.HEADER_STRUCT.pack(_fujr.MAGIC, _fujr.VERSION, count))
    for (tag, o, sz, h) in entries:
        hdr.extend(_fujr.SECTION_STRUCT.pack(tag, o, sz, h))
    hdr.extend(payload)
    with safe_open(out, "wb") as f:
        f.write(hdr)
    print(f"fujopack: wrote {out} ({len(hdr)} bytes, {count} sections)")


def info(path: str, verify: bool):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < _fujr.HEADER_SIZE or _fujr.HEADER_STRUCT.unpack_from(data, 0)[0] != _fujr.MAGIC:
        print("fujopack: not a FUJR container", file=sys.stderr)
        return 1
    _magic, ver, count = _fujr.HEADER_STRUCT.unpack_from(data, 0)
    print(f"fujopack: FUJR v{ver} sections={count} total={len(data)}")
    names = {_fujr.TAG_MANIFEST: "MANIFEST", _fujr.TAG_EMBED: "EMBED", _fujr.TAG_DATA: "DATA"}
    ok = True
    for i in range(count):
        tag, off, size, h = _fujr.SECTION_STRUCT.unpack_from(
            data, _fujr.HEADER_SIZE + i * _fujr.SECTION_SIZE
        )
        real = fnv1a(data[off:off + size]) if verify else -1
        match = "ok" if (not verify or h == real) else f"HASH MISMATCH ({h:#x} vs {real:#x})"
        if match != "ok":
            ok = False
        print(f"  [{i}] {names.get(tag, tag):8s} off={off:#x} size={size:#x} fnv={h:#010x} {match}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="fujopack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("-e", "--exec", required=True)
    p.add_argument("-m", "--manifest", default=None)
    p.add_argument("-r", "--resource", action="append", default=[])
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--name", default="fujo-program", help="manifest 名")
    p.add_argument("--type", default="app", help="manifest 类型 (app/game/tool)")
    p.add_argument("-v", "--verbose", action="store_true", help="节表概要")
    p2 = sub.add_parser("info")
    p2.add_argument("file")
    p3 = sub.add_parser("check")
    p3.add_argument("file")
    a = ap.parse_args()
    if a.cmd == "pack":
        with open(a.exec, "rb") as f:
            ex = f.read()
        man = None
        if a.manifest:
            with open(a.manifest, "r", encoding="utf-8") as f:
                man = f.read()
        res = []
        for r in a.resource:
            if ":" not in r:
                print(f"fujopack: resource '{r}' must be name:file", file=sys.stderr)
                return 1
            name, path = r.split(":", 1)
            with open(path, "rb") as f:
                res.append((name[:15], f.read()))
        pack(ex, res, man, a.out, a.name, a.type, a.verbose)
        return 0
    if a.cmd == "info":
        return info(a.file, verify=False)
    if a.cmd == "check":
        return info(a.file, verify=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
