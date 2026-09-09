/* loment/build/native_str_driver.c — M1/M2 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_str_exe.exe native_str_driver.c native_str.ll
 * 期望: 5 1 0 1 0 90 99
 */
#include <stdio.h>

extern unsigned int hello_len(void);
extern _Bool same_lit(void);
extern _Bool same_var(void);
extern _Bool eq_var(void);
extern _Bool diff_len(void);
extern unsigned int first_byte(void);
extern unsigned int third_byte(void);

int main(void) {
    printf("%u\n", hello_len());
    printf("%d\n", same_lit() ? 1 : 0);
    printf("%d\n", same_var() ? 1 : 0);
    printf("%d\n", eq_var() ? 1 : 0);
    printf("%d\n", diff_len() ? 1 : 0);
    printf("%u\n", first_byte());
    printf("%u\n", third_byte());
    return 0;
}
