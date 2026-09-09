// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_agg (loment v0 -> rust)

// ---- Loment 运行时 (M15 堆分配) ----
#[allow(static_mut_refs)]
static mut __LOMENT_HEAP: [u8; 65536] = [0; 65536];
#[allow(static_mut_refs)]
static mut __LOMENT_OFF: usize = 0;

fn __loment_alloc(size: u32) -> *mut u8 {
    unsafe {
        let off = __LOMENT_OFF;
        let end = off + size as usize;
        if end > 65536 {
            panic!("loment: heap oom");
        }
        __LOMENT_OFF = end;
        __LOMENT_HEAP.as_mut_ptr().add(off)
    }
}
fn __loment_load8(p: *mut u8, off: u32) -> u8 {
    unsafe { *p.add(off as usize) }
}
fn __loment_store8(p: *mut u8, off: u32, v: u8) {
    unsafe { *p.add(off as usize) = v; }
}

// ---- P4: 能力域运行时 ----
#[allow(static_mut_refs)]
static mut __LOMENT_AUDIT: [u64; 16] = [0; 16];

fn __loment_guard(cap: usize, idx: u64, lo: u64, hi: u64) {
    unsafe {
        __LOMENT_AUDIT[cap] += 1;
    }
    if idx < lo || idx > hi {
        panic!("loment: capability {} violation at {}", cap, idx);
    }
}


// ==== 本模块 native_agg ====
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

pub fn blk_end() -> u32 {
    let mut b: Blk = Blk { off: 3, len: 5 };
    return (b.off + b.len);
}

pub fn array_sum() -> u32 {
    let mut a: [u32; 4] = [1, 2, 3, 4];
    let mut s: u32 = 0;
    for i in 0..4 {
        s = (s + a[(i) as usize]);
    }
    a[(0) as usize] = 100;
    return (s + a[(0) as usize]);
}

pub fn shape_area(tag: u32, v: u32) -> u32 {
    let mut s: Shape = Shape::Empty;
    if (tag == 0) {
        s = Shape::Circle(v);
    } else {
        s = Shape::Square(v);
    }
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

pub fn short_circuit(x: u32) -> u32 {
    let mut n: u32 = 0;
    if ((x > 0) && (x < 10)) {
        n = (n + 1);
    }
    if ((x == 0) || (x == 5)) {
        n = (n + 10);
    }
    return n;
}
