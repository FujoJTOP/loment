/* loment/build/native_agg_driver.c — M23–M26 的 C 驱动 (docs/145)
 * 编译: clang -O1 -o native_agg_exe.exe native_agg_driver.c native_agg.ll
 * 期望: 8 110 12 9 11 10
 */
#include <stdio.h>

extern unsigned int blk_end(void);
extern unsigned int array_sum(void);
extern unsigned int shape_area(unsigned int, unsigned int);
extern unsigned int short_circuit(unsigned int);

int main(void) {
    printf("%u\n", blk_end());
    printf("%u\n", array_sum());
    printf("%u\n", shape_area(0, 2));
    printf("%u\n", shape_area(1, 3));
    printf("%u\n", short_circuit(5));
    printf("%u\n", short_circuit(0));
    return 0;
}
