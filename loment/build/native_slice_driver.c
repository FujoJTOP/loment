/* loment/build/native_slice_driver.c — M3 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_slice_exe.exe native_slice_driver.c native_slice.ll
 * 期望: 10 7
 */
#include <stdio.h>

extern unsigned int call_sum(void);
extern unsigned int call_first(void);

int main(void) {
    printf("%u\n", call_sum());
    printf("%u\n", call_first());
    return 0;
}
