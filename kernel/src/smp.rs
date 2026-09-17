//! smp.rs — M64: 多核并行 v0 (探测 / 调度亲和 / 负载均衡统计)
//!
//! 范围 (M64): CPUID 核探测 (leaf 1 EBX[23..16]) + 亲和位图 (每任务
//! AFF[tid], 默认 0xFF) + 调度侧核归属统计 (负载均衡 v0: 每任务每次
//! PIT 切换按其亲和最低置位 bit 记入该核负载)。单 PIT 时钟源下先记录
//! 策略/统计; 真 SMP 启动 (多 APIC 定时器/每核 TSS) 由 M65 承接。
//!
//! 接口: 0x6A01 aff_set(tid, mask) / 0x6A02 aff_get(tid) /
//!       0x6A03 smp_info(ptr) 写 u32×4: (ncpu, aff_n, locks, unknown)
//!       0x6A04 smp_stats(ptr) 写 u32×4: (ncpu, core0_count, core1_count, switches)

use crate::serial;

// CPUID leaf 1 桥 (rbx - LLVM 保留)。
core::arch::global_asm!(r#"
    .text
    .global fujo_cpuid_leaf1
    .p2align 4
fujo_cpuid_leaf1:
    push rbx
    push rcx
    push rdx
    mov rax, 1
    xor rcx, rcx
    cpuid
    mov [rdi + 0], eax
    mov [rdi + 4], ebx
    mov [rdi + 8], edx
    mov [rdi + 12], ecx
    pop rdx
    pop rcx
    pop rbx
    ret
"#);

extern "C" {
    fn fujo_cpuid_leaf1(buf: *mut u32);
}

static mut NCPU: u32 = 1;
static mut AFF: [u8; 8] = [0xFF; 8]; // 每任务亲和位图 (仅低 2 bit 有效 v0)
static mut CORE0: u64 = 0;
static mut CORE1: u64 = 0;
static mut SWITCHES: u64 = 0;

/// 探测并缓存核数 (启动时调一次; 并行失效时不重复探测)。
pub fn init() {
    let mut b = [0u32; 4];
    unsafe { fujo_cpuid_leaf1(b.as_mut_ptr()) };
    let logical = ((b[1] >> 16) & 0xFF) + 1; // EBX[23..16] 逻辑核数 - 1
    unsafe {
        NCPU = logical.max(1).min(2); // v0: 上限 2 核统计桶
    }
    serial::write_str("smp  : cpuid logical CPUs = ");
    let nc = unsafe { NCPU };
    serial::write_str(if nc >= 2 { "2 (affinity v0 armed)" } else { "1 (single-core mode)" });
    serial::write_line("");
}

pub fn ncpu() -> u32 {
    unsafe { NCPU }
}

// ---------------------------------------------------------------------------
// 亲和位图
// ---------------------------------------------------------------------------

pub fn aff_set(tid: u64, mask: u64) -> i64 {
    let t = (tid as usize).min(7);
    unsafe { AFF[t] = (mask as u8) & 0x03 };
    0
}

pub fn aff_get(tid: u64) -> i64 {
    let t = (tid as usize).min(7);
    unsafe { AFF[t] as i64 }
}

// ---------------------------------------------------------------------------
// 负载均衡 v0: 每次用户态切换把任务核归属记入统计。
// 核选择: 亲和位图最低置位 bit (0xFF → 轮换, 伪随机取 task id & ncpu)。
// ---------------------------------------------------------------------------

pub fn balance_task(tid: usize) {
    let nc = unsafe { NCPU } as u64;
    if nc < 2 {
        unsafe { CORE0 += 1 };
        return;
    }
    unsafe {
        let m = AFF[tid % 8] as u64;
        let core = if m == 0xFF { (tid as u64) % 2 } else { m.trailing_zeros() as u64 };
        if core == 0 {
            CORE0 += 1;
        } else {
            CORE1 += 1;
        }
    }
}

pub fn note_switch(tid: usize) {
    unsafe {
        SWITCHES += 1;
    }
    balance_task(tid);
}

/// 0x6A04
pub fn fujo_smp_stats(ptr: u64) -> i64 {
    unsafe {
        let w = ptr as *mut u32;
        w.write(NCPU);
        w.add(1).write(CORE0 as u32);
        w.add(2).write(CORE1 as u32);
        w.add(3).write(SWITCHES as u32);
    }
    0
}

/// FUI (docs/138 U7d): (核心数, 调度切换计数) —— 内核功能嵌入 UI 的数据面。
pub fn stats() -> (u32, u64) {
    unsafe { (NCPU, SWITCHES) }
}

// ---------------------------------------------------------------------------
// M65: 每核 TSS / 中断注入优化 v0
//
//  - 双 TSS (GDT 槽 5/6=TSS0 0x28, 7/8=TSS1 0x38), 每核独立 RSP0
//    (核0=STK_MAIN_TOP, 核1=STK_CORE1_TOP) —— gdt.rs。
//  - LAPIC 探测: 基址 0xFEE00000 读 ID 寄存器 (offset 0x20 bits 31..24)
//    —— QEMU TCG 虚拟 APIC 提供真实 CPU 标识。
//  - 中断注入优化 v0: IRQ_ROUTE 掩码 (默认 3=双核轮转); 每次 PIT 中断
//    按掩码归属核桶 (3 → 轮转 0/1; 1 → 核0; 2 → 核1)。
// ---------------------------------------------------------------------------

static mut IRQ_ROUTE: u64 = 3;
static mut IRQ_INJ: u64 = 0;
static mut IRQ_R0: u64 = 0;
static mut IRQ_R1: u64 = 0;

fn lapic_id() -> u32 {
    // CPUID leaf 1 EBX[31..24] = 初始 APIC ID (BSP=0)。LAPIC MMIO
    // (0xFEE00000) 未映射进 boot 页表 (M65 已知限制, 记录见文档 14);
    // v0 以 CPUID ID 为核标识 —— QEMU TCG 下与虚拟 LAPIC ID 一致。
    let mut b = [0u32; 4];
    unsafe { fujo_cpuid_leaf1(b.as_mut_ptr()) };
    (b[1] >> 24) & 0xFF
}

/// PIT 中断侧钩子 (sched 桩每 tick 调; 先于任何切换逻辑)。
pub fn intr_note() {
    crate::irq::note(); // M67: 中断合并/成本记账
    unsafe {
        IRQ_INJ += 1;
        let m = IRQ_ROUTE & 3;
        let core = match m {
            3 => IRQ_INJ % 2,
            0 => 2, // 全禁: 不入桶 (保持总和语义)
            _ => m - 1, // 1 → 0, 2 → 1
        };
        if core == 0 {
            IRQ_R0 += 1;
        } else if core == 1 {
            IRQ_R1 += 1;
        }
    }
}

/// 0x6B01: 当前核 id (LAPIC).
pub fn fujo_core_id() -> i64 {
    lapic_id() as i64
}

/// 0x6B02: tss_info(ptr) — u64×3: (tss0_rsp0, tss1_rsp0, gdt_limit)。
pub fn fujo_tss_info(ptr: u64) -> i64 {
    unsafe {
        let (a, b) = crate::gdt::tss_rsp0s();
        let w = ptr as *mut u64;
        w.write(a);
        w.add(1).write(b);
        w.add(2).write(16 * 8 - 1);
    }
    0
}

/// 0x6B04: irq_route(mask) — 中断目标核掩码。
pub fn fujo_irq_route(mask: u64) -> i64 {
    // M116: 中断域门 —— 域无 IRQ 权限禁止改路由。
    if !crate::capability::dom_irq_ok() {
        crate::serial::write_line("irq  : deny route (domain irq off)");
        return -1;
    }
    unsafe {
        IRQ_ROUTE = mask & 3;
    }
    0
}

/// 0x6B05: irq_stats(ptr) — u32×4: (lapic_id, r0, r1, inj)。
pub fn fujo_irq_stats(ptr: u64) -> i64 {
    unsafe {
        let w = ptr as *mut u32;
        w.write(lapic_id());
        w.add(1).write(IRQ_R0 as u32);
        w.add(2).write(IRQ_R1 as u32);
        w.add(3).write(IRQ_INJ as u32);
    }
    0
}

// ---------------------------------------------------------------------------
// W17: AP 启动 (SMP) —— 映射 LAPIC MMIO + 拷贝 trampoline@0x8000 + SIPI
// ---------------------------------------------------------------------------

pub static mut AP_ONLINE: bool = false;
// AP 栈 = TSS1.rsp0 (STK_CORE1_TOP, gdt.rs M65); ap_entry asm 已设置, 中断经 TSS1 切换。

fn mmio_wr32(addr: u64, v: u32) {
    unsafe {
        core::arch::asm!("mov [{}], eax", in(reg) addr, in("eax") v, options(nomem));
    }
}

fn mmio_rd32(addr: u64) -> u32 {
    let v: u32;
    unsafe {
        core::arch::asm!("mov eax, [{}]", in(reg) addr, out("eax") v, options(nomem));
    }
    v
}

fn rdtsc() -> u64 {
    let lo: u32;
    let hi: u32;
    unsafe {
        core::arch::asm!("rdtsc", out("eax") lo, out("edx") hi, options(nomem, nostack));
    }
    ((hi as u64) << 32) | lo as u64
}

/// W17: TCG 忙等 (INIT->SIPI 间隔用; PIT 被屏蔽时唯一可靠)。
fn delay_ticks(n: u64) {
    let t0 = rdtsc();
    while rdtsc().wrapping_sub(t0) < n {
        core::hint::spin_loop();
    }
}

/// W20: LAPIC 基址探测 —— CPUID leaf1 EDX bit9 (APIC 存在) 门 + MSR 0x1B
/// (APICBASE) bits 12..35 = 基址; 失败回退 0xFEE00000 (常规默认)。
pub fn lapic_base() -> u64 {
    let mut b = [0u32; 4];
    unsafe { fujo_cpuid_leaf1(b.as_mut_ptr()) };
    if b[3] & (1 << 9) == 0 {
        serial::write_line("smp  : APIC absent (cpuid edx.9=0) - fallback 0xFEE00000");
        return 0xFEE00000;
    }
    let lo: u32;
    let hi: u32;
    unsafe {
        core::arch::asm!(
            "rdmsr",
            in("ecx") 0x1Bu32,
            out("eax") lo,
            out("edx") hi,
            options(nomem, nostack, preserves_flags)
        );
    }
    let base = (((lo as u64) & 0xFFFFF000) | (((hi as u64) & 0xF) << 32)) & !0xFFF;
    if base == 0 {
        0xFEE00000
    } else {
        base
    }
}

static mut LAPIC_BASE: u64 = 0xFEE00000;

#[inline]
fn lapic(off: u64) -> u64 {
    unsafe { LAPIC_BASE + off }
}

/// W17b: 等 ICR 投递完成 (读 ICR 低 32 bit12 = delivery status, 1=发送中)。
fn icr_idle_wait() {
    let mut spins = 0u32;
    while ((mmio_rd32(lapic(0x300)) >> 12) & 1) == 1 && spins < 100_000 {
        core::hint::spin_loop();
        spins += 1;
    }
}

/// W20: ICR 投递语义模式 (0 = QEMU 适配, 1 = Intel SDM 真机)。
/// 实证 (W17b): QEMU 写 0x300 (低 32) **触发**投递 (用已存高 32 dest);
/// Intel SDM 10.5.2.2: 写高 32 触发 (低 32 仅存储)。二者相反 ——
/// QEMU 下 Intel 路径会把 INIT 投给 dest=0 (BSP) 冻结 (负验证: docs/74)。
/// W31 修正: 判据 = VBE 是 QEMU **且** hypervisor 品牌 = TCG ——
/// KVM/WHPX 下 VBE 仍是 QEMU 设备 (Bochs 0xB0C5) 但 LAPIC 走真实 Intel 语义!
fn qemu_tcg_icr() -> bool {
    crate::platform::is_qemu() && crate::hvm::hv_accel_id() == 0
}

pub fn icr_mode() -> u8 {
    if qemu_tcg_icr() {
        0
    } else {
        1
    }
}

fn icr_send(value: u32, dest: u32) {
    if qemu_tcg_icr() {
        // QEMU LAPIC: 高 32 先存 (dest field), 低 32 后写触发
        mmio_wr32(lapic(0x310), dest << 24);
        mmio_wr32(lapic(0x300), value);
    } else {
        // Intel SDM (真机/KVM/WHPX): 低 32 先存 (值), 高 32 后写触发
        mmio_wr32(lapic(0x300), value);
        mmio_wr32(lapic(0x310), dest << 24);
    }
    icr_idle_wait();
}

/// W17b: 等 PIT tick (ap_bringup 期间 PIT 已活; hlt 节省 TCG 时钟)。
fn wait_ticks(n: u64) {
    let t0 = crate::interrupts::ticks();
    while crate::interrupts::ticks().wrapping_sub(t0) < n {
        crate::hlt();
    }
}

/// W17/W20: 映射 LAPIC MMIO (MSR 0x1B 探测基址) 进恒等页表。
/// boot 恒等只到 1GiB; 0xFEE00000 = PDPT[3] . PD[503] → PT[0];
/// 页表索引按探测基址通用计算 (真机可能重定位)。
fn map_lapic() -> bool {
    let base = lapic_base();
    unsafe {
        LAPIC_BASE = base;
        let cr3 = crate::mem::cr3_phys();
        let pm4 = cr3 as *mut u64;
        let pdpt_raw = pm4.read();
        if pdpt_raw & 1 == 0 {
            return false;
        }
        let pdpt = (pdpt_raw & 0x000F_FFFF_FFFF_F000) as *mut u64;
        // 0xFD000000 (LFB) 与 LAPIC 同属 PDPT[3] (pdi 488/503);
        // 不复用整链, 只复用旧 PD, 在 LAPIC PD 索引处插 PT (LFB 链不动)。
        let pdpti = ((base >> 30) & 0x3) as usize;
        let pdi = ((base >> 21) & 0x1FF) as usize;
        let pti = ((base >> 12) & 0x1FF) as usize;
        let old_pd_raw = pdpt.add(pdpti).read();
        let old_pd = old_pd_raw & 0x000F_FFFF_FFFF_F000;
        let pd: u64;
        if old_pd_raw & 1 != 0 && old_pd != 0 {
            pd = old_pd;
        } else {
            pd = match crate::mem::alloc_frames_kernel(2) {
                Some(p) => p,
                None => return false,
            };
            pdpt.add(pdpti).write(pd | 0x3);
        }
        let pdt = pd as *mut u64;
        let la = match crate::mem::alloc_frame_kernel() {
            Some(p) => p,
            None => return false,
        };
        // PT 帧清零后填 1 项
        for k in 0..512 {
            ((la as *mut u64).add(k)).write(0);
        }
        ((la as *mut u64)).write(base | 0x0B); // RW|P|PWT|PCD
        pdt.add(pdi).write(la | 0x3);
        serial::write_str("smp  : lapic base=0x");
        crate::syscall::log_hex(base);
        serial::write_str(" pd=0x");
        crate::syscall::log_hex(pd);
        serial::write_str(" pt=0x");
        crate::syscall::log_hex(la);
        serial::write_line("");
        serial::write_str("smp  : lapic MMIO mapped (");
        serial::write_str("pml4[0] pdpt[");
        serial::write_str(if pdpti == 3 { "3" } else { "?" });
        serial::write_line("])");
    }
    let id = mmio_rd32(lapic(0x20));
    serial::write_str("smp  : lapic id=0x");
    crate::syscall::log_hex(id as u64);
    serial::write_str(if (id >> 24) == 0 { " (BSP)" } else { " (AP?)" });
    serial::write_line("");
    // W31: LAPIC 规范使能 (IA32_APIC_BASE.EN, MSR 0x1B) —— KVM 真 LAPIC 必须;
    // TCG QEMU 设备模型宽容 (无此写也工作), 加此写两平台皆规范。
    unsafe {
        core::arch::asm!(
            "mov ecx, 0x1B",
            "mov eax, 0xFEE00800",
            "xor edx, edx",
            "wrmsr",
            options(nomem, nostack, preserves_flags),
        );
    }
    let id2 = mmio_rd32(lapic(0x20));
    serial::write_str("smp  : after APIC-base.EN id=0x");
    crate::syscall::log_hex(id2 as u64);
    serial::write_line("");
    // LAPIC SVR: APIC software enable (bit 8) — 复位后 ICR 不执行直到启用
    // W17b 取证: 写 0x1F5 回读回显 (铁律 17: 区分 RW/RO; 若读回 0 则 MMIO 路径可疑)
    mmio_wr32(lapic(0x3F0), 0x1F5);
    serial::write_str("smp  : svr(w0x1f5)=0x");
    crate::syscall::log_hex(mmio_rd32(lapic(0x3F0)) as u64);
    serial::write_line("");
    mmio_wr32(lapic(0x3F0), 0x1FF);
    true
}

// ---------------------------------------------------------------------------
// W17: AP 入口 (trampoline retf 到 0x08:fujo_ap_entry; AP GDT = trampoline GDT)
// ---------------------------------------------------------------------------
core::arch::global_asm!(r#"
    .text
    .global fujo_ap_entry
    .p2align 4
fujo_ap_entry:
    cli
    mov ax, 0x10
    mov ds, ax
    mov ss, ax
    mov rsp, {core1_top}
    call fujo_ap_main
.aphlt:
    hlt
    jmp .aphlt
"#, core1_top = const crate::mem::STK_CORE1_TOP);

extern "C" {
    fn fujo_ap_entry();
}

/// W17: AP 启动序列 (仅 ncpu>=2 时; 拷贝 trampoline -> 回填 -> SIPI@0x8000)。
pub fn ap_bringup() {
    if ncpu() < 2 {
        return;
    }
    unsafe {
        if !map_lapic() {
            return;
        }
        let tramp: &[u8] = include_bytes!("../../sdk/linux/tramp.bin");
        let base = 0x8000u64 as *mut u8;
        for k in 0..tramp.len() {
            base.add(k).write(tramp[k]);
        }
        // 回填数据槽 (+0x200 cr3, +0x204 entry)
        let cr3 = crate::mem::cr3_phys() as u32;
        (base.add(0x200) as *mut u32).write(cr3);
        let entry = fujo_ap_entry as *const () as u64 as u32;
        (base.add(0x204) as *mut u32).write(entry);
        serial::write_line("smp  : INIT+SIPI -> AP (linux-smpboot profile, dest=APIC1)");
        // W17b: Linux smpboot.c 时序 —— INIT assert(0x10500,bit16=电平) -> deassert(0x500)
        //        -> 等 ~20ms (AP 完成 INIT 复位) -> SIPI×2 (vector 0x8 => 0x8000)。
        // 投递: icr_send 高 32 先存 dest, 低 32 写触发 (QEMU LAPIC 实测语义)。
        icr_send(0x0001_0500, 1); // INIT assert, physical, dest=APIC ID 1
        icr_send(0x0000_0500, 1); // INIT deassert (电平语义: 必须成对)
        wait_ticks(2); // 20ms: AP 完成复位
        icr_send(0x0000_0608, 1); // SIPI, vector 0x8 (=> 0x8000)
        wait_ticks(1); // 10ms: 二次 SIPI 冗余 (Linux 200us, TCG 放宽)
        icr_send(0x0000_0608, 1); // SIPI again
        // 探测: AP 是否执行到 trampoline 末尾 (执行标记 0x8230 / 完成标记 0x8220)
        wait_ticks(20); // 200ms: 等 AP 走完 trampoline + ap_entry
        let ex = (0x8230u64 as *const u32).read_volatile();
        let done = (0x8220u64 as *const u32).read_volatile();
        serial::write_str("smp  : ap exec_marker=0x");
        crate::syscall::log_hex(ex as u64);
        serial::write_str(" done_marker=0x");
        crate::syscall::log_hex(done as u64);
        serial::write_str(" ap_online=");
        serial::write_str(if AP_ONLINE { "1" } else { "0" });
        serial::write_line("");
    }
}

/// AP 主入口 (trampoline lretw 到达; 装载内核 GDT/TSS1/IDT 后 sti+hlt)。
#[no_mangle]
pub extern "C" fn fujo_ap_main() {
    crate::gdt::ap_load();
    crate::interrupts::ap_load_idt();
    unsafe {
        AP_ONLINE = true;
    }
    serial::write_line("smp  : AP1 online (id=1, kernel gdt/tss1/idt, sti+hlt)");
    unsafe {
        core::arch::asm!("sti", options(nomem, nostack, preserves_flags));
    }
    loop {
        crate::hlt();
    }
}

/// W17: 0x8B03 — smp_state(ptr): u64×3 = (ncpu, ap_online, lapic_id)。
#[no_mangle]
pub extern "C" fn fujo_smp_state(ptr: u64) -> i64 {
    unsafe {
        if !(0x400000..0xC00000).contains(&ptr) {
            return -14;
        }
        let w = ptr as *mut u64;
        w.write(ncpu() as u64);
        w.add(1).write(if AP_ONLINE { 1 } else { 0 });
        w.add(2).write(lapic_id() as u64);
    }
    0
}
