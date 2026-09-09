/* loment/build/native_res_driver.c — M9/M10 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_res_exe.exe native_res_driver.c native_res.ll
 * 期望: 6 99
 */
#include <stdio.h>

extern unsigned int call_ok(void);
extern unsigned int call_err(void);

int main(void) {
    printf("%u\n", call_ok());
    printf("%u\n", call_err());
    return 0;
}
