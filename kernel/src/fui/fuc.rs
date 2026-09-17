//! fui/fuc.rs — `.fuc` 二进制文档读取 (docs/138 §1.3)
//!
//! 内核只读校验过的二进制: magic/version/边界/FNV 四道检查; 节点记录
//! 固定 64 字节, 零拷贝视图, 越界读取一律返回默认值 (不 panic)。
//!
//! 布局:
//!   header 48B | nodes (64B×n) | strings | tokens | body_fnv(u32 @ 48)

use super::paint::Rect;

// 布局常量由 lom/fuc.lom 单源生成 (docs/141 §4), 生成物 kernel/src/fui/fuc_gen.rs。
pub use super::fuc_gen::*;

pub const MAX_NODES: usize = 256;
pub const MAX_DEPTH: u32 = 16;

// 词汇表常量 (kind/tone/flags/模式/对齐) 由 ui/fui_spec.json 单源生成,
// 见 tools/fuic.py --emit-rust 与 docs/138 §1。
pub use super::spec_gen::*;

fn rd_u16(b: &[u8], o: usize) -> u16 {
    if o + 2 > b.len() {
        return 0;
    }
    (b[o] as u16) | ((b[o + 1] as u16) << 8)
}

fn rd_u32(b: &[u8], o: usize) -> u32 {
    if o + 4 > b.len() {
        return 0;
    }
    (b[o] as u32) | ((b[o + 1] as u32) << 8) | ((b[o + 2] as u32) << 16) | ((b[o + 3] as u32) << 24)
}

fn rd_i16(b: &[u8], o: usize) -> i16 {
    rd_u16(b, o) as i16
}

/// FNV-1a (与 tools/fujopack.py 同族, 确定性)。
pub fn fnv1a(b: &[u8]) -> u32 {
    let mut h: u32 = 0x811C_9DC5;
    for &x in b {
        h ^= x as u32;
        h = h.wrapping_mul(0x0100_0193);
    }
    h
}

/// 载入结果: 只读文档视图。
#[derive(Clone, Copy)]
pub struct Doc<'a> {
    pub bytes: &'a [u8],
    pub doc_w: u16,
    pub doc_h: u16,
    pub root: u16,
    pub node_count: u32,
    pub nodes: &'a [u8],
    pub strs: &'a [u8],
    pub toks: &'a [u8],
    pub tok_count: u32,
}

/// 校验并载入。任何不合法 -> Err(原因), 由调用方回退。
pub fn load<'a>(b: &'a [u8]) -> Result<Doc<'a>, &'static str> {
    if b.len() < 56 {
        return Err("short header");
    }
    if rd_u32(b, 0) != MAGIC {
        return Err("bad magic");
    }
    if rd_u16(b, 4) != VERSION {
        return Err("bad version");
    }
    let node_count = rd_u32(b, 8);
    let node_off = rd_u32(b, 12) as usize;
    let str_off = rd_u32(b, 16) as usize;
    let str_len = rd_u32(b, 20) as usize;
    let tok_off = rd_u32(b, 24) as usize;
    let tok_count = rd_u32(b, 28);
    let stored_fnv = rd_u32(b, b.len() - 4);

    if node_count as usize > MAX_NODES {
        return Err("too many nodes");
    }
    let node_end = node_off + node_count as usize * NODE_SIZE;
    let str_end = str_off + str_len;
    // 令牌表是变长记录 (u16 len + name + u8 kind + u32 value), 一直排到
    // 文件尾的 FNV 之前 —— 不能按 tok_count*8 估算 (会把表尾令牌截掉)
    let tok_end = b.len().saturating_sub(4);
    if node_end > b.len() || str_end > b.len() || tok_off > tok_end || tok_end > b.len() {
        return Err("section out of range");
    }
    if fnv1a(&b[..b.len() - 4]) != stored_fnv {
        return Err("checksum mismatch");
    }
    Ok(Doc {
        bytes: b,
        doc_w: rd_u16(b, 40),
        doc_h: rd_u16(b, 42),
        root: rd_u16(b, 44),
        node_count,
        nodes: &b[node_off..node_end],
        strs: &b[str_off..str_end],
        toks: &b[tok_off..tok_end],
        tok_count,
    })
}

/// 字符串表: [u32 count][ (u16 len, bytes)... ]
pub fn str_count(doc: &Doc) -> u32 {
    rd_u32(doc.strs, 0)
}

/// 取第 idx 个字符串 (越界返回空)。
pub fn str_at<'a>(doc: &Doc<'a>, idx: u32) -> &'a [u8] {
    let mut o = 4usize;
    let mut i = 0u32;
    let n = str_count(doc);
    while i < n && o + 2 <= doc.strs.len() {
        let len = rd_u16(doc.strs, o) as usize;
        o += 2;
        if i == idx {
            if o + len <= doc.strs.len() {
                return &doc.strs[o..o + len];
            }
            return b"";
        }
        o += len;
        i += 1;
    }
    b""
}

