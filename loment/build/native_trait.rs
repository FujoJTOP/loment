// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_trait (loment v0 -> rust)

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


// ==== 本模块 native_trait ====
#[derive(Clone, Copy)]
pub struct Small {
    pub v: u32,
}

#[derive(Clone, Copy)]
pub struct Big {
    pub v: u32,
}

pub fn Small_measure(__self: Small) -> u32 {
    return __self.v;
}

pub fn Big_measure(__self: Big) -> u32 {
    return (__self.v * 10);
}

pub fn call_small() -> u32 {
    let mut s: Small = Small { v: 7 };
    return Small_measure(s);
}

pub fn call_big() -> u32 {
    let mut b: Big = Big { v: 7 };
    return Big_measure(b);
}
