// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_gen (loment v0 -> rust)

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


// ==== 本模块 native_gen ====
#[derive(Clone, Copy, PartialEq)]
pub enum Opt_u32 {
    Some(u32),
    None,
}

#[derive(Clone, Copy)]
pub struct Pair_u32 {
    pub a: u32,
    pub b: u32,
}

pub fn call_pair_max() -> u32 {
    let mut p: Pair_u32 = Pair_u32 { a: 7, b: 3 };
    return max_u32(p.a, p.b);
}

pub fn call_max_i32() -> i32 {
    let mut x: i32 = (-5);
    let mut y: i32 = 3;
    return max_i32(x, y);
}

pub fn call_opt() -> u32 {
    let mut a: Opt_u32 = Opt_u32::Some(42);
    let mut b: Opt_u32 = Opt_u32::None;
    let mut s: u32 = 0;
    match a {
        Opt_u32::Some(v) => {
            s = v;
        }
        _ => {
            s = 1;
        }
    }
    match b {
        Opt_u32::Some(v) => {
            s = (s + v);
        }
        _ => {
            s = (s + 2);
        }
    }
    return s;
}

pub fn max_u32(a: u32, b: u32) -> u32 {
    if (a > b) {
        return a;
    }
    return b;
}

pub fn max_i32(a: i32, b: i32) -> i32 {
    if (a > b) {
        return a;
    }
    return b;
}
