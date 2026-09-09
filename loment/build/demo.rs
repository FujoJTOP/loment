// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module demo (loment v0 -> rust)

// ==== 导入模块 mathutil ====
pub const TWO: u32 = 2;

#[derive(Clone, Copy)]
pub struct Pair {
    pub a: u32,
    pub b: u32,
}

pub fn double(x: u32) -> u32 {
    return (x * TWO);
}

pub fn pair_sum(p: Pair) -> u32 {
    return (p.a + p.b);
}


// ==== 本模块 demo ====
// ---- 来自 lom/fujr.lom (L0 布局单源) ----
// 由 tools/lomc.py 生成 —— 请勿手改；改 lom/*.lom 后重新生成。
// module fujr

pub const MAGIC: u32 = 0x524A5546;
pub const VERSION: u32 = 0x00000001;
pub const SECTION_ALIGN: u32 = 0x00001000;

// enum Tag: u32 (3 项)
pub const TAG_MANIFEST: u32 = 0x00000001;
pub const TAG_EMBED: u32 = 0x00000004;
pub const TAG_DATA: u32 = 0x00000005;
pub const TAG_COUNT: usize = 3;

// record Header: size=64 endian=little packed=true
pub const HEADER_SIZE: usize = 64;
pub const HEADER_MAGIC_OFF: usize = 0;
pub const HEADER_VERSION_OFF: usize = 4;
pub const HEADER_COUNT_OFF: usize = 8;

// record Section: size=32 endian=little packed=true
pub const SECTION_SIZE: usize = 32;
pub const SECTION_TAG_OFF: usize = 0;
pub const SECTION_OFF_OFF: usize = 8;
pub const SECTION_SIZE_OFF: usize = 16;
pub const SECTION_FNV1A_OFF: usize = 24;

// capability blk_write: disk[0..4] revocable
pub const CAP_BLK_WRITE_SPACE: &str = "disk";
pub const CAP_BLK_WRITE_LO: u64 = 0;
pub const CAP_BLK_WRITE_HI: u64 = 4;
pub const CAP_BLK_WRITE_REVOCABLE: bool = true;

pub const MAX_BLKS: u32 = 8;

#[derive(Clone, Copy, PartialEq)]
pub enum Color {
    Red,
    Green,
    Blue,
}

#[derive(Clone, Copy, PartialEq)]
pub enum Shape {
    Circle(u32),
    Square(u32),
    Empty,
}

#[derive(Clone, Copy)]
pub struct Blk {
    pub off: u32,
    pub len: u32,
}

pub fn fib(n: u32) -> u32 {
    let mut a: u32 = 0;
    let mut b: u32 = 1;
    let mut i: u32 = 0;
    while (i < n) {
        let mut t: u32 = (a + b);
        a = b;
        b = t;
        i = (i + 1);
    }
    return a;
}

pub fn gcd(a: u32, b: u32) -> u32 {
    let mut x: u32 = a;
    let mut y: u32 = b;
    while (y != 0) {
        let mut t: u32 = (x % y);
        x = y;
        y = t;
    }
    return x;
}

pub fn popcount(x: u32) -> u32 {
    let mut v: u32 = x;
    let mut c: u32 = 0;
    while (v != 0) {
        if ((v % 2) == 1) {
            c = (c + 1);
        }
        v = (v / 2);
    }
    return c;
}

pub fn in_domain(off: u32) -> bool {
    return ((off >= 0) && (off <= 4));
}

pub fn blk_end(b: Blk) -> u32 {
    return (b.off + b.len);
}

pub fn mask_low(x: u32, n: u32) -> u32 {
    return (x & ((1 << n) - 1));
}

pub fn has_flag(flags: u32, bit: u32) -> bool {
    return ((flags & (1 << bit)) != 0);
}

pub fn sum_array(xs: [u32; 4]) -> u32 {
    let mut s: u32 = 0;
    let mut i: u32 = 0;
    while (i < 4) {
        s = (s + xs[(i) as usize]);
        i = (i + 1);
    }
    return s;
}

pub fn fill_incr() -> [u32; 4] {
    let mut a: [u32; 4] = [0, 0, 0, 0];
    let mut i: u32 = 0;
    while (i < 4) {
        a[(i) as usize] = (i * 2);
        i = (i + 1);
    }
    return a;
}

pub fn color_code(c: Color) -> u32 {
    match c {
        Color::Red => {
            return 1;
        }
        Color::Green => {
            return 2;
        }
        _ => {
            return 0;
        }
    }
}

pub fn sum_range(n: u32) -> u32 {
    let mut s: u32 = 0;
    for i in 0..n {
        s = (s + i);
    }
    return s;
}

pub fn quadruple(x: u32) -> u32 {
    return double(double(x));
}

pub fn max_blocks() -> u32 {
    return MAX_BLKS;
}

pub fn shape_area(s: Shape) -> u32 {
    match s {
        Shape::Circle(r) => {
            return ((r * r) * 3);
        }
        Shape::Square(a) => {
            return (a * a);
        }
        _ => {
            return 0;
        }
    }
}
