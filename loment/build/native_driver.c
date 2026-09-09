/* loment/build/native_driver.c — 原生后端 M0 的 C 驱动 (docs/144)
 *
 * 只做两件事: 声明 native.lomt 里的函数, 打印结果供逐值比对。
 * 编译: clang -O1 -o native_exe.exe native_driver.c native.ll
 * 期望: 55 21 8 45 205 1 21
 */
#include <stdio.h>

extern unsigned int fib(unsigned int);
extern unsigned int gcd(unsigned int, unsigned int);
extern unsigned int popcount(unsigned int);
extern unsigned int sum_range(unsigned int);
extern unsigned int mask_low(unsigned int, unsigned int);
extern _Bool in_domain(unsigned int);
extern unsigned int scaled(unsigned int);

int main(void) {
    printf("%u\n", fib(10));
    printf("%u\n", gcd(1071, 462));
    printf("%u\n", popcount(0xF0F0u));
    printf("%u\n", sum_range(10));
    printf("%u\n", mask_low(0xABCDu, 8));
    printf("%d\n", in_domain(4) ? 1 : 0);
    printf("%u\n", scaled(7));
    return 0;
}
