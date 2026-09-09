/* loment/build/native_gen_driver.c — M6/M7 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_gen_exe.exe native_gen_driver.c native_gen.ll
 * 期望: 7 3 44
 */
#include <stdio.h>

extern unsigned int call_pair_max(void);
extern int call_max_i32(void);
extern unsigned int call_opt(void);

int main(void) {
    printf("%u\n", call_pair_max());
    printf("%d\n", call_max_i32());
    printf("%u\n", call_opt());
    return 0;
}
