// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_mut (loment v0 -> rust)

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


// ==== 本模块 native_mut ====
pub fn fill(xs: &mut [u32], v: u32) -> u32 {
    let mut n: u32 = (xs.len() as u32);
    let mut i: u32 = 0;
    while (i < n) {
        xs[(i) as usize] = (v + i);
        i = (i + 1);
    }
    return n;
}

pub fn call_fill() -> u32 {
    let mut a: [u32; 4] = [0, 0, 0, 0];
    let mut n: u32 = fill((&mut a), 10);
    return ((n + a[(0) as usize]) + a[(3) as usize]);
}

pub fn read_only(xs: &[u32]) -> u32 {
    return xs[(0) as usize];
}

pub fn call_read_only() -> u32 {
    let mut a: [u32; 2] = [5, 6];
    return (read_only((&a)) + read_only((&mut a)));
}
