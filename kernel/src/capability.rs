//! capability.rs — M91: 权限与审计 (四件套④): 能力表 + 审计日志
//!
//! 能力表: 8 槽 {perm u64, granted bool}; 审计环 32 项
//! {ts, action, subject, result}; cap_check 拒绝自动入审计。
//! 接口: 0x8101 cap_grant(idx,perm) / 0x8102 cap_check(idx,perm) /
//!       0x8103 aud_log(action,subject) / 0x8104 aud_read(ptr,cap)。
//!
//! M112 (AI For Next ③ 动作通道): 0x8105 cap_exec(act,arg0,arg1) ——
//! 模型输出 → 内核执行, 经 cap_check 授权 (槽 6 = exec 槽, perm bit=act-1)
//! + aud_log(action=2)。动作: KILL/ISOLATE/RESUME/SET_CFG/ACK;
//! LAUNCH 需 syscall 现场 (在 syscall.rs 分发内实现)。
//! 0x8106 cfg_get(key): 配置读取 (anom 阈值/自动隔离等, SET 经 cap_exec)。

use crate::serial;

const N_CAP: usize = 8;
const N_AUD: usize = 32;

static mut CAP_PERM: [u64; N_CAP] = [0; N_CAP];
static mut CAP_GRANT: [bool; N_CAP] = [false; N_CAP];

static mut AUD: [(u64, u64, u64, u64); N_AUD] = [(0, 0, 0, 0); N_AUD];
static mut AUD_POS: u64 = 0;
static mut AUD_NUM: u64 = 0;
static mut DENIES: u64 = 0;

// ---- M112: exec 动作集 (act 1..=6, perm bit = act-1) ----
pub const EXEC_SLOT: usize = 6;
pub const ACT_KILL: u64 = 1;
pub const ACT_ISOLATE: u64 = 2;
pub const ACT_LAUNCH: u64 = 3;
pub const ACT_SET_CFG: u64 = 4;
pub const ACT_RESUME: u64 = 5;
pub const ACT_ACK: u64 = 6;
/// W36/B-2: act7 = BOX_CMD (盒供应商调用门; BOX_XFR act8 留 v1)。
pub const ACT_BOX_CMD: u64 = 7;
pub const ALL_ACTS: u64 = 0x7F;

/// M112: 配置槽 (key 1..=8): 1=anom 置信阈值 (默认 50), 2=自动隔离 (默认 0)。
/// B24: 政策值域表 (key -> (min, max)); 越界拒绝 (由 G3/m151 暴露: 无门 -> 污染接受)。
static mut CFG: [(u64, u64); 8] = [(0, 0); 8];
const CFG_DOMAIN: [(u64, u64); 8] = [
    (0, 100), // 1 anom 置信阈值
    (0, 1),   // 2 自动隔离 on/off
    (0, 100), // 3 策略时段 start
    (0, 100), // 4 策略时段 end
    (0, 100), // 5 策略参数
    (0, 100), // 6 审计掩码
    (0, 100), // 7 τ_high (trust 加宽)
    (0, 100), // 8 τ_low (trust 收缩)
];

pub fn cfg_set(key: u64, val: u64) -> i64 {
    if key == 0 || key > 8 {
        return -22;
    }
    let (lo, hi) = CFG_DOMAIN[(key - 1) as usize];
    if val < lo || val > hi {
        serial::write_str("cfg  : reject #");
        crate::syscall::debug_dec(key);
        serial::write_str(" = ");
        crate::syscall::debug_dec(val);
        serial::write_line(" (out of value domain)");
        return -22;
    }
    // B24: S3 策略不变式 —— τ_high > τ_low (加宽阈必须高于收缩阈),
    // 否则 "quality >= tau_high" 恒真 -> 加宽无条件放行 (Goodhart 绕过值域).
    // W46: 与真实默认对齐 (τ_low 35 / τ_high 45, 原回退 30/70 与部署不符) +
    // 政策包络: τ_high ≥ 35 (推导梯队下界) / τ_low ≤ 45 —— 封堵域内写序攻击
    // (8:0;7:1: 7:1 越过 <35 下限即拒; 每次单键写, 对照"当前值或真实默认")。
    if key == 7 {
        let tau_low;
        unsafe {
            tau_low = if CFG[7].0 == 8 { CFG[7].1 } else { 35 };
        }
        if val < 35 || val <= tau_low {
            serial::write_line("cfg  : reject #7 (tau invariant: tau_high < 35 or <= tau_low)");
            return -22;
        }
    }
    if key == 8 {
        let tau_high;
        unsafe {
            tau_high = if CFG[6].0 == 7 { CFG[6].1 } else { 45 };
        }
        if val > 45 || val >= tau_high {
            serial::write_line("cfg  : reject #8 (tau invariant: tau_low > 45 or >= tau_high)");
            return -22;
        }
    }
    unsafe {
        CFG[(key - 1) as usize] = (key, val);
        serial::write_str("cfg  : set #");
        crate::syscall::debug_dec(key);
        serial::write_str(" = ");
        crate::syscall::debug_dec(val);
        serial::write_line("");
    }
    0
}

