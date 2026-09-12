# API: `native_cap`

> 源: `loment/examples/native_cap.lomt` · 由 lomdoc 生成

## 能力域

| 能力 | 空间 | 区间 | 可撤销 | 说明 |
|---|---|---|---|---|
| `blk_write` | `disk` | [0..4] | 是 |  |

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn write(slot: u32) -> u32`



### `fn ok() -> u32`



### `fn literal_ok() -> u32`
