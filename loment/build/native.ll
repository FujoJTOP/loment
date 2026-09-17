; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe


; ---- Loment freestanding 运行时 (M31: 无 libc) ----
define internal i32 @__loment_memcmp(ptr %a, ptr %b, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %cont ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %eq, label %body
body:
  %pa = getelementptr i8, ptr %a, i64 %i
  %pb = getelementptr i8, ptr %b, i64 %i
  %ca = load i8, ptr %pa
  %cb = load i8, ptr %pb
  %ne = icmp ne i8 %ca, %cb
  br i1 %ne, label %diff, label %cont
cont:
  %i1 = add i64 %i, 1
  br label %loop
diff:
  %da = zext i8 %ca to i32
  %db = zext i8 %cb to i32
  %r = sub i32 %da, %db
  ret i32 %r
eq:
  ret i32 0
}

define internal void @__loment_memset(ptr %p, i8 %v, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %q = getelementptr i8, ptr %p, i64 %i
  store i8 %v, ptr %q
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret void
}

define internal void @__loment_memcpy(ptr %d, ptr %s, i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %i1, %body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %end, label %body
body:
  %sp = getelementptr i8, ptr %s, i64 %i
  %b = load i8, ptr %sp
  %dp = getelementptr i8, ptr %d, i64 %i
  store i8 %b, ptr %dp
  %i1 = add i64 %i, 1
  br label %loop
end:
  ret void
}

define internal void @__loment_abort() {
  call void @llvm.trap()
  unreachable
}

declare void @llvm.trap()

; fib -> u32
define i32 @fib(i32 %n) {
entry:
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
entry:
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
  %t8 = icmp eq i32 %t6, 0
  br i1 %t8, label %L5_dtrap, label %L4_dok
L5_dtrap:
  call void @__loment_abort()
  unreachable
L4_dok:
  %t7 = urem i32 %t5, %t6
  br label %L6_dend
L6_dend:
  store i32 %t7, ptr %t.addr
  %t9 = load i32, ptr %y.addr
  store i32 %t9, ptr %x.addr
  %t10 = load i32, ptr %t.addr
  store i32 %t10, ptr %y.addr
  br label %L1_wcond
L3_wend:
  %t11 = load i32, ptr %x.addr
  ret i32 %t11
}
; popcount -> u32
define i32 @popcount(i32 %x) {
entry:
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
  %t6 = icmp eq i32 2, 0
  br i1 %t6, label %L5_dtrap, label %L4_dok
L5_dtrap:
  call void @__loment_abort()
  unreachable
L4_dok:
  %t5 = urem i32 %t4, 2
  br label %L6_dend
L6_dend:
  %t7 = icmp eq i32 %t5, 1
  br i1 %t7, label %L7_then, label %L8_else
L7_then:
  %t8 = load i32, ptr %c.addr
  %t9 = add i32 %t8, 1
  store i32 %t9, ptr %c.addr
  br label %L9_end
L8_else:
  br label %L9_end
L9_end:
  %t10 = load i32, ptr %v.addr
  %t12 = icmp eq i32 2, 0
  br i1 %t12, label %L11_dtrap, label %L10_dok
L11_dtrap:
  call void @__loment_abort()
  unreachable
L10_dok:
  %t11 = udiv i32 %t10, 2
  br label %L12_dend
L12_dend:
  store i32 %t11, ptr %v.addr
  br label %L1_wcond
L3_wend:
  %t13 = load i32, ptr %c.addr
  ret i32 %t13
}
; sum_range -> u32
define i32 @sum_range(i32 %n) {
entry:
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
entry:
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
entry:
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
entry:
  %x.addr = alloca i32
  store i32 %x, ptr %x.addr
  %t1 = load i32, ptr %x.addr
  %t2 = mul i32 %t1, 3
  ret i32 %t2
}
