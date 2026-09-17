//! syscall.rs — Linux ABI syscall gate (M1 内核化)
//!
//! 路径: 用户 syscall -> LSTAR(0xC0000082) -> fujo_syscall_entry(asm)
//!       -> 栈切换(内核栈 STK_MAIN_TOP, 见 mem.rs 栈区) -> fujo_syscall_dispatch(C)
//!       -> sysretq (STAR 数学: CS=0x20 SS=0x18 与 GDT 一致)
//!
//! 本版本直接实现 linux x86_64 编号: write(1) → 串口/VGA, exit(60)/exit_group(231)。
//! 这就是 "Linux ABI 第一公民" 的最短路径: ELF 里的 syscall 无需任何用户态垫片。

use core::arch::asm;

use crate::interrupts;
use crate::serial;
use crate::vga;

// ---------- 占位表数据（完整表由 tools 生成, 见 fujo-compat::abi） ----------

pub const LINUX_X64_SUBSET: &[(u16, &str)] = &[
    (0, "read"), (1, "write"), (2, "open"), (3, "close"), (4, "stat"), (5, "fstat"),
    (6, "lstat"), (7, "poll"), (8, "lseek"), (9, "mmap"), (10, "mprotect"), (11, "munmap"),
    (12, "brk"), (16, "ioctl"), (17, "pread64"), (19, "readv"), (20, "writev"), (21, "access"),
    (22, "pipe"), (23, "select"), (24, "sched_yield"), (35, "nanosleep"), (41, "socket"),
    (42, "connect"), (43, "accept"), (57, "fork"), (59, "execve"), (60, "exit"), (61, "wait4"),
    (63, "uname"), (72, "fcntl"), (78, "gettimeofday"), (79, "getcwd"), (157, "prctl"),
    (158, "arch_prctl"), (217, "getdents64"), (231, "exit_group"), (257, "openat"), (317, "getrandom"),
    (318, "memfd_create"),
];

