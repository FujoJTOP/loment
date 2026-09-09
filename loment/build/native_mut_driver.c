/* loment/build/native_mut_driver.c — M4 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_mut_exe.exe native_mut_driver.c native_mut.ll
 * 期望: 27 10
 */
#include <stdio.h>

extern unsigned int call_fill(void);
extern unsigned int call_read_only(void);

int main(void) {
    printf("%u\n", call_fill());
    printf("%u\n", call_read_only());
    return 0;
}
