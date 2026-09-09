/* loment/build/native_trait_driver.c — M8 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_trait_exe.exe native_trait_driver.c native_trait.ll
 * 期望: 7 70
 */
#include <stdio.h>

extern unsigned int call_small(void);
extern unsigned int call_big(void);

int main(void) {
    printf("%u\n", call_small());
    printf("%u\n", call_big());
    return 0;
}
