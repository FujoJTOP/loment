# API: `lumtui_demo`

> 源: `loment/examples/lumtui_demo.lomt` · 由 lomdoc 生成

## 常量

| 常量 | 类型 | 值 | 说明 |
|---|---|---|---|
| `W` | `u32` | 96 |  |
| `H` | `u32` | 36 |  |

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn wr(fd: u64, p: ptr, n: u32) -> i64`



### `fn wstr(s: str) -> ()`



### `fn say(sc: ptr, tag: str, v: u32) -> ()`



### `fn ramp(l: u32) -> str`

亮度 -> 一个字符。ASCII 只有 10 档, 够了 (这是"看形状", 不是"看颜色")。

### `fn dump(fb: ptr, w: u32, h: u32) -> ()`



### `fn build(d: ptr) -> u32`

搭一个界面: 透明的 surface 根, 里面一张居中的卡片 —— 标题 / 一排按钮 / 状态条。

### `fn _start() -> ()`
