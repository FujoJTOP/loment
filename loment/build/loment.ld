/* loment.ld — Loment 独立可执行体链接脚本 (M32)
 *
 * 布局与 kernel/kernel.ld 同族但更简单: 1 MiB 起, 无引导桩。
 * 用法:
 *   clang --target=x86_64-unknown-none -ffreestanding -nostdlib -fuse-ld=lld \
 *         -T loment/build/loment.ld loment/build/native_entry.ll -o native_entry.elf
 */
ENTRY(_start)

SECTIONS
{
    . = 0x00100000;

    .text : ALIGN(16) {
        *(.text .text.*)
    }

    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
    }

    .data : ALIGN(16) {
        *(.data .data.*)
    }

    .bss : ALIGN(16) {
        *(.bss .bss.*)
        *(COMMON)
    }

    /DISCARD/ : {
        *(.comment)
        *(.eh_frame)
    }
}