pub const DARWIN_X64_SUBSET: &[(u64, &str)] = &[
    (0x200_0001, "exit"), (0x200_0003, "read"), (0x200_0004, "write"), (0x200_0005, "open"),
    (0x200_0006, "close"), (0x200_0013, "lseek"), (0x200_0014, "getpid"), (0x200_00C5, "mmap"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Abi {
    LinuxX64,
    DarwinX64,
}

pub fn linux_x64_count() -> usize {
    LINUX_X64_SUBSET.len()
}

pub fn darwin_x64_count() -> usize {
    DARWIN_X64_SUBSET.len()
}

// ---------- 状态 ----------

#[no_mangle]
pub static mut user_rsp_tmp: u64 = 0;
/// W45: SetLastError/GetLastError 内核内存 (per-boot)
pub static mut SHIM_LAST_ERROR: u64 = 0;
pub static mut IS_BIG: bool = false; // W41: 当前装载为大程序 (单任务模式)
#[no_mangle]
pub static mut sys_kernel_rsp: u64 = crate::mem::STK_MAIN_TOP;
#[no_mangle]
pub static mut spam_count: u64 = 0;
/// M27: 用户态 GS 基址 (mingw CRT 的假 TEB; ELF 程序保持 0)。
#[no_mangle]
pub static mut user_gs_base: u64 = 0;
/// M27: PE 模块 argv0 (__getmainargs 回填, 由装载路径写入)。
#[no_mangle]
pub static mut pe_argv0: [u8; 64] = [0; 64];
/// M33: 系统调用追踪 (默认关; 开关经 0x5301)。
#[no_mangle]
pub static mut TRACE_ON: u64 = 0;
// W36/B-2: 计数 u64 -> u32 (BSS 硬约束回收 -1KB; 0x5303 仍返回 i64)
#[no_mangle]
pub static mut TRACE_COUNTS: [u32; 256] = [0; 256];
pub static mut TRACE_RING: [(u64, u64, u64); 64] = [(0, 0, 0); 64];
pub static mut TRACE_POS: usize = 0;
/// M76: 后台记录 (不经 trace_show 也持续写入 ring/counts)。
#[no_mangle]
pub static mut TRACE_BG: u64 = 0;
pub static mut TRACE_TOTAL: u64 = 0;
pub static mut TRACE_FILTER: u64 = 0; // 0=全记录, 否则仅该 nr
/// L1 诊断: 逐调用打印 (cmdline `fujo.strace=1`)。
pub static mut STRACE: u64 = 0;
pub static mut TRACE_DROPPED: u64 = 0;
/// M112: syscall 流采样计数 (每 1000 次一条 ev_syscall)。
static mut SYS_EV_COUNT: u64 = 0;
/// M11: 用户 FPU/SIMD 状态 (syscall 进出保存恢复; 16 对齐 —— fxsave 要求)。
#[repr(C, align(16))]
pub struct FpuSave {
    data: [u8; 512],
}
#[no_mangle]
pub static mut fpu_saved: FpuSave = FpuSave { data: [0; 512] };

extern "C" {
    fn fujo_syscall_entry();
    fn fujo_enter_user(entry: u64, rsp: u64);
}

core::arch::global_asm!(r#"
    .text
    # ---- syscall 入口 (LSTAR) ----
    # M10 修复 (根因): 入口只恢复 rcx/r11 会破坏用户的 rdi/rsi/rdx/r8/r9/r10 ——
    # C 分发是 caller-saved 契约, 用户编译器认为这些寄存器跨 syscall 存活
    # (clang 会把跨调用基址放 r9 等), 实际被内核吃光, 造成 M9 的 "intent=3 /
    # context[1883]" 漂移与 M10 的 cr2=-3 #PF (r9 残留 0 -> slot-3 地址)。
    # 因此: 保存全部通用寄存器并在返回前原样恢复; rcx/r11 例外处理 (sysretq 需用)。
    .p2align 4
    .global fujo_syscall_entry
fujo_syscall_entry:
    fxsave [rip + fpu_saved]
    mov [rip + user_rsp_tmp], rsp
    mov rsp, [rip + sys_kernel_rsp]
    push r11
    push rcx
    push r9
    push r8
    push r10
    push rdx
    push rsi
    push rdi
    mov rdi, rax
    mov rsi, rsp
    mov rdx, rcx
    call fujo_syscall_dispatch
    pop rdi
    pop rsi
    pop rdx
    pop r10
    pop r8
    pop r9
    pop rcx
    pop r11
    fxrstor [rip + fpu_saved]
    mov rsp, [rip + user_rsp_tmp]
    sysretq

    # ---- iretq 进入用户态 ----
    # rdi=entry, rsi=user_stack; 先 cli: 构造帧期间不允许中断 (M1 现场验证)
    # M23: 用户入口寄存器清零 (Linux _start 契约: rsp=argc 帧, 其他未定义但
    # glibc _start 用 rdx=rtld_fini; 清零保证 rtld_fini=NULL)。保留 rsp 语义。
    .p2align 4
    .global fujo_enter_user
fujo_enter_user:
    cli
    mov rax, cr3
    mov cr3, rax          # TLB flush (M2 原有; 幂等)
    mov r8, rdi
    mov r10, rsi
    # M27: 用户 GS 基址 (mingw TEB) — 先于 iretq 写 MSR_GS_BASE; ELF 保持 0
    mov rax, [rip + user_gs_base]
    mov ecx, 0xC0000101
    mov rdx, rax
    shr rdx, 32
    wrmsr
    xor rax, rax
    xor rbx, rbx
    xor rcx, rcx
    xor rdx, rdx
    xor rsi, rsi
    xor rdi, rdi
    xor r9, r9
    xor r11, r11
    xor r12, r12
    xor r13, r13
    xor r14, r14
    xor r15, r15
    mov r9, 60            # spare (未用)
    push 0x1b
    push r10
    push 0x202
    push 0x23
    push r8
    mov rax, r9
    iretq
"#);

unsafe fn wrmsr(msr: u32, val: u64) {
    asm!(
        "wrmsr",
        in("ecx") msr,
        in("eax") val as u32,
        in("edx") (val >> 32) as u32,
        options(nomem, nostack, preserves_flags)
    );
}

unsafe fn rdmsr(msr: u32) -> u64 {
    let hi: u32;
    let lo: u32;
    asm!(
        "rdmsr",
        in("ecx") msr,
        out("eax") lo,
        out("edx") hi,
        options(nomem, nostack, preserves_flags)
    );
    ((hi as u64) << 32) | lo as u64
}

const MSR_EFER: u32 = 0xC000_0080;
const MSR_STAR: u32 = 0xC000_0081;
const MSR_LSTAR: u32 = 0xC000_0082;
const MSR_SFMASK: u32 = 0xC000_0084;

/// 启用 syscall/sysret (EFER.SCE + STAR + LSTAR + SFMASK)
pub fn setup() {
    unsafe {
        let efer = rdmsr(MSR_EFER);
        wrmsr(MSR_EFER, efer | 0x1); // SCE

        // STAR:  kcs=0x08 @[47:32], user field=0x13 @[63:48]
        // sysret: CS=0x13+16=0x23 (RPL3!), SS=0x13+8=0x1B —— RPL 必须落进 STAR,
        // 否则 sysret 以 RPL0 返回 -> 用户实际跑在 CPL0 (M13 现场教训:
        // 无栈切换/中断帧紧凑/U-guard 失效, 三处异常同源)。
        let star = (0x08u64 << 32) | (0x13u64 << 48);
        wrmsr(MSR_STAR, star);

        let lst = fujo_syscall_entry as usize as u64;
        wrmsr(MSR_LSTAR, lst);

        wrmsr(MSR_SFMASK, 0x200); // syscall 时屏蔽 IF (简单: 内核期无中断)
    }
}

pub fn lstar() -> u64 {
    unsafe { rdmsr(MSR_LSTAR) }
}

// ---------- 分发 ----------

/// linux-x64 syscall 分发 (由 asm 以 C ABI 调用; rdx = 用户返回 RIP)
#[no_mangle]
pub extern "C" fn fujo_syscall_dispatch(nr: u64, args: *const u64, ret: u64) -> i64 {
    let a0 = unsafe { args.read() };
    let a1 = unsafe { args.add(1).read() };
    let a2 = unsafe { args.add(2).read() };
    let a3 = unsafe { args.add(3).read() };
    let a4 = unsafe { args.add(4).read() };
    let a5 = unsafe { args.add(5).read() };

    // L1 诊断: cmdline `fujo.strace=1` -> 逐个打印 Linux 系统调用号与首参
    if unsafe { STRACE } != 0 {
        serial::write_str("sys : nr=");
        print_dec(nr);
        serial::write_str(" a0=");
        print_hex(a0);
        serial::write_str(" a1=");
        print_hex(a1);
        serial::write_line("");
    }

    // ---- M33/M76: trace 登记 (前台开关或后台记录; 过滤面) ----
    unsafe {
        let rec = TRACE_ON != 0 || TRACE_BG != 0;
        if rec && (TRACE_FILTER == 0 || TRACE_FILTER == nr) {
            TRACE_COUNTS[(nr % 256) as usize] += 1;
            TRACE_RING[TRACE_POS % 64] = (nr, a0, crate::interrupts::ticks());
            TRACE_POS += 1;
            TRACE_TOTAL += 1;
            if TRACE_POS % 64 == 0 {
                TRACE_DROPPED = TRACE_TOTAL - 64; // 环形覆盖计数
            }
        }
    }

    // ---- M68: 性能计数器 syscall ----
    crate::perf::bump(1);

    // ---- M112: syscall 流采样 (每 1000 次一条 ev_syscall; 事件环不刷屏) ----
    unsafe {
        SYS_EV_COUNT += 1;
        if SYS_EV_COUNT % 1000 == 0 {
            crate::ctx::sys_note(crate::sched::current_task() as u64, SYS_EV_COUNT);
        }
    }

    let res = match nr {
        // fujo_trace_enable(on) — M33
        0x5301 => {
            unsafe { TRACE_ON = a0; }
            0
        }
        // fujo_trace_show() — 打印 ring 尾部 + 非零计数
        0x5302 => trace_show(),
        // fujo_trace_count(nr) -> 计数
        0x5303 => unsafe { TRACE_COUNTS[(a0 % 256) as usize] as i64 },
        // ---- M76: trace 工具化 (后台记录/统计/过滤) ----
        0x7701 => {
            unsafe { TRACE_BG = a0; }
            0
        }
        0x7702 => {
            unsafe {
                // (total, nonzero, ring_pos, dropped)
                let mut nonzero = 0u64;
                for &c in TRACE_COUNTS.iter() {
                    if c > 0 {
                        nonzero += 1;
                    }
                }
                let w = a0 as *mut u64;
                w.write(TRACE_TOTAL);
                w.add(1).write(nonzero);
                w.add(2).write(TRACE_POS as u64);
                w.add(3).write(TRACE_DROPPED);
            }
            0
        }
        0x7703 => {
            unsafe { TRACE_FILTER = a0; }
            0
        }
        // ---- M77: 性能计数器窗口 ----
        0x7801 => crate::perf::fujo_win_begin(a0),
        0x7802 => crate::perf::fujo_win_end(a0),
        0x7803 => crate::perf::fujo_win_read(a0),
        // ---- M82: 单元测试框架 ----
        0x7901 => crate::utest::fujo_ut_run(),
        0x7902 => crate::utest::fujo_ut_info(a0),
        // ---- M83: 泄漏检测 ----
        0x7A01 => crate::leak::fujo_leak_begin(),
        0x7A02 => crate::leak::fujo_leak_end(a0),
        0x7A03 => crate::leak::fujo_leak_stats(a0),
        // ---- M84: 崩溃转储 ----
        0x7B01 => crate::dump::fujo_dump_arm(a0),
        0x7B02 => crate::dump::fujo_dump_read(a0, a1),
        0x7B03 => crate::dump::fujo_dump_info(a0),
        // ---- M86: 权重 mmap 对象 ----
        0x7C01 => crate::mem::fujo_wmap_load(a0, a1),
        0x7C02 => crate::mem::fujo_wmap_res(a0, a1),
        0x7C03 => crate::mem::fujo_wmap_stats(a0),
        // ---- M87: 模型卡 ----
        0x7D01 => crate::modelcard::fujo_mc_register(a0),
        0x7D02 => crate::modelcard::fujo_mc_call(a0, a1, a2),
        0x7D03 => crate::modelcard::fujo_mc_info(a0),
        0x7D04 => crate::modelcard::fujo_mc_audit(a0, a1),
        // ---- M88: agent 会话 ----
        0x7E01 => crate::sessions::fujo_sess_create(a0),
        0x7E02 => crate::sessions::fujo_sess_save(a0, a1, a2),
        0x7E03 => crate::sessions::fujo_sess_load(a0, a1),
        0x7E04 => crate::sessions::fujo_sess_info(a0),
        0x7E05 => crate::sessions::fujo_sess_tick(a0, a1),
        // ---- M89: fujoctx 摘要注入 ----
        0x7F01 => crate::ctx::fujo_ctx_snap(a0, a1),
        // ---- M90: 上下文压缩 ----
        0x8001 => crate::ctx::fujo_ctx_compress(a0, a1, a2, a3, a4),
        // ---- M112: AI 感知 (fujoctx v2 结构态 + 事件环) ----
        0x8002 => crate::ctx::fujo_ctx_subscribe(a0),
        0x8003 => crate::ctx::fujo_ctx_events(a0, a1),
        0x8004 => crate::ctx::fujo_ctx_inject(a0, a1, a2),
        0x8005 => crate::ctx::fujo_ctx_struct(a0, a1),
        // ---- M91: 权限与审计 ----
        0x8101 => crate::capability::fujo_cap_grant(a0, a1),
        0x8102 => crate::capability::fujo_cap_check(a0, a1),
        0x8103 => crate::capability::fujo_aud_log(a0, a1),
        0x8104 => crate::capability::fujo_aud_read(a0, a1),
        // ---- M112: AI 动作 (cap_exec 经 syscall 现场; LAUNCH 需寄存器) ----
        0x8105 => cap_exec(args),
        0x8106 => crate::capability::fujo_cfg_get(a0),
        // ---- M116 (W9): 权限域 (cap 集合 / 地址空间 / 中断域, 可撤销) ----
        0x8107 => crate::capability::fujo_dom_create(a0, a1, a2),
        0x8108 => {
            crate::sched::set_current_domain(a0.min(4));
            0
        }
        0x8109 => crate::capability::fujo_dom_revoke(a0),
        0x810A => crate::capability::fujo_dom_info(a0),
        // ---- M92: 意图路由增强 ----
        0x8201 => crate::ai::fujo_route_set(a0),
        0x8202 => crate::ai::fujo_route_classify(a0, a1),
        0x8203 => crate::ai::fujo_route_table(a0),
        // ---- M93: 推理执行器插槽 ----
        0x8301 => crate::infer::fujo_infer_run(a0, a1, a2, a3),
        0x8302 => crate::infer::fujo_infer_slot(a0),
        0x8303 => crate::infer::fujo_infer_set(a0),
        // ---- M112: 异常哨兵 (A 首刀) ----
        0x8304 => crate::ai::fujo_anom_run(a0, a1, a2, a3),
        // ---- M113: 计划-执行器 + I/O 预测器 ----
        0x8305 => crate::ai::fujo_plan_run(a0, a1, a2, a3),
        0x8306 => crate::ai::fujo_io_predict(a0, a1, a2, a3),
        // ---- M114: 自然语言配置 + 环境侦察 ----
        0x8307 => crate::ai::fujo_nlc_set(a0, a1, a2, a3),
        0x8308 => crate::ai::fujo_env_scan(a0, a1),
        // ---- M118 (W8): R3 时延一致性探针 + R1 公理化自检 ----
        0x8309 => crate::ai::fujo_r3_probe(a0, a1, a2, a3),
        0x830A => crate::ai::fujo_inv_run(a0, a1),
        // ---- W10 (R5/R6): 蒸馏字节码规则 + AI 统计/审计导出 ----
        0x830B => crate::ai::fujo_rules_load(a0, a1),
        0x830C => crate::ai::fujo_ai_stats(a0),
        0x830D => crate::ai::fujo_ai_audit(a0, a1),
        // ---- W11: 当前 CR3 探针 (调试) ----
        0x830E => crate::mem::fujo_cr3_probe(a0),
        // ---- W22: 评测引擎强制 (三引擎对照; 0=auto 1=model 2=rules) ----
        0x830F => crate::ai::fujo_evl_mode(a0),
        // ---- W27: 事件流摘要 (哨兵感知系统自身; 最近 100tick 速率+最近事件) ----
        0x8312 => crate::ai::fujo_ev_digest(a0, a1),
        // ---- W32: 信任自适应域 (质量台账 -> 域宽=f(质量); zcode 框架) ----
        // W46/a6: 仅测试模式 (fujo.qual_test=1) 可用 —— 用户态可伪造信号提权
        // (m165 LIVE; docs/133)。
        0x8313 => { if !crate::ai::qual_test_enabled() { return -12; } crate::ai::fujo_dom_admit(a0, a1) }
        0x8314 => { if !crate::ai::qual_test_enabled() { return -12; } crate::ai::fujo_qual_feed(a0, a1) }
        0x8315 => crate::ai::fujo_qual_seq(a0, a1),
        // ---- W36/B-2: BOX-BRIDGE v0 (盒供应商: 命令进/产物检疫/状态) ----
        0x8316 => crate::boxbridge::fujo_box_run(a0, a1, a2),
        0x8317 => crate::boxbridge::fujo_box_stat(a0, a1),
        0x8318 => crate::boxbridge::fujo_box_result(a0, a1),
        0x8319 => crate::boxbridge::fujo_box_fb_info(a0),
        0x8320 => crate::boxbridge::fujo_box_contract(a0, a1, a2, a3),
        // ---- W13: virtio-blk (PCI 总线模型 + 驱动) ----
        0x8A01 => crate::virtio::fujo_vblk_read(a0, a1, a2),
        0x8A02 => crate::virtio::fujo_vblk_info(a0),
        0x8A03 => crate::virtio::fujo_vblk_write(a0, a1, a2),
        0x8A04 => crate::net::fujo_net_info(a0),
        0x8A05 => crate::net::fujo_net_tx(a0, a1),
        0x8A06 => crate::net::fujo_net_rx(a0, a1),
        // ---- W15: 应用管理器 (ABI 冻结面) ----
        0x8B01 => crate::vfs::fujo_app_list(a0),
        // ---- W16: 内存执行 (编译产物直入; 用户缓冲 -> 内核暂存 -> ELF 装载 -> iretq) ----
        0x8B02 => fujo_exec_mem(a0, a1),
        // ---- W17: SMP 状态 (ncpu/ap_online/lapic_id) ----
        0x8B03 => crate::smp::fujo_smp_state(a0),
        // ---- W19: 统一审计 (cap + AI 双环同构导出) ----
        0x8C01 => crate::capability::fujo_unified_aud(a0, a1),
        // ---- M94: 模型注册表 + fupm ----
        0x8401 => crate::modelreg::fujo_fupm_install(a0, a1, a2),
        0x8402 => crate::modelreg::fujo_reg_list(a0),
        0x8403 => crate::modelreg::fujo_reg_active(a0),
        0x8404 => crate::modelreg::fujo_fupm_remove(a0),
        // ---- M96: ACPI/PCI 真机最小集 ----
        0x8501 => crate::acpi::fujo_acpi_info(a0),
        0x8502 => crate::acpi::fujo_acpi_dump(a0, a1),
        0x8503 => crate::acpi::fujo_pci_scan(a0),
        // ---- M97: 真机 hw 适配面 ----
        0x8601 => crate::hw::fujo_hw_disp(a0),
        0x8602 => crate::hw::fujo_hw_storage(a0),
        // ---- M98: live 镜像 + 安装器 ----
        0x8701 => crate::installer::fujo_inst_install(),
        0x8702 => crate::installer::fujo_inst_status(a0),
        // ---- M99: 签名/更新 ----
        0x8801 => crate::upd::fujo_upd_check(a0, a1),
        0x8802 => crate::upd::fujo_upd_apply(a0, a1, a2),
        0x8803 => crate::upd::fujo_upd_status(a0),
        // ---- W20: 平台信息 (平台检测原语面) ----
        0x8D01 => crate::platform::fujo_platform_info(a0),
        // ---- W20 p6: 内存拓扑 ----
        0x8F02 => crate::mem::fujo_mem_topology(a0),
        // ---- W42: demand 页映射 (源软页; 缺页载入) ----
        0x8F03 => crate::mem::fujo_demand_map(a0, a1, a2),
        // ---- W43: demand 回收 (swap v0) ----
        0x8F04 => crate::mem::fujo_demand_reclaim(),
        // ---- W20: AHCI (SATA) 驱动面 ----
        0x8E01 => crate::ahci::fujo_ahci_read(a0, a1),
        0x8E02 => crate::ahci::fujo_ahci_write(a0, a1),
        0x8E03 => crate::ahci::fujo_ahci_info(a0),
        // read(fd, buf, len) — M15 VFS
        0 => crate::vfs::fujo_read(a0, a1, a2),
        // write(fd, buf, len)
        1 => user_write(a0, a1, a2),
        // open(path, flags, mode) — M15 VFS
        2 => crate::vfs::fujo_open(a0, a1, a2),
        // close(fd) — M15 VFS
        3 => crate::vfs::fujo_close(a0),        // ---- M11: 内存原语 (linux ABI 直通) ----
        // lseek(fd, off, whence) — M29 已有实现; W16 补分发 (tcc 静态 stdio)
        8 => crate::vfs::fujo_lseek(a0, a1 as i64, a2),
        // mmap(addr, len, prot, flags, fd, off) — 匿名私有子集
        9 => crate::mem::fujo_mmap(a0, a1, a2, a3, a4, a5),
        // munmap(addr, len) — v0 no-op
        11 => crate::mem::fujo_munmap(a0, a1),
        // brk(ptr) — 堆尾, 恒等 heap 区 bump
        12 => crate::mem::fujo_brk(a0),
        // getpid() (x86-64: 39) — linuxsubsys v0 最小实现
        39 => 1,
        // fork() — M22: 克隆当前任务 (v0 共享地址空间 + 用户栈物理拷贝)
        57 => fork_self(args),
        // execve(path, argv, envp) — M22 v0: 未实现 (M23 直通扩展)
        59 => -38, // -ENOSYS
        // ---------------------------------------------------------------
        // M21: linuxsubsys syscall 面扩展 (~20 个常用)
        // 原则: 行为合理的哨兵返回 + 必要回填 (用户缓冲地址检查同 VFS)。
        // ---------------------------------------------------------------
        // stat(path, buf) — 简化: mode=REG|0644, size=len(path)
        4 => sys_stat(a0, a1),
        // fstat(fd, buf)
        5 => sys_fstat(a0, a1),
        // lstat(path, buf) — 同 stat (无符号链接)
        6 => sys_stat(a0, a1),
        // writev(fd, iovec, count) — 逐个 iovec 写串口
        20 => sys_writev(a0, a1, a2),
        // access(path, mode) -> 0 (允许)
        21 => 0,
        // pipe(fds[2]) — linux ABI 22 号 (M18 内核实现)
        22 => crate::ipc::fujo_pipe(a0),
        // nanosleep(req, rem) — PIT 忙等 (100Hz 粒度)
        35 => sys_nanosleep(a0),
        // uname(buf) — 回填 c_* 字段 (FujoOS)
        63 => sys_uname(a0),
        // gettimeofday(tv, tz) — 单调钟 (PIT ticks 派生)
        78 => sys_gettimeofday(a0, a1),
        // getuid/getgid/geteuid/getegid -> 1000
        102 => 1000,
        104 => 1000,
        107 => 1000,
        108 => 1000,
        // arch_prctl(arch, addr) — ARCH_SET_FS=0x1002 写 FS_BASE (glibc TLS);
        // ARCH_GET_FS=0x1003 读回。M23: busybox glibc %fs 寻址必需。
        // v0: 写 MSR_FS_BASE; 多任务切换保存/恢复由 sched::save/restore 处理。
        158 => {
            match a0 {
                0x1002 => {
                    unsafe {
                        core::arch::asm!(
                            "wrmsr",
                            in("ecx") 0xC000_0100u32,
                            in("eax") a1 as u32,
                            in("edx") (a1 >> 32) as u32,
                            options(nomem, nostack, preserves_flags)
                        );
                    }
                    0
                }
                0x1003 => {
                    if user_ok(a1, 8) {
                        let lo: u32;
                        let hi: u32;
                        unsafe {
                            core::arch::asm!(
                                "rdmsr",
                                in("ecx") 0xC000_0100u32,
                                out("eax") lo,
                                out("edx") hi,
                                options(nomem, nostack, preserves_flags)
                            );
                            (a1 as *mut u64).write((lo as u64) | ((hi as u64) << 32));
                        }
                    }
                    0
                }
                _ => 0,
            }
        }
        // prctl(option, ...) -> 0 (no-op)
        157 => 0,
        // mprotect(addr, len, prot) -> 0 (直通; busybox 除 exec 区外全 RWX)
        10 => 0,
        // set_tid_address(ptr) -> tid
        218 => crate::sched::current_task() as i64 + 1,
        // set_robust_list(ptr, len) -> 0
        273 => 0,
        // rseq(ptr, len, flags, sig) -> -ENOSYS (glibc 可回退)
        334 => -38,
        // get_robust_list -> 0
        274 => 0,
        // gettid -> 当前任务 id+1
        186 => crate::sched::current_task() as i64 + 1,
        // time(ptr) -> 单调秒
        201 => sys_time(a0),
        // futex(op, uaddr, val) -> 0 (no-op)
        202 => 0,
        // openat(dirfd, path, flags, mode) — 转发 open (忽略 dirfd=AT_FDCWD)
        257 => crate::vfs::fujo_open(a1, a2, a3),
        // getdents64(fd, buf, len) — W18: 目录枚举 (busybox ls 目录命令)
        217 => crate::vfs::fujo_getdents(a0, a1, a2),
        // getrandom(buf, len, flags) — PIT 混哈希假熵
        317 => sys_getrandom(a0, a1),
        // W16: 静态 glibc 运行面 (tcc 0.9.27):
        // getcwd(buf, size) — 返回 "/" (进程无目录概念)
        79 => {
            if a0 != 0 {
                unsafe {
                    (a0 as *mut u8).write(b'/');
                    (a0 as *mut u8).add(1).write(0);
                }
            }
            a0 as i64
        }
        // fcntl(fd, cmd, ...) — F_GETFL(3) 返回 O_WRONLY|O_LARGEFILE (0x8001;
        // tcc fopen 输出流据此判定可写; 0=O_RDONLY 会让 FILE* 只读)
        72 => {
            if a1 == 3 {
                0x8001
            } else {
                0
            }
        },
        // unix (87, legacy Linux nsfs 遗留) — 返回 0 容忍
        87 => 0,
        // prlimit64(resource, new, old) -> 写 old 为 RLIM_INFINITY
        302 => {
            if a2 != 0 {
                unsafe {
                    let o = a2 as *mut u64;
                    o.write_volatile(u64::MAX);
                    o.add(1).write_volatile(u64::MAX);
                }
            }
            0
        }
        // memfd_create(name, flags) -> -ENOSYS (tcc 未强制)
        318 => -38,
        // clock_nanosleep -> 立即完成
        228 => 0,
        // ---- fujo 原生 Win32 shim 通道 (M3/M26 基础 + M27 mingw CRT + M28/M30) ----
        0x5001..=0x5018 | 0x5201..=0x522B | 0x5019..=0x502E | 0x5030..=0x5035 => shim_dispatch(nr, args),
        // exit(code) / exit_group(code) -> 返回 shell (W16: 编译链三步可串行; 原 M6 halt)
        60 | 231 => {
            serial::write_str("user : sys_exit code=");
            print_dec(a0);
            serial::write_line(" - return to shell (M6/W16)");
            crate::sched::set_single();
            exit_to_shell();
        }
        // ---- M9/M10: fujonn 模型调用原语 (fujoos-ai-dev) ----
        // fujo_ai_classify(ptr, len) -> intent (engine=qwen COM2 链路 / 规则降级)
        0x5101 => crate::ai::fujo_ai_classify(a0, a1),
        // fujo_ai_fetch(ptr, len) -> n (fujoctx 上下文注入)
        0x5102 => crate::ai::fujo_ai_fetch(a0, a1),
        // fujo_read_kbd() -> char | 0 (M10 · Hermes CLI 交互输入)
        0x5103 => crate::kbd::try_poll().map(|c| c as i64).unwrap_or(0),
        // fujo_ai_info(ptr, len) -> n (引擎/模型/链路信息)
        0x5104 => crate::ai::fujo_ai_info(a0, a1),
        // fujo_get_task_id() -> tid (M14: 进程/任务标识原语)
        0x5105 => crate::sched::current_task() as i64,
        // ---- M18: IPC 原语 (管道/共享内存/信号) ----
        // fujo_pipe(ptr) -> 0 (ptr 处写 [rfd, wfd])
        0x5110 => crate::ipc::fujo_pipe(a0),
        // fujo_shm() -> 共享窗口基址 0xA00000
        0x5111 => crate::ipc::fujo_shm(),
        // fujo_sigset(handler) -> 0
        0x5120 => crate::ipc::fujo_sigset(a0),
        // fujo_sigkill(tid, sig) -> 0
        0x5121 => crate::ipc::fujo_sigkill(a0, a1),
        // fujo_sigret() -> 0
        0x5122 => crate::ipc::fujo_sigret(),
        // ---- M61: blit/缩放 ----
        0x6801 => crate::blit::fujo_blit(a0, a1, a2, a3, a4),
        0x6802 => crate::blit::fujo_blit_scal(a0, a1, a2, a3),
        // ---- M62: 着色器内核 (compute 子集 v0) ----
        0x6901 => crate::shader::fujo_shader_load(a0, a1),
        0x6902 => crate::shader::fujo_shader_run(a0, a1, a2, a3),
        0x6903 => crate::shader::fujo_shader_pixel(a0, a1),
        0x6904 => crate::shader::fujo_shader_ops(),
        // ---- M64: 多核 v0 (亲和/负载均衡统计) ----
        0x6A01 => crate::smp::aff_set(a0, a1),
        0x6A02 => crate::smp::aff_get(a0),
        0x6A04 => crate::smp::fujo_smp_stats(a0),
        // ---- M65: 每核 TSS / 中断注入优化 ----
        0x6B01 => crate::smp::fujo_core_id(),
        0x6B02 => crate::smp::fujo_tss_info(a0),
        0x6B04 => crate::smp::fujo_irq_route(a0),
        0x6B05 => crate::smp::fujo_irq_stats(a0),
        // ---- M66: 页缓存/预读 ----
        0x6C01 => crate::pcache::fujo_pc_alloc(a0),
        0x6C02 => crate::pcache::fujo_pc_write(a0, a1),
        0x6C03 => crate::pcache::fujo_pc_read(a0, a1),
        0x6C04 => crate::pcache::fujo_pc_prefetch(a0, a1),
        0x6C05 => crate::pcache::fujo_pc_flush(),
        0x6C06 => crate::pcache::fujo_pc_evict(),
        0x6C07 => crate::pcache::fujo_pc_info(a0),
        // ---- M67: 中断合并/减轻 ----
        0x6D01 => crate::irq::fujo_irq_set_window(a0),
        0x6D02 => crate::irq::fujo_irq_cost_stats(a0),
        // ---- M68: 帧时间表/性能计数器 ----
        0x6E01 => crate::perf::fujo_perf_frame_mark(),
        0x6E02 => crate::perf::fujo_perf_frame_stats(a0),
        0x6E03 => crate::perf::fujo_perf_counter_enable(a0, a1),
        0x6E04 => crate::perf::fujo_perf_counter_read(a0),
        // ---- M69: 2D 游戏#2 + 输入延迟基准 ----
        0x6F01 => crate::game2::fujo_game2_latency(a0),
        0x6F02 => crate::game2::fujo_game2_stats(a0),
        0x6F03 => crate::game2::fujo_game2_hits(a0),
        // ---- M71: 系统内汇编器 ----
        0x7001 => crate::asm::fujo_asm_assemble(a0, a1, a2, a3),
        0x7002 => crate::asm::fujo_asm_verify(a0, a1),
        // ---- M72: 系统内链接器 ----
        0x7101 => crate::ld::fujo_ld_link(a0),
        0x7102 => crate::ld::fujo_ld_info(),
        // ---- M73: 迷你编辑器 ----
        0x7401 => crate::editor::fujo_ed_init(),
        0x7402 => crate::editor::fujo_ed_text(a0, a1),
        0x7403 => crate::editor::fujo_ed_key(a0),
        0x7404 => crate::editor::fujo_ed_dump(a0, a1),
        0x7405 => crate::editor::fujo_ed_info(a0),
        // ---- M74: fujocc 编译壳 ----
        0x7501 => crate::fujocc::fujo_cc_compile(a0, a1, a2, a3, a4),
        0x7502 => crate::fujocc::fujo_cc_version(),
        // ---- M75: 调试器 v0 ----
        0x7601 => crate::dbg::fujo_dbg_step(a0),
        0x7602 => crate::dbg::fujo_dbg_bp0(a0),
        0x7603 => crate::dbg::fujo_dbg_info(a0),
        0x7604 => crate::dbg::fujo_dbg_clear(),
        // ---- M60: 存档沙箱 ----
        0x6701 => crate::save::fujo_save_write(a0, a1, a2),
        0x6702 => crate::save::fujo_save_read(a0, a1, a2),
        0x6703 => crate::save::fujo_save_list(a0),
        0x6704 => crate::save::fujo_save_version(a0),
        // ---- M59: 游戏模式 ----
        0x6601 => crate::gamemode::fujo_game_mode(a0),
        0x6602 => crate::gamemode::fujo_game_status(a0),
        0x6603 => crate::gamemode::fujo_game_fullscreen(a0),
        // ---- M57: 加速探测 ---
        0x6401 => crate::hvm::fujo_accel_info(a0),
        // ---- M56: DXVK 式翻译原型 ----
        0x6301 => crate::dxwrap::fujo_dx_verts(a0, a1),
        0x6302 => crate::dxwrap::fujo_dx_matrix(a0),
        0x6303 => crate::dxwrap::fujo_dx_flush(a0),
        // ---- M55: fujogl (软件光栅) ----
        0x6201 => crate::gl::fujo_gl_clear(a0, a1, a2),
        0x6202 => crate::gl::fujo_gl_rect(a0, a1, a2, a3, a4),
        0x6203 => crate::gl::fujo_gl_tri(a0, a1),
        0x6204 => crate::gl::fujo_gl_line(a0, a1, a2, a3, a4),
        0x6205 => crate::gl::fujo_gl_pixel(a0, a1),
        // ---- M54: 高精度定时器
        0x6100 => crate::timer::fujo_timer_arm(),
        0x6101 => crate::timer::fujo_timer_us(),
        0x6102 => crate::timer::fujo_timer_ms(),
        0x6103 => crate::timer::fujo_timer_sleep_us(a0),
        0x6104 => crate::timer::fujo_timer_frame_wait(a0),
        0x6105 => crate::timer::fujo_timer_info(a0),
        // ---- M53: XInput 输入抽象 ----
        0x6001 => crate::xinput::fujo_xin_get(a0),
        0x6002 => crate::xinput::fujo_xin_reset(),
        0x6003 => crate::xinput::fujo_xin_press(a0),
        // ---- M52: 音频 (AC97) ----
        0x5F01 => crate::audio::fujo_audio_info(a0),
        0x5F02 => crate::audio::fujo_audio_enable(a0),
        0x5F03 => crate::audio::fujo_audio_volume(a0),
        0x5F04 => crate::audio::fujo_audio_playback(a0, a1),
        // ---- M63: 混音器/效果链 ----
        0x5F05 => crate::audio::fujo_mix_open(a0),
        0x5F06 => crate::audio::fujo_mix_push(a0, a1, a2),
        0x5F07 => crate::audio::fujo_mix_render(a0, a1, a2),
        0x5F08 => crate::audio::fujo_mix_effect(a0, a1, a2),
        0x5F09 => crate::audio::fujo_mix_status(a0),
        // ---- M51: 显示驱动抽象 ----
        0x5E01 => crate::display::fujo_disp_info(a0),
        0x5E02 => crate::display::fujo_disp_set_backend(a0),
        // ---- M49: 无障碍模式 ----
        0x5D01 => crate::a11y::fujo_a11y_set(a0),
        0x5D02 => crate::a11y::fujo_a11y_get(),
        // ---- M47: VBE 分辨率切换 ----
        0x5C01 => crate::graphics::fujo_vbe_set(a0),
        0x5C02 => crate::graphics::fujo_vbe_actual(a0),
        // ---- M46: 桌面环境 ----
        0x5B01 => crate::desk::fujo_desk_init(),
        0x5B02 => crate::desk::fujo_desk_taskbar(a0),
        0x5B03 => crate::desk::fujo_desk_start(a0, a1),
        0x5B04 => crate::desk::fujo_desk_menu(a0),
        0x5B05 => crate::desk::fujo_desk_pixel(a0, a1),
        // ---- M108: 桌面代理原语 ----
        0x5B10 => crate::desk::desk_launch(a0),
        0x5B11 => crate::desk::desk_state(a0),
        // ---- M45: 终端窗口控件 ----
        0x5A01 => crate::term::fujo_term_put(a0, a1, a2, a3),
        0x5A02 => crate::term::fujo_term_draw(a0, a1, a2),
        0x5A03 => crate::term::fujo_term_pixel(a0, a1),
        // ---- M44: 调色板/主题/图标 ----
        0x5901 => crate::icon::fujo_pal_get(a0),
        0x5902 => crate::icon::fujo_pal_set(a0, a1),
        0x5903 => crate::icon::fujo_theme_apply(a0),
        0x5904 => crate::icon::fujo_icon_draw(a0, a1, a2, a3),
        0x5905 => crate::icon::fujo_icon_pixel(a0, a1),
        // ---- M43: 剪贴板/拖放 ----
        0x5801 => crate::clip::fujo_clip_set(a0, a1),
        0x5802 => crate::clip::fujo_clip_get(a0, a1),
        0x5803 => crate::clip::fujo_clip_len(),
        0x5804 => crate::clip::fujo_dnd_begin(a0 as u32, a1 as u32, a2 as u32),
        0x5805 => crate::clip::fujo_dnd_move(a0 as u32, a1 as u32),
        0x5806 => crate::clip::fujo_dnd_drop(a0 as u32, a1 as u32, a2),
        // ---- M40: IME 骨架 ----
        0x5701 => crate::ime::fujo_ime_begin(),
        0x5702 => crate::ime::fujo_ime_key(a0),
        0x5703 => crate::ime::fujo_ime_candidates(a0, a1),
        0x5704 => crate::ime::fujo_ime_commit(a0),
        0x5705 => crate::ime::fujo_ime_reset(),
        0x5706 => crate::ime::fujo_ime_out(a0),
        // ---- M39: 位图字体 (缩放/字形/backbuffer) ----
        0x5601 => crate::font::fujo_font_text(a0, a1, a2, a3, a4),
        0x5602 => crate::font::fujo_font_pixel(a0, a1),
        0x5603 => crate::font::fujo_font_clear(a0),
        // ---- M37: 消息环 (win32k 等价: 窗口类/窗口/消息队列/z-order) ----
        0x5520 => crate::wmsg::fujo_wm_class(a0),
        0x5521 => crate::wmsg::fujo_wm_create(a0 as u32, a1 as u32, a2 as u32, a3 as u32, a4 as u32),
        0x5522 => crate::wmsg::fujo_wm_getmsg(a0),
        0x5523 => crate::wmsg::fujo_wm_top(a0 as u32),
        0x5524 => crate::wmsg::fujo_wm_remove(a0 as u32),
        0x5525 => crate::wmsg::fujo_wm_move(a0 as u32, a1 as i32, a2 as i32),
        0x5526 => crate::wmsg::fujo_wm_rect(a0 as u32, a1),
        // ---- M36: PS/2 鼠标 (位置/按键/命中测试/焦点) ----
        0x5410 => crate::mouse::fujo_mouse_info(a0),
        0x5411 => crate::mouse::fujo_mouse_rects(a0, a1),
        0x5412 => crate::mouse::fujo_mouse_focus(),
        // ---- M19: 内核对象/句柄表 (统一资源抽象) ----
        // fujo_kobj_create(kind) -> slot
        0x5130 => crate::kobj::fujo_kobj_create(a0),
        // fujo_kobj_free(handle) -> 0
        0x5131 => crate::kobj::fujo_kobj_free(a0),
        // fujo_kobj_info(ptr, n) -> 写入 i32×min(4,n) 计数
        0x5132 => crate::kobj::fujo_kobj_info(a0, a1),
        // ---- darwin BSD 空间 (0x2000000|nr, M6/M29 darwinsubsys) ----
        0x200_0001 => {
            serial::write_str("user : darwin exit(");
            print_dec(a0);
            serial::write_line(") - kernel takeover, M6 verified");
            serial::write_str("timer : pit ticks=");
            print_dec(interrupts::ticks());
            serial::write_line(" (~100 Hz since boot)");
            halt_forever();
        }
        0x200_0003 => crate::vfs::fujo_read(a0, a1, a2), // darwin read(fd, buf, len)
        0x200_0004 => user_write(a0, a1, a2), // darwin write(fd, buf, len)
        0x200_0005 => crate::vfs::fujo_open(a0, a1, a2), // darwin open(path, flags, mode)
        0x200_0006 => crate::vfs::fujo_close(a0), // darwin close(fd)
        0x200_0013 => crate::vfs::fujo_lseek(a0, a1 as i64, a2), // darwin lseek(fd, off, whence)
        // darwin getpid (BSD: 0x2000014)
        0x200_0014 => 2,
        // darwin mmap (197) — darwin flags: MAP_PRIVATE=2 | MAP_ANON=0x1000 -> 内核集
        0x200_00C5 => {
            let flags = if (a3 & 0x1002) == 0x1002 { 2 | 0x20 } else { a3 };
            crate::mem::fujo_mmap(a0, a1, a2, flags, a4, a5)
        }
        _ => {
            // 未实现: 打印一次(带计数), 返回 -ENOSYS
            let c = unsafe {
                let p = core::ptr::addr_of_mut!(spam_count);
                p.write_volatile(p.read_volatile() + 1);
                p.read_volatile()
            };
            if c <= 3 {
                serial::write_str("syscall unimplemented nr=");
                print_dec(nr);
                serial::write_str(" (");
                serial::write_str(name_of(nr).unwrap_or("?"));
                serial::write_line(")");
            }
            -38 // -ENOSYS
        }
    };
    // 返回探针: M9 曾发现 ring3 收到 0x5101/0x5102 返回值与内核不一致 (DEV 项),
    // 此处如实记录内核侧返回值, 便于与用户侧对照。
    if nr == 0x5101 || nr == 0x5102 {
        serial::write_str("dbg  : nr=");
        print_dec(nr);
        serial::write_str(" -> ");
        print_dec(res as u64);
        serial::write_line("");
    }
    res
}

/// 从 LINUX_X64_SUBSET 中查 syscall 名 (M2: 日志可读性)
pub fn name_of(nr: u64) -> Option<&'static str> {
    LINUX_X64_SUBSET.iter().find(|(n, _)| *n as u64 == nr).map(|(_, s)| *s)
}

/// M3: 记录垫片绑定 (由 pe_loader 调用)
pub fn log_shim(dll: &str, func: &str, addr: u64) {
    serial::write_str("shim : ");
    serial::write_str(dll);
    serial::write_str("!");
    serial::write_str(func);
    serial::write_str(" -> trampoline ");
    print_hex(addr);
    serial::write_line("");
}

/// M33: trace 输出 (最近 16 条 ring + 非零计数前 12)。
fn trace_show() -> i64 {
    unsafe {
        serial::write_line("trace : ---- syscall trace ----");
        let n = TRACE_POS.min(16);
        let mut k = 0;
        while k < n {
            let (nr, a0, tk) = TRACE_RING[(TRACE_POS + 64 - n + k) % 64];
            serial::write_str("trace :   nr=");
            print_dec(nr);
            serial::write_str(" a0=");
            print_hex(a0);
            serial::write_str(" tick=");
            print_dec(tk);
            serial::write_line("");
            k += 1;
        }
        serial::write_line("trace : ---- counts (non-zero) ----");
        let mut c = 0usize;
        let mut shown = 0usize;
        while c < 256 && shown < 12 {
            if TRACE_COUNTS[c] > 0 {
                serial::write_str("trace :   nr%256=");
                print_dec(c as u64);
                serial::write_str(" count=");
                print_dec(TRACE_COUNTS[c] as u64);
                serial::write_line("");
                shown += 1;
            }
            c += 1;
        }
        serial::write_line("trace : ---- end ----");
    }
    0
}

/// M3 调试: 十六进制日志 (pe_loader 使用)
pub fn log_hex(v: u64) {
    print_hex(v);
}

/// M36: 十进制日志 (鼠标/其他模块使用)
pub fn debug_dec(v: u64) {
    print_dec(v);
}

/// M66: 十六进制日志 (页缓存诊断)
pub fn debug_hex(v: u64) {
    print_hex(v);
}

fn user_write(fd: u64, ptr: u64, len: u64) -> i64 {    // M15: fd>=3 先走 VFS (内存盘追加); /dev/tty 与 fd<3 走串口
    if len == 0 {
        return 0; // W16: write(fd, NULL, 0) 返回 0
    }
    if fd == 1 && len > 0 {
        crate::desk::tty_feed(ptr, len); // M107: 桌面窗口 TTY 捕获 (串口照走)
    }
    if fd >= 3 {
        if let Some(n) = crate::vfs::file_write(fd, ptr, len) {
            return n;
        }
        // /dev/tty: 落到串口
    }
    let _ = fd;
    // 用户地址范围检查: linux/win 低区 (0x400000..0xC00000, 含堆/mmap 区)
    // 或 darwin 区 (0x100000000..0x100800000, M6 Mach-O 原生地址)。
    // M23b 更新: 0x800000..0xC00000 是 musl/glibc mmap 堆 (busybox stdout
    // 缓冲即落此处; 旧界 0x800000 拒绝 -> write EFAULT, 实证)。
    let in_low = ptr >= 0x400000 && ptr <= 0xC00000;
    let in_high = ptr >= 0x1000000 && ptr <= 0x1080000; // M108: 高地址窗口程序 (user-high.ld)
    let in_darwin = ptr >= 0x100000000 && ptr <= 0x100800000;
    let in_big = ptr >= 0x4400000 && ptr <= 0x8400000; // W41: 大程序装载区
    if !in_low && !in_darwin && !in_high && !in_big {
        serial::write_line("syscall write: bad user pointer");
        return -14; // -EFAULT
    }
    let len = len.min(256) as usize;
    let src = ptr as *const u8;
    let mut line = [0u8; 288];
    let mut n = 0;
    for i in 0..len {
        let b = unsafe { src.add(i).read() };
        line[n] = b;
        n += 1;
    }
    serial::write_str(core::str::from_utf8(&line[..n]).unwrap_or("<non-utf8>"));
    vga::write_str(core::str::from_utf8(&line[..n]).unwrap_or("<non-utf8>"));
    // M45: 终端窗口镜像 (user_write 文本进 80x25 屏)
    crate::term::term_feed(&line[..n]);
    len as i64
}

fn print_dec(v: u64) {
    let mut buf = [0u8; 24];
    let mut i = 24;
    let mut x = v;
    if x == 0 {
        serial::write_str("0");
        return;
    }
    while x > 0 {
        i -= 1;
        buf[i] = b'0' + (x % 10) as u8;
        x /= 10;
    }
    serial::write_str(core::str::from_utf8(&buf[i..]).unwrap());
}

fn print_hex(v: u64) {
    let mut buf = [0u8; 16];
    for i in 0..16 {
        let d = ((v >> (4 * i)) & 0xF) as u8;
        buf[15 - i] = if d < 10 { b'0' + d } else { b'a' + d - 10 };
    }
    serial::write_str("0x");
    serial::write_str(core::str::from_utf8(&buf).unwrap());
    serial::write_str(" ");
}

fn halt_forever() -> ! {
    loop {
        crate::hlt();
    }
}

/// W16: 跳转到用户入口 (runfile 等直接装载路径; 不再返回)。
pub fn run_user(entry: u64) -> ! {
    unsafe {
        fujo_enter_user(entry, 0x5FFFF8);
    }
    loop {
        crate::hlt();
    }
}

/// W16: exit -> 回到 shell 主循环 (重设内核栈 + sti + 跳转; 旧调用链废弃, 不再返回)。
#[no_mangle]
pub extern "C" fn exit_to_shell() -> ! {
    unsafe {
        core::arch::asm!(
            "mov rsp, [rip + {ksp}]",
            "sti",
            "jmp {sh}",
            ksp = sym sys_kernel_rsp,
            sh = sym crate::shell::shell_loop,
            options(noreturn)
        );
    }
}

// ================= M27: winsubsys 垫片分发 (Win64 ABI -> fujo syscall) =================
// 蹦床寄存器映射: rdi=arg1, rsi=arg2, rdx=arg3, rcx=arg4 (原 r9)。
// args 帧: [0]=arg1 [1]=arg2 [2]=arg3 [4]=arg3(原样) [5]=arg4 [6]=arg4(rcx)。
// 第 5 个以上参数在用户栈 [user_rsp_tmp+0x38] 起。
fn shim_dispatch(nr: u64, args: *const u64) -> i64 {
    unsafe {
        let a1 = args.read();
        let a2 = args.add(1).read();
        let a3 = args.add(2).read();
        let a4 = args.add(5).read();
        match nr {
            // ---------- gdi32/user32 (M109: 字体兼容层) ----------
            // Win64 shim 布局: a1=rdi(arg1), a2=rsi(arg2), a3=rdx(arg3), a4=r9(arg4)
            0x5019 => crate::gdi::create_font(a1 as u32, a2 as u32, a3 as u32), // CreateFontA(h,w,weight)
            0x501A => crate::gdi::create_font(a1 as u32, a2 as u32, a3 as u32), // CreateFontW(h,w,weight)
            0x501B => crate::gdi::select_object(a2 as u32), // SelectObject(hdc,h)
            0x501C => crate::gdi::delete_object(a1 as u32), // DeleteObject(h)
            0x501D => crate::gdi::text_out(a2 as u32, a3 as u32, a1, a4 as u32), // TextOutA(hdc,x,y,str,..)
            0x501E => crate::gdi::text_out(a2 as u32, a3 as u32, a1, a4 as u32), // TextOutW(hdc,x,y,wstr)
            0x5020 => crate::gdi::set_bk_mode(a2 as u32), // SetBkMode(hdc,mode)
            0x5021 => crate::gdi::stock_object(a4 as u32), // GetStockObject(id)
            0x5022 => crate::gdi::text_extent(a1, a2 as u32, a3), // GetTextExtentPointA(hdc,str,len,size)
            // 0x501F SetTextColor(hdc,color): a2=color
            0x501F => crate::gdi::set_text_color(a2 as u32), // SetTextColor(hdc,color)
            0x5023 => crate::gdi::get_dc(a1 as u32), // GetDC(hwnd)
            0x5024 => crate::gdi::release_dc(a1 as u32, a2 as u32), // ReleaseDC(hwnd,hdc)
            // ---------- kernel32 ----------
            0x5001 => user_write(a1, a2, a3), // WriteFile(fd, buf, len)
            0x5002 => {
                // ExitProcess(code)
                serial::write_str("user : ExitProcess(");
                print_dec(a1);
                serial::write_line(") - kernel takeover, M3 verified");
                serial::write_str("timer : pit ticks=");
                print_dec(interrupts::ticks());
                serial::write_line(" (~100 Hz since boot)");
                halt_forever()
            }
            0x5003 => {
                // ReadFile(fd, buf, len, read_ptr, ovl) — 写回 *read + BOOL 语义
                let n = crate::vfs::fujo_read(a1, a2, a3);
                if n >= 0 {
                    if a4 != 0 {
                        (a4 as *mut u32).write(n as u32);
                    }
                    1
                } else {
                    0
                }
            }
            0x5004 => crate::vfs::fujo_size(a1), // GetFileSize
            0x5005 => crate::sched::current_task() as i64 + 1, // GetCurrentThreadId
            0x5006 => {
                // CloseHandle(fd) — vfs close 返回 0 成功, 转 BOOL
                if crate::vfs::fujo_close(a1) >= 0 {
                    1
                } else {
                    0
                }
            }
            // GetModuleHandleA(name|NULL) -> 镜像基址 / 假模块句柄
            0x5007 => {
                if a1 == 0 {
                    0x400000i64
                } else {
                    0x4001_0000i64
                }
            }
            // GetProcAddress(h, name) -> 已知垫片返回蹦床地址; 未知 -> 通用 no-op
            0x5008 => {
                let mut nb = [0u8; 64];
                let mut nn = 0usize;
                while nn < 63 {
                    let b = (a2 as *const u8).add(nn).read();
                    if b == 0 {
                        break;
                    }
                    nb[nn] = b;
                    nn += 1;
                }
                let name = core::str::from_utf8(&nb[..nn]).unwrap_or("");
                match crate::pe_loader::shim_resolve_any(name) {
                    Some(idx) => crate::pe_loader::shim_addr(idx) as i64,
                    None => crate::pe_loader::shim_noop_addr() as i64,
                }
            }
            0x5009 => 0x4001_0000i64, // LoadLibraryA -> 假模块句柄 (成功)
            0x500A => 1, // FreeLibrary
            0x500B => unsafe { SHIM_LAST_ERROR as i64 }, // GetLastError (W45: SetLastError 闭环)
            0x500C => 0, // Sleep(ms) — PIT 在跑, no-op
            0x500D => {
                // VirtualProtect(addr, size, prot, old)
                if a4 != 0 {
                    (a4 as *mut u32).write(4);
                }
                1
            }
            0x500E => {
                // VirtualQuery(addr, buf, len) — 填 MEMORY_BASIC_INFORMATION(x64)
                let b = a2 as *mut u64;
                b.add(0).write(a1); // BaseAddress
                b.add(1).write(a1); // AllocationBase
                (b.add(2) as *mut u32).write(4); // AllocationProtect
                b.add(3).write(0x1000u64); // RegionSize
                (b.add(4) as *mut u32).write(0x1000); // State=MEM_COMMIT
                (b.add(5) as *mut u32).write(4); // Protect=PAGE_READWRITE
                (b.add(6) as *mut u32).write(0x20000); // Type=MEM_PRIVATE
                a3 as i64
            }
            0x500F => 0, // TlsGetValue
            0x5010 => 0, // SetUnhandledExceptionFilter
            0x5011 | 0x5012 | 0x5013 | 0x5014 => 0, // CS 操作
            0x5015 => {
                // MultiByteToWideChar(cp, flags, src, srclen, dst, dstlen)
                let src = a2 as *const u8;
                let dst = a4 as *mut u16;
                let limit = if a3 == 0xFFFF_FFFF { 1024usize } else { a3 as usize };
                let mut n = 0usize;
                for i in 0..limit.min(1024) {
                    let c = src.add(i).read();
                    if a3 == 0xFFFF_FFFF && c == 0 {
                        break;
                    }
                    dst.add(n).write(c as u16);
                    n += 1;
                }
                n as i64
            }
            0x5016 => {
                // WideCharToMultiByte(cp, flags, src, srclen, dst, dstlen)
                let src = a2 as *const u16;
                let dst = a4 as *mut u8;
                let st = user_rsp_tmp;
                let _dstlen = (st + 0x40) as *const u64; // 6th arg
                let limit = if a3 == 0xFFFF_FFFF { 1024usize } else { a3 as usize };
                let mut n = 0usize;
                for i in 0..limit.min(1024) {
                    let c = src.add(i).read();
                    if a3 == 0xFFFF_FFFF && c == 0 {
                        break;
                    }
                    dst.add(n).write((c & 0xFF) as u8);
                    n += 1;
                }
                n as i64
            }
            0x5017 => {
                // GetCPInfo(cp, buf) — CPINFO.MaxCharSize=1
                (a2 as *mut u8).write(1);
                1
            }
            // ---- W34: Console/时间/内存/进程 面 ----
            0x5025 => {
                // GetStdHandle(n): -11 stdout / -12 stderr / -10 stdin -> fd 1/2/0
                // (Win64: (DWORD)-11 经 mov ecx 零扩展 -> 按低 32 位截断)
                match (a1 as u32) as i32 {
                    -11 => 1,
                    -12 => 2,
                    _ => 0,
                }
            }
            0x5026 => {
                // WriteConsoleA(h, buf, len, written, reserved)
                let n = user_write(a1, a2, a3);
                if a4 != 0 && n >= 0 {
                    (a4 as *mut u32).write(n as u32);
                }
                if n >= 0 { 1 } else { 0 }
            }
            0x5027 => 1, // GetConsoleMode
            0x5028 => 1, // SetConsoleMode
            0x5029 => {
                // GetTickCount(): ms (PIT 100Hz * 10)
                interrupts::ticks() as i64 * 10
            }
            0x502A => {
                // GetSystemTimeAsFileTime(ptr): u64 100ns since 1601 (boot 相对 + 偏移)
                if a1 != 0 {
                    let v = 0x01D3_0000_0000_0000u64 + interrupts::ticks() * 100_000;
                    (a1 as *mut u64).write(v);
                }
                0
            }
            0x502B => {
                // VirtualAlloc(addr, size, type, prot): addr=0 -> shim 堆; 否则原样
                if a1 == 0 { shim_heap_alloc(a2) as i64 } else { a1 as i64 }
            }
            0x502C => 1, // VirtualFree
            0x502D => 1, // FlushFileBuffers (写直通)
            0x502E => crate::sched::current_task() as i64 + 1, // GetCurrentProcessId
             // ---------- W45: kernel32 深度 +6 ----------
             0x5030 => 0, // GetEnvironmentVariableA v0 空环境
             0x5031 => {
                 let n = a1;
                 let buf = a2;
                 if buf != 0 && n >= 2 {
                     unsafe {
                         (buf as *mut u8).write(b'/');
                         (buf as *mut u8).add(1).write(0);
                     }
                     return 2;
                 }
                 0
             }
             0x5032 => {
                 let fd = a1;
                 let out = a2;
                 if out != 0
                     && !(0x400000..0xC00000).contains(&out)
                     && !(0x4400000..0x8400000).contains(&out)
                 {
                     return 0;
                 }
                 let sz = crate::vfs::fujo_size(fd);
                 if sz < 0 {
                     return 0;
                 }
                 unsafe { (out as *mut u64).write(sz as u64); }
                 1
             }
             0x5033 => {
                 unsafe { SHIM_LAST_ERROR = a1; }
                 0
             }
             0x5034 => crate::interrupts::ticks() as i64, // GetTickCount64
             0x5035 => 0x7F2000i64, // GetCommandLineA: v0 静态串 "app"
            0x5018 => {
                // CreateFileA(name, access, share, sec, disp, flags, tmpl)
                // M30: 统一对象路径 — 反斜杠归一后走 vfs open; 句柄=fd。
                let mut path = [0u8; 64];
                let mut nn = 0usize;
                while nn < 63 {
                    let b = (a1 as *const u8).add(nn).read();
                    if b == 0 {
                        break;
                    }
                    path[nn] = if b == b'\\' { b'/' } else { b };
                    nn += 1;
                }
                path[nn] = 0;
                let ps = core::str::from_utf8(&path[..nn]).unwrap_or("");
                if ps.is_empty() {
                    return -1;
                }
                crate::vfs::fujo_open_name(ps, 0)
            }
            // ---------- msvcrt ----------
            0x5201 => 0, // __C_specific_handler (无 SEH 会走这里)
            0x5202 => 1252, // ___lc_codepage_func
            0x5203 => 1, // ___mb_cur_max_func
            0x5204 => shim_getmainargs(a1, a2, a3), // __getmainargs
            0x5206 => 0x7E0000i64, // __iob_func -> FILE[2]
            0x5207 => 0, // __set_app_type
            0x5208 => 0, // __setusermatherr
            0x5209 => {
                serial::write_line("msv : _amsg_exit() - ignored");
                0
            }
            0x520A => 0, // _cexit
            0x520B => 0x7E0100i64, // _errno -> &int cell
            0x520C => 0, // _initterm (mu 无静态 ctor)
            0x520D | 0x520E => 0, // _lock/_unlock
            0x520F => 0, // atexit
            0x5210 => 3, // abort (不中止内核)
            0x5211 => {
                // calloc(n, size): 清零
                let total = a1.wrapping_mul(a2);
                if total > 0x400000 {
                    return 0;
                }
                let p = shim_heap_alloc(total);
                for i in 0..total {
                    (p as *mut u8).add(i as usize).write(0u8);
                }
                p as i64
            }
            0x5212 => {
                // exit(code) — 内核接管
                serial::write_str("user : msvcrt exit(");
                print_dec(a1);
                serial::write_str(") - kernel takeover, M27 verified (ticks=");
                print_dec(interrupts::ticks());
                serial::write_line(")");
                halt_forever()
            }
            0x5213 => 0, // fflush (无缓冲)
            0x5214 => {
                // fprintf(fp, fmt, a1, a2)
                let mut tmp = [0u64; 4];
                tmp[0] = a3;
                tmp[1] = a4;
                shim_vfmt(a2, tmp.as_ptr(), 2, 0, 0)
            }
            0x5215 => {
                // fputc(c, stream)
                shim_write_chars(core::slice::from_ref(&(a1 as u8)))
            }
            0x5216 => 0, // free
            0x5217 => 0x7E0200i64, // localeconv -> lconv
            0x5218 => shim_heap_alloc(a1) as i64, // malloc
            0x5219 => {
                // memcpy(dst, src, n)
                for i in 0..(a3 as usize).min(0x100000) {
                    let b = (a2 as *const u8).add(i).read();
                    (a1 as *mut u8).add(i).write(b);
                }
                a1 as i64
            }
            0x521A => {
                // puts(str) -> "str\n"
                let mut out = [0u8; 260];
                let mut n = 0usize;
                while n < 255 {
                    let b = (a1 as *const u8).add(n).read();
                    if b == 0 {
                        break;
                    }
                    out[n] = b;
                    n += 1;
                }
                out[n] = b'\n';
                shim_write_chars(&out[..n + 1]);
                1
            }
            0x521B => 0, // setvbuf
            0x521C => 0, // signal
            0x521D => 0x7E0330i64, // strerror
            0x521E => {
                // strlen
                let mut n = 0u64;
                while n < 0x100000 {
                    let b = ((a1 + n) as *const u8).read();
                    if b == 0 {
                        break;
                    }
                    n += 1;
                }
                n as i64
            }
            0x521F => {
                // strncmp(a, b, n)
                let mut r = 0i64;
                for i in 0..a3 {
                    let x = ((a1 + i) as *const u8).read();
                    let y = ((a2 + i) as *const u8).read();
                    if x != y || x == 0 {
                        r = x as i64 - y as i64;
                        break;
                    }
                }
                r
            }
            0x5220 => {
                // vfprintf(fp, fmt, va) — va = mingw ms_va_list (char* 参数数组)
                shim_vfmt(a2, a3 as *const u64, 8, 0, 0)
            }
            0x5221 => {
                // wcslen
                let mut n = 0u64;
                while n < 0x100000 {
                    let c = ((a1 + n * 2) as *const u16).read();
                    if c == 0 {
                        break;
                    }
                    n += 1;
                }
                n as i64
            }
            // ---------- M28: vcruntime 函数面 ----------
            0x5222 => {
                // _snprintf(ptr, n, fmt, ...) — 格式化落用户缓冲
                let st = user_rsp_tmp;
                let mut tmp = [0u64; 5];
                tmp[0] = a4; // vararg 1 (r9)
                let s1p = (st + 0x40) as *const u64; // vararg 2 [rsp+0x28]
                let s2p = (st + 0x48) as *const u64; // vararg 3 [rsp+0x30]
                let s3p = (st + 0x50) as *const u64; // vararg 4 [rsp+0x38]
                let s4p = (st + 0x58) as *const u64; // vararg 5 [rsp+0x40]
                tmp[1] = s1p.read();
                tmp[2] = s2p.read();
                tmp[3] = s3p.read();
                tmp[4] = s4p.read();
                shim_vfmt(a3, tmp.as_ptr(), 5, a1, a2.min(4096))
            }
            0x5223 => {
                // atof(str) — 双精度位模式: 写 cell (专用蹦床 movsd xmm0) + rax
                let bits = shim_parse_double(a1);
                (crate::pe_loader::ATOF_CELL as *mut u64).write(bits);
                bits as i64
            }
            0x5224 => {
                // atoi(str)
                shim_strtol_i(a1) as i64
            }
            0x5225 => {
                // memset(dst, c, n)
                for i in 0..(a3 as usize).min(0x100000) {
                    (a1 as *mut u8).add(i).write(a2 as u8);
                }
                a1 as i64
            }
            0x5226 => shim_qsort(a1, a2, a3, a4), // qsort(base, n, sz, cmp)
            0x5227 => {
                // rand() — xorshift/LCG, 返回 [0, 32767]
                let s = unsafe {
                    let p = core::ptr::addr_of_mut!(SHIM_RAND_SEED);
                    let v = p.read_volatile();
                    let nv = v.wrapping_mul(6364136223846793005).wrapping_add(1);
                    p.write_volatile(nv);
                    (nv >> 33) & 0x7FFF
                };
                s as i64
            }
            0x5228 => {
                // srand(seed)
                unsafe {
                    core::ptr::addr_of_mut!(SHIM_RAND_SEED).write_volatile(a1);
                }
                0
            }
            0x5229 => shim_strtol_any(a1, a2, a3) as i64, // strtol(str, end, base)
            0x522A => shim_strtoul_any(a1, a2, a3) as i64, // strtoul(str, end, base)
            0x522B => {
                // toupper(c)
                if a1 >= b'a' as u64 && a1 <= b'z' as u64 {
                    (a1 - 32) as i64
                } else {
                    a1 as i64
                }
            }
            _ => 0,
        }
    }
}

/// __getmainargs(&argc, &argv, &envp, _dowildcard, &startinfo):
/// 回填用户态 argv0 帧 (0x7E0400 指针区 + 0x7E0420 字符串区)。
fn shim_getmainargs(argc_out: u64, argv_out: u64, envp_out: u64) -> i64 {
    unsafe {
        let mut n = 0usize;
        while n < 63 && pe_argv0[n] != 0 {
            n += 1;
        }
        if n == 0 {
            let bb = b"m27_mingw.exe";
            for k in 0..bb.len() {
                pe_argv0[k] = bb[k];
            }
            pe_argv0[bb.len()] = 0;
            n = bb.len();
        }
        // 字符串 @0x7E0420
        let strp = 0x7E0420u64;
        for k in 0..=n {
            ((strp + k as u64) as *mut u8).write(if k < n { pe_argv0[k] } else { 0 });
        }
        // 指针数组 @0x7E0400: [argv0, 0]
        (0x7E0400u64 as *mut u64).write(strp);
        (0x7E0400u64 as *mut u64).add(1).write(0);
        // envp 空表 @0x7E0408
        (0x7E0408u64 as *mut u64).write(0);
        (argc_out as *mut u32).write(1);
        (argv_out as *mut u64).write(0x7E0400u64);
        (envp_out as *mut u64).write(0x7E0408u64);
    }
    0
}

/// M27: 用户态堆 bump (0x800000 起, 与 mem 堆/mmap 同区)。
static mut SHIM_HEAP: u64 = 0x800000;
fn shim_heap_alloc(n: u64) -> u64 {
    unsafe {
        let n = (n + 15) & !15;
        let p = SHIM_HEAP;
        if p + n > 0xBFFFF0 {
            return 0;
        }
        SHIM_HEAP = p + n;
        p
    }
}

/// 迷你 printf 引擎: fmt 用户指针, argp 用户态 u64 参数数组 (va 或回归值)。
/// out_buf>0: 渲染到用户缓冲 (out_cap 上限, NUL 终结), 返回字符数;
/// out_buf==0: 串口+VGA 输出, 返回输出字节数。
fn shim_vfmt(fmt: u64, argp: *const u64, max_args: usize, out_buf: u64, out_cap: u64) -> i64 {
    let mut out = [0u8; 512];
    let mut on = 0usize;
    let mut ai = 0usize;
    let mut fi = 0usize;
    unsafe {
        loop {
            let mut c = (fmt as *const u8).add(fi).read();
            if c == 0 {
                break;
            }
            if c != b'%' {
                if on < out.len() {
                    out[on] = c;
                    on += 1;
                }
                fi += 1;
                continue;
            }
            fi += 1;
            c = (fmt as *const u8).add(fi).read();
            if c == 0 {
                break;
            }
            if c == b'%' {
                if on < out.len() {
                    out[on] = b'%';
                    on += 1;
                }
                fi += 1;
                continue;
            }
            // 标志/宽度/精度跳过
            while matches!(
                c,
                b'-' | b'0' | b'#' | b' ' | b'+' | b'.' | b'0'..=b'9'
            ) {
                fi += 1;
                c = (fmt as *const u8).add(fi).read();
            }
            let mut wide = false;
            if c == b'l' {
                wide = true;
                fi += 1;
                c = (fmt as *const u8).add(fi).read();
                if c == b'l' {
                    fi += 1;
                    c = (fmt as *const u8).add(fi).read();
                }
            } else if c == b'z' || c == b'j' || c == b't' {
                wide = true;
                fi += 1;
                c = (fmt as *const u8).add(fi).read();
            } else if c == b'h' {
                fi += 1;
                c = (fmt as *const u8).add(fi).read();
                if c == b'h' {
                    fi += 1;
                    c = (fmt as *const u8).add(fi).read();
                }
            }
            let v = if ai < max_args {
                (argp as *const u64).add(ai).read()
            } else {
                0
            };
            ai += 1;
            match c {
                b'd' | b'i' => {
                    let s = if wide {
                        v as i64
                    } else {
                        v as u32 as i32 as i64
                    };
                    on += write_dec_into(&mut out[on..], s);
                }
                b'u' => {
                    let u = if wide { v } else { v as u32 as u64 };
                    on += write_dec_into(&mut out[on..], u as i64);
                }
                b'x' | b'X' => {
                    let u = if wide { v } else { v as u32 as u64 };
                    on += write_hex_into(&mut out[on..], u, c == b'X');
                }
                b'p' => {
                    on += write_hex_prefix_into(&mut out[on..], v);
                }
                b'c' => {
                    if on < out.len() {
                        out[on] = (v & 0xFF) as u8;
                        on += 1;
                    }
                }
                b's' => {
                    let mut k = 0usize;
                    while k < 200 && on < out.len() {
                        let b = (v as *const u8).add(k).read();
                        if b == 0 {
                            break;
                        }
                        out[on] = b;
                        on += 1;
                        k += 1;
                    }
                }
                _ => {
                    if on < out.len() {
                        out[on] = b'%';
                        on += 1;
                    }
                    if on < out.len() {
                        out[on] = c;
                        on += 1;
                    }
                }
            }
            fi += 1;
        }
        if out_buf != 0 {
            // _snprintf 语义: 截断到 cap-1 + NUL; 返回应写字符数
            let cap = (out_cap.min(512)) as usize;
            let n = on.min(cap.saturating_sub(1));
            for i in 0..n {
                (out_buf as *mut u8).add(i).write(out[i]);
            }
            (out_buf as *mut u8).add(n).write(0);
            on as i64
        } else {
            shim_write_chars(&out[..on.min(512)])
        }
    }
}

fn write_dec_into(buf: &mut [u8], v: i64) -> usize {
    let mut n = 0usize;
    let neg = v < 0;
    let mut x = if neg { (v as i64).wrapping_neg() as u64 } else { v as u64 };
    let mut tmp = [0u8; 24];
    let mut ti = 24;
    if x == 0 {
        tmp[23] = b'0';
        ti = 23;
    }
    while x > 0 {
        ti -= 1;
        tmp[ti] = b'0' + (x % 10) as u8;
        x /= 10;
    }
    if neg {
        if n < buf.len() {
            buf[n] = b'-';
            n += 1;
        }
    }
    for k in ti..24 {
        if n < buf.len() {
            buf[n] = tmp[k];
            n += 1;
        }
    }
    n
}

fn write_hex_into(buf: &mut [u8], v: u64, upper: bool) -> usize {
    let mut n = 0usize;
    let mut tmp = [0u8; 16];
    let mut ti = 16;
    let mut x = v;
    if x == 0 {
        tmp[15] = b'0';
        ti = 15;
    }
    while x > 0 {
        ti -= 1;
        let d = (x & 0xF) as u8;
        tmp[ti] = if d < 10 {
            b'0' + d
        } else if upper {
            b'A' + d - 10
        } else {
            b'a' + d - 10
        };
        x >>= 4;
    }
    for k in ti..16 {
        if n < buf.len() {
            buf[n] = tmp[k];
            n += 1;
        }
    }
    n
}

fn write_hex_prefix_into(buf: &mut [u8], v: u64) -> usize {
    let mut n = 0usize;
    let mut h = 0usize;
    if n < buf.len() {
        buf[n] = b'0';
        n += 1;
    }
    if n < buf.len() {
        buf[n] = b'x';
        n += 1;
    }
    if v == 0 {
        if n < buf.len() {
            buf[n] = b'0';
            n += 1;
        }
    } else {
        let mut tmp = [0u8; 16];
        let mut ti = 16;
        let mut x = v;
        while x > 0 {
            ti -= 1;
            let d = (x & 0xF) as u8;
            tmp[ti] = if d < 10 { b'0' + d } else { b'a' + d - 10 };
            x >>= 4;
        }
        while h < 16 - ti {
            if n < buf.len() {
                buf[n] = tmp[ti + h];
                n += 1;
            }
            h += 1;
        }
    }
    n
}

/// 垫片输出: 串口 + VGA 文本
fn shim_write_chars(chars: &[u8]) -> i64 {
    let s = core::str::from_utf8(chars).unwrap_or("<bin>");
    serial::write_str(s);
    vga::write_str(s);
    chars.len() as i64
}

// ================= M28: vcruntime 函数面实现 =================

extern "C" {
    /// 经 Win64 ABI (rcx/rdx 双参, rcx 入口对齐) 调用用户态函数指针
    fn fujo_call_win_fn(f: u64, a: u64, b: u64) -> i64;
}

core::arch::global_asm!(r#"
    .text
    .p2align 4
    .global fujo_call_win_fn
fujo_call_win_fn:
    push rbp
    mov rbp, rsp
    push r12
    push r13
    mov r12, rdi          # f
    mov r13, rdx          # b
    mov rdi, rsi          # a -> arg1
    mov rsi, r13          # b -> arg2
    push rbx
    mov rbx, rsp          # 保存原 rsp (rax 会被用户返回覆盖!)
    and rsp, -16
    sub rsp, 32           # shadow space
    call r12
    mov rsp, rbx          # 恢复栈 (rbx 由用户 callee 保持)
    pop rbx
    pop r13
    pop r12
    pop rbp
    ret
"#);

/// M28: rand/srand 种子
static mut SHIM_RAND_SEED: u64 = 0x253F1;

/// strtol/strtoul/atoi 共用整数解析。
fn shim_strtol_any(strp: u64, endp: u64, base: u64) -> i64 {
    unsafe {
        let mut i = 0usize;
        while i < 256 {
            let b = (strp as *const u8).add(i).read();
            if b == b' ' || b == b'\t' || b == b'\n' || b == b'\r' {
                i += 1;
            } else {
                break;
            }
        }
        let mut neg = false;
        let mut c = (strp as *const u8).add(i).read();
        if c == b'-' {
            neg = true;
            i += 1;
        } else if c == b'+' {
            i += 1;
        }
        let mut base = base;
        let mut c = (strp as *const u8).add(i).read();
        if base == 0 {
            if c == b'0' {
                let c2 = (strp as *const u8).add(i + 1).read();
                if c2 == b'x' || c2 == b'X' {
                    base = 16;
                    i += 2;
                } else {
                    base = 8;
                    i += 1;
                }
            } else {
                base = 10;
            }
        } else if base == 16 && c == b'0' {
            let c2 = (strp as *const u8).add(i + 1).read();
            if c2 == b'x' || c2 == b'X' {
                i += 2;
            }
        }
        let mut val: u64 = 0;
        let mut last = i;
        loop {
            let ch = (strp as *const u8).add(i).read();
            let d = if ch >= b'0' && ch <= b'9' {
                ch - b'0'
            } else if ch >= b'a' && ch <= b'f' {
                ch - b'a' + 10
            } else if ch >= b'A' && ch <= b'F' {
                ch - b'A' + 10
            } else {
                255
            };
            if d >= base as u8 {
                break;
            }
            val = val.wrapping_mul(base).wrapping_add(d as u64);
            last = i + 1;
            i += 1;
        }
        if endp != 0 {
            (endp as *mut u64).write(strp + last as u64);
        }
        if neg && last > 0 {
            (val as i64).wrapping_neg()
        } else {
            val as i64
        }
    }
}

fn shim_strtol_i(strp: u64) -> i64 {
    shim_strtol_any(strp, 0, 10) as i32 as i64
}

/// strtoul: 无符号 (C 语义 "-1" 取反)。
fn shim_strtoul_any(strp: u64, endp: u64, base: u64) -> u64 {
    shim_strtol_any(strp, endp, base) as u64
}

/// atof: 解析 [-]digits[.digits][e[+-]digits], 返回 f64 位模式。
fn shim_parse_double(strp: u64) -> u64 {
    unsafe {
        let mut i = 0usize;
        while i < 256 {
            let b = (strp as *const u8).add(i).read();
            if b == b' ' || b == b'\t' {
                i += 1;
            } else {
                break;
            }
        }
        let mut neg = false;
        let mut c = (strp as *const u8).add(i).read();
        if c == b'-' {
            neg = true;
            i += 1;
        } else if c == b'+' {
            i += 1;
        }
        let mut int_part: f64 = 0.0;
        c = (strp as *const u8).add(i).read();
        while c >= b'0' && c <= b'9' {
            int_part = int_part * 10.0 + (c - b'0') as f64;
            i += 1;
            c = (strp as *const u8).add(i).read();
        }
        let mut frac = 0.0;
        let mut scale = 0.1;
        if c == b'.' {
            i += 1;
            c = (strp as *const u8).add(i).read();
            while c >= b'0' && c <= b'9' {
                frac += (c - b'0') as f64 * scale;
                scale *= 0.1;
                i += 1;
                c = (strp as *const u8).add(i).read();
            }
        }
        let mut v = int_part + frac;
        if c == b'e' || c == b'E' {
            i += 1;
            let mut ec = (strp as *const u8).add(i).read();
            let mut eneg = false;
            if ec == b'-' {
                eneg = true;
                i += 1;
                ec = (strp as *const u8).add(i).read();
            } else if ec == b'+' {
                i += 1;
                ec = (strp as *const u8).add(i).read();
            }
            let mut e = 0u32;
            while ec >= b'0' && ec <= b'9' {
                e = e * 10 + (ec - b'0') as u32;
                i += 1;
                ec = (strp as *const u8).add(i).read();
            }
            let mut m = 1.0f64;
            for _ in 0..e.min(64) {
                m *= 10.0;
            }
            if eneg {
                v /= m;
            } else {
                v *= m;
            }
        }
        if neg {
            v = -v;
        }
        v.to_bits()
    }
}

/// qsort: 插入排序 (n<=64, sz<=1024), 经 Win64 桥调用户 cmp。
fn shim_qsort(base: u64, n: u64, sz: u64, cmp_fn: u64) -> i64 {
    if sz == 0 || n == 0 || n > 64 || sz > 1024 || n * sz > 0x10000 {
        return 0;
    }
    unsafe {
        let mut rounds = 0usize;
        for i in 1..n {
            let mut j = i;
            while j > 0 {
                let p1 = base + (j - 1) * sz;
                let p2 = base + j * sz;
                let r = fujo_call_win_fn(cmp_fn, p1, p2);
                if r <= 0 {
                    break;
                }
                for k in 0..sz as usize {
                    let a = (p1 as *mut u8).add(k).read();
                    let bv = (p2 as *mut u8).add(k).read();
                    (p1 as *mut u8).add(k).write(bv);
                    (p2 as *mut u8).add(k).write(a);
                }
                j -= 1;
                rounds += 1;
                if rounds > 8192 {
                    return 0;
                }
            }
        }
    }
    0
}

/// M22 fork 实现: 从当前 syscall 帧克隆任务。
/// 帧布局 (fujo_syscall_entry push 序, 栈顶->下):
///   [0]=rdi [1]=rsi [2]=rdx [3]=r10 [4]=r8 [5]=r9 [6]=rcx [7]=r11
/// 用户返回 RIP = rcx (syscall 指令后的地址, sysretq 用); RSP = user_rsp_tmp。
/// M112: 0x8105 cap_exec(act, arg0, arg1) —— LAUNCH 需 syscall 现场 (登记父任务),
/// 其余动作由 capability 执行; 均经 exec 槽授权 + 审计。
fn cap_exec(args: *const u64) -> i64 {
    unsafe {
        let act = args.read();
        let a0 = args.add(1).read();
        let a1 = args.add(2).read();
        if !crate::capability::exec_authorized(act) {
            crate::capability::aud_exec(act, 1);
            serial::write_str("cap  : deny exec #");
            print_dec(act);
            serial::write_line("");
            return -1;
        }
        // M116: 地址空间门 —— LAUNCH 入口必须落在当前域允许区域。
        if act == crate::capability::ACT_LAUNCH && !crate::capability::launch_entry_ok(a0) {
            crate::capability::aud_exec(act, 1);
            serial::write_str("cap  : deny LAUNCH entry outside domain as (");
            print_hex(a0);
            serial::write_line(")");
            return -1;
        }
        let rc = if act == crate::capability::ACT_LAUNCH {
            crate::sched::exec_spawn(a0)
        } else {
            crate::capability::fujo_cap_exec(act, a0, a1)
        };
        crate::capability::aud_exec(act, if rc == 0 { 0 } else { 1 });
        rc
    }
}

fn fork_self(args: *const u64) -> i64 {
    unsafe {
        let rip = args.add(6).read(); // rcx = 用户返回地址
        let rsp = user_rsp_tmp;
        let regs8: [u64; 8] = [
            args.add(7).read(), // r11
            args.add(3).read(), // r10
            args.add(5).read(), // r9
            args.add(4).read(), // r8
            args.add(0).read(), // rdi
            args.add(1).read(), // rsi
            args.add(2).read(), // rdx
            args.add(6).read(), // rcx
        ];
        match crate::sched::fork_current(rip, rsp, &regs8) {
            Some(tid) => {
                // 父返回子 tid (=1 首个); 子返回 0 (rax 槽置零)
                serial::write_str("fork : parent returns tid ");
                print_dec(tid as u64);
                serial::write_line("");
                tid as i64
            }
            None => -12, // -ENOMEM (任务表满)
        }
    }
}

/// M21: linuxsubsys syscall 面扩展实现 (~20 个常用)

/// 用户指针区域检查 (linux 低区 0x400000..0xC00000 含堆, + darwin 区)。
fn user_ok(ptr: u64, len: u64) -> bool {
    let in_low = ptr >= 0x400000 && ptr <= 0xC00000;
    let in_darwin = ptr >= 0x100000000 && ptr <= 0x100800000;
    in_low || in_darwin
}

/// stat(path, buf): 真实语义 —— vfs 路径解析 (目录/文件/设备);
/// 未知路径 -ENOENT (W18: busybox ls 靠 st_mode 区分文件或目录)。
fn sys_stat(ptr: u64, buf: u64) -> i64 {
    if !user_ok(buf, 128) {
        return -14; // -EFAULT
    }
    let mut name = [0u8; 256];
    let n = unsafe {
        if !user_ok(ptr, 1) {
            return -14;
        }
        let mut c = 0usize;
        while c < 255 {
            let b = (ptr as *const u8).add(c).read();
            if b == 0 {
                break;
            }
            name[c] = b;
            c += 1;
        }
        c
    };
    let path = match core::str::from_utf8(&name[..n]) {
        Ok(s) => s,
        Err(_) => return -2, // -ENOENT
    };
    let (mode, size) = match crate::vfs::fujo_stat_path(path) {
        Some(m) => m,
        None => return -2, // -ENOENT
    };
    unsafe {
        let s = buf as *mut u64;
        // struct stat (x86_64): st_dev(0) st_ino(8) st_nlink(16) st_mode(24=u32)
        s.add(0).write(1u64); // st_dev
        s.add(1).write(1u64); // st_ino
        s.add(2).write(1u64); // st_nlink
        (s.add(3) as *mut u32).write(mode);
        (s.add(4) as *mut u32).write(1000u32); // uid
        ((s.add(4) as *mut u32).add(1)).write(1000u32); // gid
        s.add(6).write(size); // st_size
    }
    0
}

/// fstat(fd, buf): 目录 fd -> S_IFDIR; 其余沿用 REG 简化。
fn sys_fstat(fd: u64, buf: u64) -> i64 {
    if !user_ok(buf, 128) {
        return -14; // -EFAULT
    }
    let mode = match crate::vfs::fujo_fstat_mode(fd) {
        Some(m) => m,
        None => 0o100644,
    };
    unsafe {
        let s = buf as *mut u64;
        s.add(0).write(1u64);
        s.add(1).write(1u64);
        s.add(2).write(1u64);
        (s.add(3) as *mut u32).write(mode);
        (s.add(4) as *mut u32).write(1000u32);
        ((s.add(4) as *mut u32).add(1)).write(1000u32);
        s.add(6).write(0u64);
    }
    0
}

/// writev(fd, iov, cnt): iovec 数组 [{base,len}..], 逐段写 (串口直通)。
fn sys_writev(fd: u64, iov: u64, cnt: u64) -> i64 {
    if !user_ok(iov, cnt.saturating_mul(16)) || cnt > 64 {
        return -14; // -EFAULT
    }
    let mut total = 0i64;
    unsafe {
        for i in 0..cnt as usize {
            let base = (iov as *const u64).add(i * 2).read();
            let len = (iov as *const u64).add(i * 2 + 1).read();
            let n = user_write(fd, base, len);
            if n < 0 {
                return n;
            }
            total += n;
        }
    }
    total
}

/// nanosleep(req, _rem): v1 模型约束 no-op。
/// 说明: SFMASK=0x200 在 syscall 期间屏蔽 IF, 内核态无法等待 PIT 中断;
/// 真正的睡眠在调度器 wakeup 后实现 (M22+)。此刻返回 0 (立即完成),
/// 用户态忙等/时间推进由 gettimeofday 用户态调用验证。
fn sys_nanosleep(_req: u64) -> i64 {
    0
}

/// uname(buf): utsname 回填 (FujoOS / fujokernel / fujo / x86_64)。
fn sys_uname(buf: u64) -> i64 {
    if !user_ok(buf, 256) {
        return -14;
    }
    unsafe {
        let u = buf as *mut u8;
        let mut off = 0usize;
        for field in [
            b"FujoOS\0".as_slice(),
            b"FujoKernel\0".as_slice(),
            b"0.1.0\0".as_slice(),
            b"FujoOS\0".as_slice(),
            b"x86_64\0".as_slice(),
        ] {
            for &c in field {
                if off < 255 {
                    u.add(off).write(c);
                    off += 1;
                }
            }
        }
    }
    0
}

/// gettimeofday(tv, tz): 单调钟 (PIT ticks/100 = 秒)。
fn sys_gettimeofday(tv: u64, tz: u64) -> i64 {
    let _ = tz;
    if !user_ok(tv, 16) {
        return -14;
    }
    let ticks = crate::interrupts::ticks();
    let sec = ticks / 100;
    let usec = (ticks % 100) * 10000;
    unsafe {
        (tv as *mut u64).write(sec);
        (tv as *mut u64).add(1).write(usec);
    }
    0
}

/// time(ptr): 单调秒 (PIT ticks/100)。
fn sys_time(ptr: u64) -> i64 {
    let ticks = crate::interrupts::ticks();
    let sec = (ticks / 100) as i64;
    if ptr != 0 && user_ok(ptr, 8) {
        unsafe { (ptr as *mut i64).write(sec); }
    }
    sec
}

/// getrandom(buf, len, _flags): PIT 混哈希伪熵 (非加密, 仅时序验证)。
fn sys_getrandom(buf: u64, len: u64) -> i64 {
    if !user_ok(buf, len) {
        return -14;
    }
    let n = len.min(64) as usize;
    unsafe {
        for i in 0..n {
            let tick = crate::interrupts::ticks();
            let x = (tick.wrapping_mul(0x9E37_79B9).rotate_left(13)
                ^ (i as u64).wrapping_mul(0x85EB_CA6B))
                & 0xFF;
            (buf as *mut u8).add(i).write(x as u8);
        }
    }
    n as i64
}

fn dump_hex_bytes(addr: u64, n: usize) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    serial::write_str("test : bytes@0x400000: ");
    unsafe {
        for i in 0..n {
            let b = (addr as *const u8).add(i).read();
            let mut line = [0u8; 3];
            line[0] = HEX[(b >> 4) as usize];
            line[1] = HEX[(b & 0xF) as usize];
            line[2] = b' ';
            serial::write_str(core::str::from_utf8(&line).unwrap());
        }
    }
    serial::write_line("");
}

/// W16: 从用户内存执行 ELF (编译产物直入; 内核暂存用帧分配器, 恒等映射)。
/// 成功不返回 (iretq 切到新入口); 失败返回负 errno。
#[no_mangle]
pub extern "C" fn fujo_exec_mem(src: u64, len: u64) -> i64 {
    const EXEC_STACK: u64 = 0x5FFFF8;
    if !(0x400000..0xC00000).contains(&src) || len == 0 || len > 0x100000 {
        return -14; // -EFAULT
    }
    unsafe {
        let n = ((len as usize + 0xFFF) / 0x1000).max(1);
        let tmp = match crate::mem::alloc_frames_kernel(n) {
            Some(p) => p,
            None => return -12, // -ENOMEM
        };
        for k in 0..len as usize {
            ((tmp as *mut u8).add(k)).write_volatile((src as *const u8).add(k).read_volatile());
        }
        match crate::elf_loader::load_elf(tmp as u32, len as u32) {
            Ok(entry) => {
                serial::write_str("exec : mem-exec entry=0x");
                print_hex(entry);
                serial::write_line("");
                fujo_enter_user(entry, EXEC_STACK);
            }
            Err(e) => {
                serial::write_str("exec : mem-exec FAILED (");
                serial::write_str(e);
                serial::write_line(")");
                return -22; // -EINVAL
            }
        }
    }
    0
}

/// W16: FUJOMULT 多模块解析 (注册表登记所有段; 首段返回为可执行体)。
/// 返回 Some((main_start, main_len)) 当首段有效。
pub fn parse_multi(start: u32, len: u32) -> Option<(u32, u32)> {
    unsafe {
        let mut new_start = start;
        let mut new_len = len;
        let mut have_main = false;
        if !((start as *const u8).read() == b'F'
            && (start as *const u8).add(1).read() == b'U'
            && (start as *const u8).add(2).read() == b'J'
            && (start as *const u8).add(3).read() == b'O'
            && (start as *const u8).add(4).read() == b'M'
            && (start as *const u8).add(5).read() == b'U'
            && (start as *const u8).add(6).read() == b'L'
            && (start as *const u8).add(7).read() == b'T')
        {
            return None;
        }
        let cnt = (start as *const u64).add(1).read();
        for i in 0..cnt.min(8) {
            let e = (start as *const u8).add(16 + i as usize * 32);
            let off = (e as *const u64).read();
            let l = (e as *const u64).add(1).read();
            let name_b = e.add(16);
            let mut nm = [0u8; 16];
            for k in 0..15 {
                nm[k] = name_b.add(k).read();
            }
            let nm_end = nm.iter().position(|&b| b == 0).unwrap_or(15);
            let nm_s = core::str::from_utf8(&nm[..nm_end]).unwrap_or("?");
            crate::vfs::fujo_lib_register(nm_s, start as u64 + off, l);
            if i == 0 {
                new_start = start + off as u32;
                new_len = l as u32;
                have_main = true;
                serial::write_str("multi: exec module '");
                serial::write_str(nm_s);
                serial::write_line("'");
            } else {
                serial::write_str("multi: lib module '");
                serial::write_str(nm_s);
                serial::write_line("' -> /lib");
            }
        }
        if have_main {
            serial::write_str("multi: main @");
            print_hex(new_start as u64);
            print_dec(new_len as u64);
            serial::write_line(" bytes");
            Some((new_start, new_len))
        } else {
            None
        }
    }
}

/// W16: boot 期注册 (引导路由前; shell 阶段注册表即就绪)。
pub fn parse_multi_early() {
    if let Some((s, l, _n)) = module_snapshot() {
        let _ = parse_multi(s, l);
    }
}

/// 进入用户态 (M2: 优先装载 multiboot 模块中的 ELF 文件; 回退内嵌二进制)。
pub fn enter_user_test(mbi: u32) -> ! {
    const LOAD_DEFAULT: u64 = 0x400000;
    // 用户栈初始 RSP: %16==8 (SysV 函数入口约定: clang 生成的 _start 按
    // "call 之后 rsp%16==8" 布局, 若给 0x600000(%16==0) 则 movaps 类 16 对齐
    // 访问错位 -> #GP; M17 res_test 现场 0x400156 movaps 实证)
    const STACK: u64 = 0x5FFFF8;

    let mut load_addr: u64 = LOAD_DEFAULT;
    let mut used_module = false;
    // L1 诊断: 逐系统调用打印
    if cmdline_has_pat(mbi, b"fujo.strace=1") {
        unsafe {
            STRACE = 1;
        }
        serial::write_line("sys : strace on");
    }
    // M23: argv 模式 (busybox 等真 libc 程序需要 argc/argv/envp 栈帧)
    let mut argv_mode = crate::shell::argv_mode();
    // L1: cmdline `fujo.argv=1` -> 为 boot autostart 启用 argc/argv/auxv 帧
    // (原路径只有 shell 的 `os run` 会开; 发行版二进制需要 auxv 才能启动)
    if !argv_mode && cmdline_has_pat(mbi, b"fujo.argv=1") {
        if let Some((_, _, name_ptr)) = unsafe { module_snapshot().or_else(|| find_module(mbi)) } {
            let mut nm = [0u8; 64];
            let mut k = 0usize;
            unsafe {
                while k < 63 && (name_ptr as *const u8).add(k).read() != 0 {
                    nm[k] = (name_ptr as *const u8).add(k).read();
                    k += 1;
                }
            }
            // 取 basename (QEMU 传的是路径)
            let mut st = 0usize;
            for i in 0..k {
                if nm[i] == b'/' || nm[i] == b'\\' {
                    st = i + 1;
                }
            }
            if let Ok(s) = core::str::from_utf8(&nm[st..k]) {
                crate::shell::set_argv0(s);
                crate::shell::set_argv_mode(true);
                argv_mode = true;
                serial::write_str("argv : L1 argv mode on, argv0=");
                serial::write_line(s);
            }
        }
    }

    // ---- M2/M3: 模块装载路径 (ELF 或 PE, 格式嗅探统一路由) ----
    match unsafe { module_snapshot().or_else(|| find_module(mbi)) } {
        Some((mut start, mut len, name_ptr)) => {
            // M17: FUJR 容器嗅探 -> 提取 EMBED 可执行体
            let is_run = unsafe {
                (start as *const u8).read() == b'F'
                    && (start as *const u8).add(1).read() == b'U'
                    && (start as *const u8).add(2).read() == b'J'
                    && (start as *const u8).add(3).read() == b'R'
            };
            // ---- M32: 多模块镜像 (BootMulti) 解析 ----
            let is_multi = unsafe {
                (start as *const u8).read() == b'F'
                    && (start as *const u8).add(1).read() == b'U'
                    && (start as *const u8).add(2).read() == b'J'
                    && (start as *const u8).add(3).read() == b'O'
                    && (start as *const u8).add(4).read() == b'M'
                    && (start as *const u8).add(5).read() == b'U'
                    && (start as *const u8).add(6).read() == b'L'
                    && (start as *const u8).add(7).read() == b'T'
            };
            if is_multi {
                if let Some((ns, nl)) = parse_multi(start, len) {
                    start = ns;
                    len = nl;
                }
            }
            if is_run && !is_multi {
                if let Some((eaddr, elen)) = crate::fujr::load(start as u64, len as u64) {
                    start = eaddr as u32;
                    len = elen as u32;
                    serial::write_line("run  : exec extracted -> format sniff");
                    // W34+: .shell magic (#!fujoshell) -> 脚本解释器 (FUJR 容器内)
                    unsafe {
                        let mut m = [0u8; 11];
                        for k in 0..11usize {
                            m[k] = (eaddr as *const u8).add(k).read();
                        }
                        if &m == b"#!fujoshell" {
                            crate::shell::run_script(eaddr, elen);
                        }
                    }
                }
            }
            // 模块名 (bootloader 提供零终止字符串)
            let mut name = [0u8; 64];
            let mut n = 0usize;
            unsafe {
                while n < 63 {
                    let b = name_ptr.add(n).read();
                    if b == 0 {
                        break;
                    }
                    name[n] = b;
                    n += 1;
                }
            }
            let name_s = core::str::from_utf8(&name[..n]).unwrap_or("?");
            serial::write_str("fmod : '");
            serial::write_str(name_s);
            serial::write_str("' @");
            print_hex(start as u64);
            print_dec(len as u64);
            serial::write_line(" bytes");

            // W45: 模块物理区立即入帧位图保留 (真实基址/长度, 非静态带) ——
            // 64MiB 模块横跨帧 544..831 未预留带, GRUB/ISO 引导会被分配覆写;
            // 含两次守卫页 (identity map len/0x1000+2)。
            crate::mem::reserve_phys_range(start as u64, len as u64 + 0x2000);

            let is_pe = unsafe {
                (start as *const u8).read() == b'M'
                    && (start as *const u8).add(1).read() == b'Z'
            };
            let is_macho = unsafe {
                let m = (start as *const u8).read();
                (m == 0xCF && (start as *const u8).add(1).read() == 0xFA)
                    || (m == 0xFE
                        && (start as *const u8).add(1).read() == 0xED
                        && (start as *const u8).add(2).read() == 0xFA)
            };
            if is_pe {
                serial::write_line("fmt  : PE32+ -> winsubsys (M3)");
                unsafe { crate::pe_loader::install_shims(); }
                // M27: mingw CRT 需要 GS:0x30 (假 TEB) + argv0 (__getmainargs)
                unsafe {
                    user_gs_base = 0x7E1000;
                    let mut nn2 = 0usize;
                    while nn2 < 63 && name[nn2] != 0 {
                        pe_argv0[nn2] = name[nn2];
                        nn2 += 1;
                    }
                    pe_argv0[nn2] = 0;
                }
                // M26: 预打开 /boot/module -> fd=3 (kernel32 文件句柄家族直读)
                crate::vfs::fujo_open_startup_module();
                match crate::pe_loader::load_pe(start, len) {
                    Ok(entry) => {
                        serial::write_str("pexc : ImageBase+EntryPoint=");
                        print_hex(entry);
                        serial::write_line("");
                        load_addr = entry;
                        used_module = true;
                    }
                    Err(e) => {
                        serial::write_str("pexc : FAILED (");
                        serial::write_str(e);
                        serial::write_line(") - fallback...");
                    }
                }
            } else if is_macho {
                serial::write_line("fmt  : Mach-O 64 -> darwinsubsys (M6)");
                match crate::macho_loader::load_macho(start, len) {
                    Ok(entry) => {
                        serial::write_str("mach : LC_SEGMENT_64 mapped, entry=");
                        print_hex(entry);
                        serial::write_line("");
                        load_addr = entry;
                        used_module = true;
                    }
                    Err(e) => {
                        serial::write_str("mach : FAILED (");
                        serial::write_str(e);
                        serial::write_line(") - fallback...");
                    }
                }
            } else {
                serial::write_line("fmt  : ELF64 -> linuxsubsys (M2)");
                // W41: 大程序 (>32MiB) 高区装载 (LOAD_BIG = 0x4400000,
                // initrd 模块放置区之后 — 源/目标无重叠; 小程序维持 0x400000)
                let big = len as u64 > 0x200_0000;
                if big {
                    // W41: 大模块 (0x2C1000..0x42C4000) 横跨 VA 0x800000..0xC00000
                    // (demand-zero 区) 与 0x4000000 后段 —— 整模块区恒等映射
                    // (P|W|PCD) 供 load_elf_rebase_to 读源; big 程序不用用户堆, 无碍。
                    crate::mem::map_phys_identity(start as u64,
                                                  (len as usize) / 0x1000 + 2);
                }
                unsafe { IS_BIG = big; }
                let loader = if big {
                    serial::write_line("fmt  : big-image -> LOAD_BIG 0x4400000");
                    crate::elf_loader::load_elf_rebase_to(start, len, 0x4400000)
                } else {
                    crate::elf_loader::load_elf(start, len)
                };
                match loader {
                    Ok(entry) => {
                        serial::write_str("elfx : entry=");
                        print_hex(entry);
                        serial::write_line("");
                        load_addr = entry;
                        used_module = true;
                    }
                    Err(e) => {
                        serial::write_str("elfx : FAILED (");
                        serial::write_str(e);
                        serial::write_line(") - fallback...");
                    }
                }
            }
        }
        None => {
            serial::write_line("fmod : no boot module (use -initrd) - embedded bin fallback");
        }
    }

    // ---- 回退: 内嵌二进制路径 (M1) ----
    if !used_module {
        let bin: &[u8] = include_bytes!("user_test.bin");
        serial::write_str("test : loading embedded user bin @0x400000 (");
        print_dec(bin.len() as u64);
        serial::write_line(" bytes)");
        unsafe {
            core::ptr::copy_nonoverlapping(bin.as_ptr(), LOAD_DEFAULT as *mut u8, bin.len());
        }
    }

    serial::write_line("test : iretq -> ring3 (cs=0x23 ss=0x1b, linux-x64 ABI)");
    // M13: 双任务模式 (os run threads) —— 装载后克隆第二个任务 (同一镜像, 独立栈)
    // W41: big 模式单任务 (大程序独占大区; 双任务 spawn 于 0x800000 堆区读缺页)
    if crate::sched::multi_task() && !unsafe { IS_BIG } {
        crate::sched::spawn_tasks(load_addr);
    }
    // M23: argv 模式 —— 用户栈顶构造 [argc][argv…][0][envp…][0][auxv…][0]
    // 静态 glibc busybox 初始化需要 auxv (AT_PHDR/AT_PHNUM/AT_ENTRY/AT_RANDOM
    // /AT_SECURE/AT_NULL) 用于 TLS 与 libc 早期状态 (M23 现场: 缺 auxv ->
    // __libc_start_main 读垃圾指针 #PF cr2=rip=0x56198468)。
    let mut user_rsp = STACK;
    if argv_mode {
        // 帧区选 0x5F0000 起 (与 STACK=0x5FFFF8 不冲突的独立区域)
        let sp0 = 0x5F0000u64;
        unsafe {
            // 字符串: 逆序放置 argv 表 (argv[0]="busybox", argv[1..]=命令词)
            let cmdn = crate::shell::argv_cmd_n();
            let cmds = crate::shell::argv_cmd();
            let argc = 1 + cmdn;
            let mut ptrs = [0u64; 16];
            let mut strs: [[u8; 32]; 9] = [[0; 32]; 9];
            {
                let a0 = crate::shell::argv0_bytes();
                for k in 0..a0.len().min(31) {
                    strs[0][k] = a0[k];
                }
            }
            for i in 0..cmdn.min(8) {
                strs[1 + i] = cmds[i];
            }
            for si in 0..argc {
                serial::write_str("argv[");
                let mut tb = [0u8; 4];
                let mut tv = si as u64;
                let mut ti = 4;
                if tv == 0 {
                    tb[3] = b'0';
                }
                while tv > 0 {
                    ti -= 1;
                    tb[ti] = b'0' + (tv % 10) as u8;
                    tv /= 10;
                }
                serial::write_str(core::str::from_utf8(&tb[ti..]).unwrap_or("0"));
                serial::write_str("]='");
                let mut eb = 0usize;
                while eb < 32 && strs[si.min(8)][eb] != 0 {
                    eb += 1;
                }
                serial::write_str(core::str::from_utf8(&strs[si.min(8)][..eb]).unwrap_or("?"));
                serial::write_line("'");
            }
            // 字符串放置: 从后往前 (argv[argc-1] 先放高地址, argv[0] 最后放
            // 最低地址) —— 低地址留给 argv[0], 避免后放置覆盖先放字符串。
            let mut cur = 0x5F0C00u64;
            for a in (0..argc).rev() {
                let s = strs[a.min(8)];
                let mut end = 31;
                while end > 0 && s[end] == 0 {
                    end -= 1;
                }
                let len = (end + 2) as u64; // 字符 + NUL (s[end] 是最后非 0)
                cur -= len;
                for k in 0..len {
                    ((cur + k) as *mut u8).write(s[k as usize]);
                }
                ((cur + len - 1) as *mut u8).write(0u8); // 保障 NUL
                ptrs[a] = cur;
            }
            // 指针区放 0x5F0400: [argc][argv0..][0][envp][0][auxv...][0]
            let argp = 0x5F0400u64;
            let n = argc;
            (argp as *mut u64).write(n as u64); // argc
            for i in 0..n {
                (argp as *mut u64).add(1 + i).write(ptrs[i]);
            }
            (argp as *mut u64).add(1 + n).write(0u64); // argv 结束
            // envp: 空串 + NULL
            (argp as *mut u64).add(2 + n).write(0x5F0100u64);
            (argp as *mut u64).add(3 + n).write(0u64); // envp 结束
            (0x5F0100u64 as *mut u8).write(0u8); // ""
            // auxv (起始于 argp+(4+n)*8)
            // L1: 按实际装载的 ELF 头生成 (原为 musl busybox 硬编码常量)
            let (ax_phdr, ax_phnum, ax_entry) = unsafe {
                let e = crate::elf_loader::LAST_ENTRY;
                let p = crate::elf_loader::LAST_PHDR;
                let n = crate::elf_loader::LAST_PHNUM;
                if e != 0 && p != 0 && n != 0 {
                    (p, n, e)
                } else {
                    (0x400040u64, 9u64, 0x401eb9u64)
                }
            };
            let aux = argp + (4 + n as u64) * 8;
            serial::write_str("auxv: AT_PHDR=");
            print_hex(ax_phdr);
            serial::write_str(" PHNUM=");
            print_dec(ax_phnum);
            serial::write_str(" ENTRY=");
            print_hex(ax_entry);
            serial::write_line("");
            (aux as *mut u64).add(0).write(3u64); // AT_PHDR
            (aux as *mut u64).add(1).write(ax_phdr);
            (aux as *mut u64).add(2).write(4u64); // AT_PHENT
            (aux as *mut u64).add(3).write(56u64);
            (aux as *mut u64).add(4).write(5u64); // AT_PHNUM
            (aux as *mut u64).add(5).write(ax_phnum);
            (aux as *mut u64).add(6).write(9u64); // AT_ENTRY
            (aux as *mut u64).add(7).write(ax_entry);
            (aux as *mut u64).add(8).write(23u64); // AT_SECURE
            (aux as *mut u64).add(9).write(0u64);
            (aux as *mut u64).add(10).write(25u64); // AT_RANDOM
            let rnd = 0x5F0300u64;
            for k in 0..16usize {
                ((rnd + k as u64) as *mut u8).write((0x41 + k) as u8);
            }
            (aux as *mut u64).add(11).write(rnd);
            (aux as *mut u64).add(12).write(6u64); // AT_PAGESZ
            (aux as *mut u64).add(13).write(0x1000u64);
            (aux as *mut u64).add(14).write(0u64); // AT_NULL
            (aux as *mut u64).add(15).write(0u64);
            // stack_end 区清零
            for off in 0x120usize..0x200 {
                ((argp + off as u64) as *mut u8).write(0u8);
            }
            user_rsp = argp;
            serial::write_str("argv : argc=");
            print_dec(n as u64);
            serial::write_str(" stack @");
            print_hex(argp);
            serial::write_line("");
        }
    }
    // M108: 桌面代理模式 —— 代理登记为任务 0 (首次 PIT 用户态中断以真实现场
    // 覆盖 saved_rsp; 此后 0x5B10 生成的窗口程序 = 任务 1+, PIT 双任务轮转)。
    if crate::sched::proxy_mode() {
        crate::sched::spawn_proxy(load_addr);
    }
    unsafe { fujo_enter_user(load_addr, user_rsp) };
    unreachable!()
}

/// M15: 引导模块信息 (addr, len) —— VFS /boot/module 后端。
pub fn boot_module_info(mbi: u32) -> Option<(u64, u64)> {
    unsafe {
        find_module(mbi).map(|(s, l, _)| (s as u64, l as u64))
    }
}

/// M98: 引导期快照值 (安装器使用; 0 = 无模块)。
pub fn boot_module_vals() -> (u64, u64) {
    unsafe { (MOD_SNAP.0 as u64, MOD_SNAP.1 as u64) }
}

/// M108: 是否桌面代理模块 (名称含 "m108_desk" —— QEMU initrd 名字透传)。
pub fn boot_module_is_desk_proxy() -> bool {
    unsafe {
        if MOD_SNAP.2 == 0 {
            return false;
        }
        let mut nb = [0u8; 64];
        let mut n = 0usize;
        while n < 63 {
            let b = (MOD_SNAP.2 as *const u8).add(n).read();
            if b == 0 {
                break;
            }
            nb[n] = b;
            n += 1;
        }
        let s = core::str::from_utf8(&nb[..n]).unwrap_or("");
        s.contains("m108_desk")
    }
}

/// M108: 引导模块存在 (路由判据: 其余模块走 shell 注入路径)。
pub fn boot_module_present() -> bool {
    unsafe { MOD_SNAP.0 != 0 && MOD_SNAP.1 != 0 }
}

/// W30: autostart —— mbi cmdline (flags bit2) 解析 `fujo.run=<name>`;
/// 与 boot 模块名匹配 (contains) 则直启 (真机/虚拟 ISO 无 monitor sendkey)。
pub fn boot_autostart(mbi: u32) -> bool {
    unsafe {
        let m = mbi as *const u32;
        if m.read() & 4 == 0 {
            return false; // 无 cmdline
        }
        let cmd = (m.add(4) as *const u32).read() as *const u8;
        let mut val = [0u8; 48];
        let mut vn = 0usize;
        let mut i = 0usize;
        loop {
            if cmd.add(i).read() == 0 || vn >= val.len() {
                break;
            }
            // 前缀匹配 "fujo.run=" 后取值 (token 至空格/0)
            if i + 9 < 1024
                && cmd.add(i).read() == b'f'
                && cmd.add(i + 1).read() == b'u'
                && cmd.add(i + 2).read() == b'j'
                && cmd.add(i + 3).read() == b'o'
                && cmd.add(i + 4).read() == b'.'
                && cmd.add(i + 5).read() == b'r'
                && cmd.add(i + 6).read() == b'u'
                && cmd.add(i + 7).read() == b'n'
                && cmd.add(i + 8).read() == b'='
            {
                let mut j = i + 9;
                while cmd.add(j).read() != 0
                    && cmd.add(j).read() != b' '
                    && vn < val.len()
                {
                    val[vn] = cmd.add(j).read();
                    vn += 1;
                    j += 1;
                }
                break;
            }
            i += 1;
        }
        // W31: 模块名匹配不可靠 (GRUB/ISO 交付 module cmdline 字段为空,
        // QEMU -initrd 才带文件名) —— cmdline 是权威: fujo.run 存在 + 模块存在 -> 直启。
        if vn == 0 || MOD_SNAP.0 == 0 || MOD_SNAP.1 == 0 {
            return false;
        }
        true
    }
}

/// W46/a6: boot cmdline 是否含 `fujo.qual_test=1` —— 允许用户态质量 syscall
/// (0x8313/0x8314); 未置则一律 -12。与 boot_autostart 同源解析 (mbi flags bit2)。
pub fn boot_qual_test(mbi: u32) -> bool {
    cmdline_has_pat(mbi, b"fujo.qual_test=1")
}

/// L1: 通用 cmdline 子串匹配 (mbi flags bit2)。
pub fn cmdline_has_pat(mbi: u32, pat: &[u8]) -> bool {
    unsafe {
        let m = mbi as *const u32;
        if m.read() & 4 == 0 {
            return false;
        }
        let cmd = (m.add(4) as *const u32).read() as *const u8;
        let mut i = 0usize;
        while i < 1024 {
            if cmd.add(i).read() == 0 {
                return false;
            }
            let mut k = 0usize;
            while k < pat.len() && cmd.add(i + k).read() == pat[k] {
                k += 1;
            }
            if k == pat.len() {
                return true;
            }
            i += 1;
        }
        false
    }
}

// 模块快照: 引导期记录一次 (enter 阶段二次解析 mbi 偶发不可靠 —— 快照绕过)
static mut MOD_SNAP: (u32, u32, u32) = (0, 0, 0);

/// 引导期调用: 记住 (start, len, name_ptr)。
pub fn remember_module(mbi: u32) {
    unsafe {
        if let Some((s, l, n)) = find_module(mbi) {
            MOD_SNAP = (s, l, n as u32);
        }
    }
}

/// W15: 应用管理器 —— 指定运行模块 (shell `os run NAME`: 注册表命中后覆盖快照)。
static mut MOD_OVERRIDE: (u32, u32) = (0, 0);
static mut MOD_OVERRIDE_NAME: [u8; 16] = [0; 16];

pub fn set_module_override(start: u32, len: u32, name: &str) {
    unsafe {
        MOD_OVERRIDE = (start, len);
        for (k, b) in name.as_bytes().iter().take(15).enumerate() {
            MOD_OVERRIDE_NAME[k] = *b;
        }
        MOD_OVERRIDE_NAME[15] = 0;
    }
}

pub fn module_snapshot() -> Option<(u32, u32, *const u8)> {
    unsafe {
        let (os, ol) = MOD_OVERRIDE;
        if os != 0 && ol != 0 {
            return Some((os, ol, MOD_OVERRIDE_NAME.as_ptr()));
        }
        let (s, l, n) = MOD_SNAP;
        if s == 0 || l == 0 || n == 0 {
            return None;
        }
        Some((s, l, n as *const u8))
    }
}

/// 解析 multiboot v1 模块表, 返回 (start, len, name)。
unsafe fn find_module(mbi: u32) -> Option<(u32, u32, *const u8)> {    if mbi == 0 {
        return None;
    }
    let p = mbi as *const u32;
    let flags = p.read();
    if flags & 0x8 == 0 {
        return None;
    }
    let count = p.add(5).read();
    let mods_addr = p.add(6).read();
    if count == 0 || mods_addr == 0 {
        return None;
    }
    let m = mods_addr as *const u32;
    let start = m.read();
    let end = m.add(1).read();
    let name = *m.add(2) as *const u8;
    if end <= start {
        return None;
    }
    Some((start, end - start, name))
}
