// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_mem (loment v0 -> rust)

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


// ==== 本模块 native_mem ====
pub fn heap_roundtrip() -> u32 {
    let mut p: *mut u8 = __loment_alloc(16);
    { __loment_store8(p, 0, 7); 0u32 };
    { __loment_store8(p, 1, 35); 0u32 };
    let mut s: u32 = 0;
    let mut i: u32 = 0;
    while (i < 2) {
        s = (s + (__loment_load8(p, i) as u32));
        i = (i + 1);
    }
    { let _ = p; 0u32 };
    return s;
}

pub fn wrap_add(a: u32, b: u32) -> u32 {
    return (a + b);
}

pub fn safe_div(a: u32, b: u32) -> u32 {
    if (b == 0) {
        return 0;
    }
    return (a / b);
}

pub fn atomic_roundtrip() -> u32 {
    let mut p: *mut u8 = __loment_alloc(4);
    { __loment_store8(p, 0, 0); 0u32 };
    let mut a: u32 = unsafe { (*((p) as *const core::sync::atomic::AtomicU32)).fetch_add(5, core::sync::atomic::Ordering::SeqCst) };
    let mut b: u32 = unsafe { (*((p) as *const core::sync::atomic::AtomicU32)).fetch_add(3, core::sync::atomic::Ordering::SeqCst) };
    return ((a + b) + (__loment_load8(p, 0) as u32));
}
