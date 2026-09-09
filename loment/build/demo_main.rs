// loment/build/demo_main.rs — 验证壳: 编译并运行 lomentc 转译出的 demo.rs
//
// 用法: rustc -O -o demo_exe demo_main.rs && ./demo_exe
// 期望输出: 55 / 21 / 8 / true / false

include!("demo.rs");

fn main() {
    println!("{}", fib(10));
    println!("{}", gcd(1071, 462));
    println!("{}", popcount(0xF0F0));
    println!("{}", in_domain(4));
    println!("{}", in_domain(5));
    println!("cap={}[{}..{}] revocable={}", CAP_BLK_WRITE_SPACE, CAP_BLK_WRITE_LO, CAP_BLK_WRITE_HI, CAP_BLK_WRITE_REVOCABLE);
    println!("fujr magic={:#X} header={} section={}", MAGIC, HEADER_SIZE, SECTION_SIZE);
    println!("{}", blk_end(Blk { off: 3, len: 5 }));
    println!("{}", mask_low(0xABCD, 8));
    println!("{}", has_flag(0b1000, 3));
    println!("{}", MAX_BLKS);
    println!("{}", sum_array([1, 2, 3, 4]));
    let arr = fill_incr();
    println!("{} {} {} {}", arr[0], arr[1], arr[2], arr[3]);
    println!("{} {} {}", color_code(Color::Red), color_code(Color::Green), color_code(Color::Blue));
    println!("{}", sum_range(10));
    println!("{} {} {}", quadruple(10), max_blocks(), pair_sum(Pair { a: 1, b: 2 }));
    println!("{} {} {}", shape_area(Shape::Circle(2)), shape_area(Shape::Square(3)), shape_area(Shape::Empty));
}
