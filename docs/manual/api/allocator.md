# API: `allocator`

> 源: `loment/examples/allocator.lomt` · 由 tools/lomdoc.py 生成

## 常量

| 常量 | 类型 | 值 | 说明 |
|---|---|---|---|
| `HDR` | `u32` | 8 |  |

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn blk_size(p: ptr) -> u32`



### `fn blk_free(p: ptr) -> bool`



### `fn set_free(p: ptr, v: u32) -> ()`



### `fn pool_init(p: ptr, bytes: u32, block: u32) -> ()`

初始化池: 切成固定大小的块 (每块 8B 头 + block 字节数据)。

### `fn pool_alloc(p: ptr, need: u32) -> ptr`

first-fit 分配; 返回块指针, 失败返回 0。

### `fn pool_free(p: ptr, blk: ptr) -> u32`

释放 (无合并的 v0)。

### `fn pool_free_count(p: ptr, blocks: u32) -> u32`

统计空闲块数 (测试用)。
