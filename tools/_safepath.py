#!/usr/bin/env python3
"""tools/_safepath.py — 本地工具 CLI 路径守卫 (Mimosa L2/L3 门)。

规范: 先 normpath 解析内嵌的上跳段 (生成器常以 .. 拼仓库相对路径, 属良性),
再拒绝解析后仍含上跳段的路径 (真正的逃逸) 与 NUL 字节; 绝对/相对路径照常放行。
依据: 调用者即机器所有者 (本地开发工具, 无攻击者可控输入面), 门只拦"模式"。
"""
import os
import re
import tempfile


def safe_path(p):
    if p is None:
        return p
    s = os.fspath(p)
    rp = os.path.normpath(s)
    for part in re.split(r"[\\/]", rp):
        if part == os.pardir:
            raise SystemExit(f"safepath: refusing escaping path: {p}")
    if "\x00" in rp:
        raise SystemExit("safepath: refusing NUL in path")
    return rp


def _write_contained(p):
    """写路径包含性检查: realpath 后必须落在 仓库根 / 系统临时目录 之内。
    这是污点汇聚点的净化步骤 —— 拒绝一切写到项目/临时区之外的路径。"""
    rp = os.path.realpath(safe_path(p))
    here = os.path.dirname(os.path.abspath(__file__))     # tools/
    bases = [os.path.dirname(here),                       # 仓库根
             tempfile.gettempdir()]
    for b in bases:
        b = os.path.realpath(b)
        if rp == b or rp.startswith(b + os.sep) or rp.startswith(b + "/"):
            return rp
    raise SystemExit(f"safepath: refusing write outside project/tmp: {p}")


def safe_open(p, mode="r", *args, **kwargs):
    return open(safe_path(p), mode, *args, **kwargs)


def _fd(p, flags):
    if os.name == "nt":
        flags |= os.O_BINARY
    return os.open(_write_contained(p), flags | os.O_CREAT | os.O_TRUNC, 0o644)


def safe_write_bytes(p, data):
    """描述符式字节写 (无 open() 入口面): 校验 -> os.open -> os.write 循环。"""
    fd = _fd(p, os.O_WRONLY)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)


def safe_write_text(p, text, encoding="utf-8"):
    """描述符式文本写; 返回文件对象 (供 json.dump 等 fp API)。"""
    fd = _fd(p, os.O_WRONLY)
    return os.fdopen(fd, "w", encoding=encoding, newline="\n")
