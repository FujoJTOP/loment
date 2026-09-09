; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; pack -> u8
define i8 @pack(i8 %v) {
entry:
  %v.addr = alloca i8
  %b.addr = alloca i8
  store i8 %v, ptr %v.addr
  store i8 0, ptr %b.addr
  %t1 = load i8, ptr %b.addr
  %t2 = zext i8 1 to i16
  %t3 = trunc i32 3 to i16
  %t4 = shl i16 %t2, %t3
  %t5 = sub i16 %t4, 1
  %t6 = trunc i16 %t5 to i8
  %t7 = load i8, ptr %v.addr
  %t8 = trunc i32 0 to i8
  %t9 = shl i8 %t6, %t8
  %t10 = xor i8 %t9, -1
  %t11 = and i8 %t1, %t10
  %t12 = and i8 %t7, %t6
  %t13 = shl i8 %t12, %t8
  %t14 = or i8 %t11, %t13
  store i8 %t14, ptr %b.addr
  %t15 = load i8, ptr %b.addr
  %t16 = zext i8 1 to i16
  %t17 = trunc i32 2 to i16
  %t18 = shl i16 %t16, %t17
  %t19 = sub i16 %t18, 1
  %t20 = trunc i16 %t19 to i8
  %t21 = trunc i32 3 to i8
  %t22 = shl i8 %t20, %t21
  %t23 = xor i8 %t22, -1
  %t24 = and i8 %t15, %t23
  %t25 = and i8 1, %t20
  %t26 = shl i8 %t25, %t21
  %t27 = or i8 %t24, %t26
  store i8 %t27, ptr %b.addr
  %t28 = load i8, ptr %b.addr
  ret i8 %t28
}
; unpack -> u32
define i32 @unpack(i8 %b) {
entry:
  %b.addr = alloca i8
  store i8 %b, ptr %b.addr
  %t1 = load i8, ptr %b.addr
  %t2 = zext i8 1 to i16
  %t3 = trunc i32 3 to i16
  %t4 = shl i16 %t2, %t3
  %t5 = sub i16 %t4, 1
  %t6 = trunc i16 %t5 to i8
  %t7 = trunc i32 0 to i8
  %t8 = lshr i8 %t1, %t7
  %t9 = and i8 %t8, %t6
  %t10 = zext i8 %t9 to i32
  ret i32 %t10
}
; roundtrip -> u32
define i32 @roundtrip(i8 %v) {
entry:
  %v.addr = alloca i8
  store i8 %v, ptr %v.addr
  %t1 = load i8, ptr %v.addr
  %t2 = call i8 @pack(i8 %t1)
  %t3 = call i32 @unpack(i8 %t2)
  ret i32 %t3
}
; top_bits -> u32
define i32 @top_bits(i8 %b) {
entry:
  %b.addr = alloca i8
  store i8 %b, ptr %b.addr
  %t1 = load i8, ptr %b.addr
  %t2 = zext i8 1 to i16
  %t3 = trunc i32 2 to i16
  %t4 = shl i16 %t2, %t3
  %t5 = sub i16 %t4, 1
  %t6 = trunc i16 %t5 to i8
  %t7 = trunc i32 3 to i8
  %t8 = lshr i8 %t1, %t7
  %t9 = and i8 %t8, %t6
  %t10 = zext i8 %t9 to i32
  ret i32 %t10
}