/// 令牌表条目视图。
pub struct Tok<'a> {
    pub name: &'a [u8],
    pub kind: u8,
    pub value: u32,
}

/// 遍历令牌表。
pub fn each_tok<'a>(doc: &Doc<'a>, mut f: impl FnMut(Tok<'a>)) {
    let mut o = 0usize;
    let mut i = 0u32;
    while i < doc.tok_count && o + 3 <= doc.toks.len() {
        let nlen = rd_u16(doc.toks, o) as usize;
        o += 2;
        if o + nlen + 5 > doc.toks.len() {
            break;
        }
        let name = &doc.toks[o..o + nlen];
        o += nlen;
        let kind = doc.toks[o];
        o += 1;
        let value = rd_u32(doc.toks, o);
        o += 4;
        f(Tok { name, kind, value });
        i += 1;
    }
}

/// 节点视图 (固定 64B 记录解码)。
#[derive(Clone, Copy)]
pub struct Node<'a> {
    pub raw: &'a [u8],
}

impl<'a> Node<'a> {
    pub fn at(doc: &Doc<'a>, idx: u32) -> Node<'a> {
        let o = idx as usize * NODE_SIZE;
        if o + NODE_SIZE <= doc.nodes.len() {
            Node {
                raw: &doc.nodes[o..o + NODE_SIZE],
            }
        } else {
            Node { raw: &[] }
        }
    }
    pub fn kind(&self) -> u16 {
        rd_u16(self.raw, 0)
    }
    pub fn id(&self) -> u16 {
        rd_u16(self.raw, 2)
    }
    pub fn flags(&self) -> u16 {
        rd_u16(self.raw, 4)
    }
    pub fn child_count(&self) -> u16 {
        rd_u16(self.raw, 6)
    }
    pub fn x(&self) -> i32 {
        rd_i16(self.raw, 8) as i32
    }
    pub fn y(&self) -> i32 {
        rd_i16(self.raw, 10) as i32
    }
    pub fn w_mode(&self) -> u16 {
        rd_u16(self.raw, 12)
    }
    pub fn w_val(&self) -> u16 {
        rd_u16(self.raw, 14)
    }
    pub fn h_mode(&self) -> u16 {
        rd_u16(self.raw, 16)
    }
    pub fn h_val(&self) -> u16 {
        rd_u16(self.raw, 18)
    }
    pub fn pad_l(&self) -> i32 {
        rd_u16(self.raw, 20) as i32
    }
    pub fn pad_t(&self) -> i32 {
        rd_u16(self.raw, 22) as i32
    }
    pub fn pad_r(&self) -> i32 {
        rd_u16(self.raw, 24) as i32
    }
    pub fn pad_b(&self) -> i32 {
        rd_u16(self.raw, 26) as i32
    }
    pub fn gap(&self) -> i32 {
        rd_u16(self.raw, 28) as i32
    }
    pub fn radius(&self) -> i32 {
        rd_u16(self.raw, 30) as i32
    }
    pub fn elev(&self) -> u16 {
        rd_u16(self.raw, 32)
    }
    pub fn align(&self) -> u16 {
        rd_u16(self.raw, 34)
    }
    pub fn justify(&self) -> u16 {
        rd_u16(self.raw, 36)
    }
    pub fn size(&self) -> u16 {
        rd_u16(self.raw, 38)
    }
    pub fn bg(&self) -> u32 {
        rd_u32(self.raw, 40)
    }
    pub fn fg(&self) -> u32 {
        rd_u32(self.raw, 44)
    }
    pub fn text_id(&self) -> u32 {
        rd_u32(self.raw, 48)
    }
    pub fn tone(&self) -> u16 {
        rd_u16(self.raw, 52)
    }
    pub fn opacity(&self) -> u16 {
        let v = rd_u16(self.raw, 54);
        if v == 0 {
            255
        } else {
            v
        }
    }
    pub fn first_child(&self) -> u32 {
        rd_u32(self.raw, 56)
    }
    pub fn extra(&self) -> u32 {
        rd_u32(self.raw, 60)
    }

    /// 是否容器 (有子布局语义)。
    pub fn is_container(&self) -> bool {
        matches!(
            self.kind(),
            K_SURFACE | K_ROW | K_COL | K_STACK | K_ABS | K_CARD | K_PANEL | K_WINDOW
                | K_LIST | K_TABS | K_TAB | K_ITEM
        )
    }

    /// 主轴方向 (row 类 = 水平)。
    pub fn is_row(&self) -> bool {
        matches!(self.kind(), K_ROW | K_TABS)
    }

    /// 可聚焦 (键盘 Tab 顺序)。
    pub fn focusable(&self) -> bool {
        matches!(
            self.kind(),
            K_BUTTON | K_TOGGLE | K_SLIDER | K_TAB | K_CHIP | K_ITEM
        ) && self.flags() & F_DISABLED == 0
    }

    /// 节点自身矩形 (相对父内容原点)。
    pub fn rel_rect(&self) -> Rect {
        Rect::new(self.x(), self.y(), self.w_val() as i32, self.h_val() as i32)
    }
}
