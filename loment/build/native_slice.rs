// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_slice (loment v0 -> rust)

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


// ==== 本模块 native_slice ====
pub fn sum(xs: &[u32]) -> u32 {
    let mut s: u32 = 0;
    for i in 0..(xs.len() as u32) {
        s = (s + xs[(i) as usize]);
    }
    return s;
}

pub fn call_sum() -> u32 {
    let mut a: [u32; 4] = [1, 2, 3, 4];
    return sum((&a));
}

pub fn first_of(xs: &[u32]) -> u32 {
    return xs[(0) as usize];
}

pub fn call_first() -> u32 {
    let mut a: [u32; 3] = [7, 8, 9];
    return first_of((&a));
}
