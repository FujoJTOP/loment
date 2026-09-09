// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_cap (loment v0 -> rust)

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


// ==== 本模块 native_cap ====
// capability blk_write: disk[0..4] revocable
pub const CAP_BLK_WRITE_SPACE: &str = "disk";
pub const CAP_BLK_WRITE_LO: u64 = 0;
pub const CAP_BLK_WRITE_HI: u64 = 4;
pub const CAP_BLK_WRITE_REVOCABLE: bool = true;

#[derive(Clone, Copy)]
pub struct CapDomain { pub space: &'static str, pub lo: u64, pub hi: u64, pub revocable: bool }
pub static CAP_DOMAINS: &[CapDomain] = &[
    CapDomain { space: "disk", lo: 0, hi: 4, revocable: true },
];

pub fn write(slot: u32) -> u32 {
    { let __i: u64 = (slot) as u64; __loment_guard(0, __i, 0, 4); }
    return slot;
}

pub fn ok() -> u32 {
    return write(3);
}

pub fn literal_ok() -> u32 {
    { let __i: u64 = (2) as u64; __loment_guard(0, __i, 0, 4); }
    return 2;
}