/// 0x8106: 配置读 (未设置返回默认)。
pub fn fujo_cfg_get(key: u64) -> i64 {
    let def = match key {
        1 => 50,  // anom 阈值
        2 => 0,   // 自动隔离 off
        7 => 45,  // W46: τ_high 声明重标定 —— v2 语义金标 T*=[0.429,0.454), 取区间内 0.45
                  // (0.46 为 10× 批的声明, 已在 v2 区间外; 保留为可配置 legacy 保守值)
        8 => 35,  // W32: 信任自适应域 收缩阈值 τ_low (B 组支撑中带 [0.334,0.369])
        _ => 0,
    };
    if key == 0 || key > 8 {
        return -22;
    }
    unsafe {
        let (k, v) = CFG[(key - 1) as usize];
        if k == key {
            v as i64
        } else {
            def
        }
    }
}

/// M116: exec 动作授权检查 —— 当前任务域门 (域 0 读全局槽 6, 兼容不变)。
/// W36: act 7 (BOX_CMD) 同一门 (per-provider 域宽与模型同构)。
pub fn exec_authorized(act: u64) -> bool {
    if act == 0 || act > 8 {
        return false;
    }
    let (g, p) = domain_perm(cur_dom());
    g && (p & (1u64 << (act - 1))) != 0
}

/// M112: exec 审计落笔。
pub fn aud_exec(act: u64, result: u64) {
    aud_note(2, act, result); // action=2 (exec)
    unsafe {
        if result == 1 {
            DENIES += 1;
        }
    }
}

/// M112: 0x8105 主体 —— 非 LAUNCH 动作 (LAUNCH 在 syscall.rs 分发内, 需现场)。
pub fn fujo_cap_exec(act: u64, a0: u64, a1: u64) -> i64 {
    if !exec_authorized(act) {
        aud_exec(act, 1);
        serial::write_str("cap  : deny exec #");
        crate::syscall::debug_dec(act);
        serial::write_line("");
        return -1;
    }
    let rc = match act {
        ACT_KILL => crate::sched::kill_task(a0 as usize),
        ACT_ISOLATE => crate::sched::task_suspend(a0 as usize),
        ACT_RESUME => crate::sched::task_resume(a0 as usize),
        ACT_SET_CFG => cfg_set(a0, a1),
        ACT_ACK => crate::ai::anom_ack(),
        _ => -22,
    };
    aud_exec(act, if rc == 0 { 0 } else { 1 });
    serial::write_str("cap  : exec #");
    crate::syscall::debug_dec(act);
    serial::write_line(if rc == 0 { " ok" } else { " rc!=0" });
    rc
}

/// 审计落笔 (统一环; box 审计经此 — W36/B-2)。
pub(crate) fn aud_note(action: u64, subject: u64, result: u64) {
    unsafe {
        AUD[(AUD_POS % N_AUD as u64) as usize] =
            (crate::interrupts::ticks(), action, subject, result);
        AUD_POS += 1;
        AUD_NUM += 1;
    }
}

/// 0x8101
pub fn fujo_cap_grant(idx: u64, perm: u64) -> i64 {
    let i = (idx as usize).min(N_CAP - 1);
    unsafe {
        CAP_PERM[i] = perm;
        CAP_GRANT[i] = true;
        serial::write_str("cap  : grant #");
        crate::syscall::debug_dec(i as u64);
        serial::write_str(" perm=");
        crate::syscall::debug_hex(perm);
        serial::write_line("");
    }
    0
}

/// 0x8102: 检查 (deny 记审计)。
pub fn fujo_cap_check(idx: u64, perm: u64) -> i64 {
    let i = (idx as usize).min(N_CAP - 1);
    unsafe {
        if CAP_GRANT[i] && (CAP_PERM[i] & perm) == perm {
            return 0;
        }
        DENIES += 1;
        aud_note(1, i as u64, 1); // action=1 (check), result=1 (deny)
        serial::write_str("cap  : deny #");
        crate::syscall::debug_dec(i as u64);
        serial::write_line("");
        -1
    }
}

/// 0x8103
pub fn fujo_aud_log(action: u64, subject: u64) -> i64 {
    aud_note(action, subject, 0);
    0
}

