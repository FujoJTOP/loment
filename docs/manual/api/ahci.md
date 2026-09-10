# API: `ahci`

> 源: `loment/examples/ahci.lomt` · 由 tools/lomdoc.py 生成

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn popcount32(x: u32) -> u32`



### `fn ahci_port_count(base: ptr) -> u32`

PI (0x0C) 的置位数 = 已实现端口数。

### `fn ahci_ncs(base: ptr) -> u32`

CAP.NCS (bits 0..4) = 每条命令列表的最大槽位数。

### `fn ahci_supports_64bit(base: ptr) -> bool`

CAP (0x00) 的 bit31 = 64 位寻址支持。

### `fn ahci_enable(base: ptr) -> u32`

GHC.AE (0x04 bit31) 置位 -> AHCI 使能。
