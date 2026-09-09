; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; fib -> u32
define i32 @fib(i32 %n) {
  %n.addr = alloca i32
  %a.addr = alloca i32
  %b.addr = alloca i32
  %i.addr = alloca i32
  %t.addr = alloca i32
  store i32 %n, ptr %n.addr
  store i32 0, ptr %a.addr
  store i32 1, ptr %b.addr
  store i32 0, ptr %i.addr
  br label %L1_wcond
L1_wcond:
  %t1 = load i32, ptr %i.addr
  %t2 = load i32, ptr %n.addr
  %t3 = icmp ult i32 %t1, %t2
  br i1 %t3, label %L2_wbody, label %L3_wend
L2_wbody:
  %t4 = load i32, ptr %a.addr
  %t5 = load i32, ptr %b.addr
  %t6 = add i32 %t4, %t5
  store i32 %t6, ptr %t.addr
  %t7 = load i32, ptr %b.addr
  store i32 %t7, ptr %a.addr
  %t8 = load i32, ptr %t.addr
  store i32 %t8, ptr %b.addr
  %t9 = load i32, ptr %i.addr
  %t10 = add i32 %t9, 1
  store i32 %t10, ptr %i.addr
  br label %L1_wcond
L3_wend:
  %t11 = load i32, ptr %a.addr
  ret i32 %t11
}
; gcd -> u32
define i32 @gcd(i32 %a, i32 %b) {
  %a.addr = alloca i32
  %b.addr = alloca i32
  %x.addr = alloca i32
  %y.addr = alloca i32
  %t.addr = alloca i32
  store i32 %a, ptr %a.addr
  store i32 %b, ptr %b.addr
  %t1 = load i32, ptr %a.addr
  store i32 %t1, ptr %x.addr
  %t2 = load i32, ptr %b.addr
  store i32 %t2, ptr %y.addr
  br label %L1_wcond
L1_wcond:
  %t3 = load i32, ptr %y.addr
  %t4 = icmp ne i32 %t3, 0
  br i1 %t4, label %L2_wbody, label %L3_wend
L2_wbody:
  %t5 = load i32, ptr %x.addr
  %t6 = load i32, ptr %y.addr
  %t7 = urem i32 %t5, %t6
  store i32 %t7, ptr %t.addr
  %t8 = load i32, ptr %y.addr
  store i32 %t8, ptr %x.addr
  %t9 = load i32, ptr %t.addr
  store i32 %t9, ptr %y.addr
  br label %L1_wcond
L3_wend:
  %t10 = load i32, ptr %x.addr
  ret i32 %t10
}
; popcount -> u32
define i32 @popcount(i32 %x) {
  %x.addr = alloca i32
  %v.addr = alloca i32
  %c.addr = alloca i32
  store i32 %x, ptr %x.addr
  %t1 = load i32, ptr %x.addr
  store i32 %t1, ptr %v.addr
  store i32 0, ptr %c.addr
  br label %L1_wcond
L1_wcond:
  %t2 = load i32, ptr %v.addr
  %t3 = icmp ne i32 %t2, 0
  br i1 %t3, label %L2_wbody, label %L3_wend
L2_wbody:
  %t4 = load i32, ptr %v.addr
  %t5 = urem i32 %t4, 2
  %t6 = icmp eq i32 %t5, 1
  br i1 %t6, label %L4_then, label %L5_else
L4_then:
  %t7 = load i32, ptr %c.addr
  %t8 = add i32 %t7, 1
  store i32 %t8, ptr %c.addr
  br label %L6_end
L5_else:
  br label %L6_end
L6_end:
  %t9 = load i32, ptr %v.addr
  %t10 = udiv i32 %t9, 2
  store i32 %t10, ptr %v.addr
  br label %L1_wcond
L3_wend:
  %t11 = load i32, ptr %c.addr
  ret i32 %t11
}
; sum_range -> u32
define i32 @sum_range(i32 %n) {
  %n.addr = alloca i32
  %s.addr = alloca i32
  %i.addr = alloca i32
  store i32 %n, ptr %n.addr
  store i32 0, ptr %s.addr
  store i32 0, ptr %i.addr
  br label %L1_fcond
L1_fcond:
  %t1 = load i32, ptr %i.addr
  %t2 = load i32, ptr %n.addr
  %t3 = icmp ult i32 %t1, %t2
  br i1 %t3, label %L2_fbody, label %L3_fend
L2_fbody:
  %t4 = load i32, ptr %s.addr
  %t5 = load i32, ptr %i.addr
  %t6 = add i32 %t4, %t5
  store i32 %t6, ptr %s.addr
  %t7 = load i32, ptr %i.addr
  %t8 = add i32 %t7, 1
  store i32 %t8, ptr %i.addr
  br label %L1_fcond
L3_fend:
  %t9 = load i32, ptr %s.addr
  ret i32 %t9
}
; mask_low -> u32
define i32 @mask_low(i32 %x, i32 %n) {
  %x.addr = alloca i32
  %n.addr = alloca i32
  store i32 %x, ptr %x.addr
  store i32 %n, ptr %n.addr
  %t1 = load i32, ptr %x.addr
  %t2 = load i32, ptr %n.addr
  %t3 = shl i32 1, %t2
  %t4 = sub i32 %t3, 1
  %t5 = and i32 %t1, %t4
  ret i32 %t5
}
; in_domain -> bool
define i1 @in_domain(i32 %off) {
  %off.addr = alloca i32
  store i32 %off, ptr %off.addr
  %t1 = load i32, ptr %off.addr
  %t2 = icmp uge i32 %t1, 0
  br i1 %t2, label %L1_sc_rhs, label %L2_sc_short
L1_sc_rhs:
  %t3 = load i32, ptr %off.addr
  %t4 = icmp ule i32 %t3, 4
  br label %L3_sc_end
L2_sc_short:
  br label %L3_sc_end
L3_sc_end:
  %t5 = phi i1 [ %t4, %L1_sc_rhs ], [ false, %L2_sc_short ]
  ret i1 %t5
}
; scaled -> u32
define i32 @scaled(i32 %x) {
  %x.addr = alloca i32
  store i32 %x, ptr %x.addr
  %t1 = load i32, ptr %x.addr
  %t2 = mul i32 %t1, 3
  ret i32 %t2
}
