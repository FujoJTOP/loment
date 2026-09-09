// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_raii (loment v0 -> rust)

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


// ==== 本模块 native_raii ====
pub struct Guard {
    pub id: u32,
}

impl Drop for Guard {
    fn drop(&mut self) {
        let mut p: *mut u8 = __loment_alloc(4);
        { __loment_store8(p, 0, ((self.id) as u8)); 0u32 };
    }
}

pub fn port_read(port: u16) -> u32 {
    return { let mut __v: u8 = 0; unsafe { core::arch::asm!("in al, dx", in("dx") (port) as u16, out("al") __v); } __v as u32 };
}

pub fn port_write(port: u16, v: u8) -> u32 {
    return { unsafe { core::arch::asm!("out dx, al", in("dx") (port) as u16, in("al") (v) as u8); } 0u32 };
}

pub fn make_guard(v: u32) -> u32 {
    let mut g: Guard = Guard { id: v };
    return g.id;
}
