// 由 tools/lomentc.py 从 .lomt 转译 —— 请勿手改。
// module native_res (loment v0 -> rust)

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


// ==== 本模块 native_res ====
#[derive(Clone, Copy, PartialEq)]
pub enum Result_u32_u32 {
    Ok(u32),
    Err(u32),
}

pub fn parse_small(v: u32) -> Result_u32_u32 {
    if (v > 10) {
        return Result_u32_u32::Err(1);
    }
    return Result_u32_u32::Ok(v);
}

pub fn twice(v: u32) -> Result_u32_u32 {
    let mut __t15: Result_u32_u32 = parse_small(v);
    let mut x: u32;
    match __t15 {
        Result_u32_u32::Ok(__v15) => {
            x = __v15;
        }
        Result_u32_u32::Err(__e15) => {
            return Result_u32_u32::Err(__e15);
        }
    }
    return Result_u32_u32::Ok((x * 2));
}

pub fn call_ok() -> u32 {
    let mut r: Result_u32_u32 = twice(3);
    match r {
        Result_u32_u32::Ok(v) => {
            return v;
        }
        _ => {
        }
    }
    return 0;
}

pub fn call_err() -> u32 {
    let mut r: Result_u32_u32 = twice(50);
    match r {
        Result_u32_u32::Ok(v) => {
            return v;
        }
        _ => {
            return 99;
        }
    }
}