/// 0x8104: 条目拷贝 (32B 每项: ts, action, subject, result)。
pub fn fujo_aud_read(ptr: u64, cap: u64) -> i64 {
    unsafe {
        let n = ((cap / 32) as usize).min(N_AUD).min(AUD_NUM as usize);
        for i in 0..n {
            let idx = (AUD_POS as usize).wrapping_add(N_AUD - n + i) % N_AUD;
            let (ts, a, s, r) = AUD[idx];
            let w = (ptr as *mut u64).add(i * 4);
            w.write(ts);
            w.add(1).write(a);
            w.add(2).write(s);
            w.add(3).write(r);
        }
        let na = (cap / 32).min(N_AUD as u64).min(AUD_NUM);
        na as i64
    }
}

pub fn denies() -> u64 {
    unsafe { DENIES }
}

/// M118 (R1): 审计条目总数 (公理化自检)。
pub fn aud_num() -> u64 {
    unsafe { AUD_NUM }
}

/// W19: 统一审计导出 (0x8C01) —— 双环同构:
/// out 布局 = u64[0]=cap_n, u64[1]=ai_n, 随后 cap 条目×32B (kind=1,
/// [ts,action,subject,result]), 再 ai 条目×32B (kind=2, [engine,duty,result,0])。
/// 返回总条数 (cap_n + ai_n)。
#[no_mangle]
pub extern "C" fn fujo_unified_aud(ptr: u64, cap: u64) -> i64 {
    if !(0x400000..0xC00000).contains(&ptr) {
        return -14;
    }
    unsafe {
        let w = ptr as *mut u64;
        let cap_n = (0u64).max(clamp_aud_n(cap));
        let ai_n = crate::ai::ai_aud_count() as u64;
        // 先写统计头
        w.write(cap_n);
        w.add(1).write(ai_n);
        let body = ptr + 16;
        // cap 条目
        let n = cap_n as usize;
        for i in 0..n {
            let idx = (AUD_POS as usize).wrapping_add(N_AUD - n + i) % N_AUD;
            let (ts, a, s, r) = AUD[idx];
            let e = (body as *mut u64).add(i * 4);
            e.write(ts);
            e.add(1).write(a);
            e.add(2).write(s);
            e.add(3).write(r);
        }
        // ai 条目
        let off = (body as u64) + (n as u64) * 32;
        let ai_ret = crate::ai::ai_aud_export_32(off, ai_n as usize);
        (cap_n + ai_ret as u64) as i64
    }
}

fn clamp_aud_n(cap: u64) -> u64 {
    let n = ((cap.saturating_sub(16)) / 32).min(N_AUD as u64).min(unsafe { AUD_NUM });
    n
}

/// M118 (R1): 最近一条审计 (ts, action, subject, result)。
pub fn aud_tail() -> (u64, u64, u64, u64) {
    unsafe {
        if AUD_NUM == 0 {
            return (0, 0, 0, 0);
        }
        AUD[(AUD_POS.wrapping_sub(1) % N_AUD as u64) as usize]
    }
}

// ---------------------------------------------------------------------------
// M116 (W9) · 权限域: 域 := { cap 集合, 地址空间, 中断域 }, 支持可撤销。
// 域 0 = 系统域 (兼容: 读全局 exec 槽 6, 全地址空间, 中断可配);
// 域 1..=4 = 显式域 {perm: act 位掩码, as_mask: region 位, irq: bool}。
// 爆炸半径: 任何 cap_exec 先过当前任务域门; LAUNCH 入口受 as_mask 约束;
// 中断配置受 irq 约束; 撤销后 granted=false -> 全拒但仍可读。
// ---------------------------------------------------------------------------

pub const DOM_MAX: usize = 5; // 0=系统域 + 1..=4
pub const REGION_LOW: u64 = 1; // 0x400000..0xC00000
pub const REGION_HIGH: u64 = 2; // 0x1000000..0x1080000 (M108 窗口镜像)
pub const REGION_ANY: u64 = REGION_LOW | REGION_HIGH;

#[derive(Clone, Copy)]
pub struct Domain {
    pub perm: u64, // cap 集合 (bit=act-1, 同 exec 槽语义)
    pub granted: bool,
    pub as_mask: u64, // 地址空间 (bit=region-1)
    pub irq: bool, // 中断域: 允许配置 0x6D01/0x6B04
}

static mut DOM: [Domain; DOM_MAX] = [
    Domain { perm: 0, granted: true, as_mask: REGION_ANY, irq: true }, // 0: 系统域 (兼容)
    Domain { perm: 0, granted: false, as_mask: 0, irq: false },
    Domain { perm: 0, granted: false, as_mask: 0, irq: false },
    Domain { perm: 0, granted: false, as_mask: 0, irq: false },
    Domain { perm: 0, granted: false, as_mask: 0, irq: false },
];

