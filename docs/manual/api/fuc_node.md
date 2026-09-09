# API: `fuc_node`

> 源: `D:\Dev\FujoOS\loment\examples\fuc_node.lomt` · 由 tools/lomdoc.py 生成

## 枚举

### `enum Option`



- `Option::Some` (T)
- `Option::None`

### `enum Result`



- `Result::Ok` (T)
- `Result::Err` (E)

## 函数

### `fn pack_node(p: ptr, kind: u32, id: u32, x: u32, y: u32, bg: u32) -> u32`

打包一个最小节点; 返回 64。
