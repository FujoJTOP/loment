#!/usr/bin/env python3
# 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。
# module fujr

import struct as _struct

MAGIC = 0x524A5546
VERSION = 0x00000001
SECTION_ALIGN = 0x00001000

# enum Tag: u32 (3 项)
TAG_MANIFEST = 0x00000001
TAG_EMBED = 0x00000004
TAG_DATA = 0x00000005
TAG_COUNT = 3

# record Header: size=64 endian=little
HEADER_SIZE = 64
HEADER_FMT = '<III52x'
HEADER_STRUCT = _struct.Struct(HEADER_FMT)
HEADER_MAGIC_OFF = 0
HEADER_VERSION_OFF = 4
HEADER_COUNT_OFF = 8

# record Section: size=32 endian=little
SECTION_SIZE = 32
SECTION_FMT = '<I4xQQI4x'
SECTION_STRUCT = _struct.Struct(SECTION_FMT)
SECTION_TAG_OFF = 0
SECTION_OFF_OFF = 8
SECTION_SIZE_OFF = 16
SECTION_FNV1A_OFF = 24