/// 当前任务的域 id (sched 提供; 未绑定 = 系统域 0)。
fn cur_dom() -> u64 {
    crate::sched::current_domain_id()
}

/// 域 perm 读取: 域 0 兼容全局 exec 槽 (M91/M112 语义不变)。
pub fn domain_perm(d: u64) -> (bool, u64) {
    unsafe {
        if d >= DOM_MAX as u64 {
            return (false, 0);
        }
        if d == 0 {
            (CAP_GRANT[EXEC_SLOT], CAP_PERM[EXEC_SLOT])
        } else {
            (DOM[d as usize].granted, DOM[d as usize].perm)
        }
    }
}

/// FUI (docs/138 U7d): 活跃显式域数量 (域 1..=4 中 granted 的个数)。
pub fn dom_active() -> u64 {
    let mut n = 0u64;
    for d in 1..DOM_MAX as u64 {
        if domain_perm(d).0 {
            n += 1;
        }
    }
    n
}

/// M116: 0x8107 创建域 (1..=4, 首个空闲槽) -> 域 id; -17 表满。
pub fn fujo_dom_create(perm: u64, as_mask: u64, irq: u64) -> i64 {
    unsafe {
        for i in 1..DOM_MAX {
            if !DOM[i].granted {
                DOM[i] = Domain {
                    perm: perm & ALL_ACTS,
                    granted: true,
                    as_mask: as_mask & REGION_ANY,
                    irq: irq != 0,
                };
                aud_dom(i as u64, 0);
                serial::write_str("dom  : create #");
                crate::syscall::debug_dec(i as u64);
                serial::write_str(" perm=");
                crate::syscall::debug_hex(DOM[i].perm);
                serial::write_str(" as=");
                crate::syscall::debug_hex(DOM[i].as_mask);
                serial::write_line("");
                return i as i64;
            }
        }
    }
    -17 // -EEXIST: 无空闲槽
}

/// M116: 0x8109 撤销域 (granted=false; 之后该域所有 cap_exec/配置被拒)。
pub fn fujo_dom_revoke(id: u64) -> i64 {
    unsafe {
        if id == 0 || id >= DOM_MAX as u64 {
            return -22;
        }
        DOM[id as usize].granted = false;
        aud_dom(id, 1);
        serial::write_str("dom  : revoke #");
        crate::syscall::debug_dec(id);
        serial::write_line("");
        0
    }
}

/// W32: 信任自适应域 —— 由 dom_admit 按模型质量调域 perm (加宽/收缩;
/// 域表变化落审计 action=3, 与 revoke 同一审计面)。
pub fn dom_adjust(id: u64, perm: u64) -> i64 {
    unsafe {
        if id == 0 || id >= DOM_MAX as u64 {
            return -22;
        }
        DOM[id as usize].perm = perm & ALL_ACTS;
        if perm != 0 {
            DOM[id as usize].granted = true;
        }
        aud_dom(id, 2);
        serial::write_str("dom  : adjust #");
        crate::syscall::debug_dec(id);
        serial::write_str(" perm=");
        crate::syscall::debug_hex(DOM[id as usize].perm);
        serial::write_line("");
    }
    0
}

/// 域审计: action=3 (domain 操作), subject=域 id, result=0/1。
pub fn aud_dom(id: u64, result: u64) {
    aud_note(3, id, result);
}

/// M116: 0x810A 域表读回 (5×5 u64: [id, perm, granted, as_mask, irq])。
pub fn fujo_dom_info(ptr: u64) -> i64 {
    let b = ptr as *mut u64;
    unsafe {
        for i in 0..DOM_MAX {
            let (g, p) = domain_perm(i as u64);
            b.add(i * 5).write(i as u64);
            b.add(i * 5 + 1).write(p);
            b.add(i * 5 + 2).write(if g { 1 } else { 0 });
            b.add(i * 5 + 3).write(DOM[i].as_mask);
            b.add(i * 5 + 4).write(if DOM[i].irq { 1 } else { 0 });
        }
    }
    0
}

/// 当前域是否允许配置中断 (0x6D01/0x6B04 门)。
pub fn dom_irq_ok() -> bool {
    let d = cur_dom();
    if d == 0 {
        return true;
    }
    unsafe { DOM[d as usize].irq }
}

/// LAUNCH 入口是否落在当前域地址空间内 (domain 0 = 全区域)。
pub fn launch_entry_ok(entry: u64) -> bool {
    let d = cur_dom();
    let region = if (0x400000..0xC00000).contains(&entry) {
        REGION_LOW
    } else if (0x1000000..0x1080000).contains(&entry) {
        REGION_HIGH
    } else {
        0
    };
    if region == 0 {
        return false;
    }
    if d == 0 {
        return true;
    }
    unsafe { (DOM[d as usize].as_mask & region) != 0 }
}
