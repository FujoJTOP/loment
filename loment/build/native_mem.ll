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

define internal void @__loment_abort() {
  call void @llvm.trap()
  unreachable
}

declare void @llvm.trap()

@__loment_heap = internal global [65536 x i8] zeroinitializer
@__loment_off = internal global i32 0

; heap_roundtrip -> u32
define i32 @heap_roundtrip() {
  %p.addr = alloca ptr
  %s.addr = alloca i32
  %i.addr = alloca i32
  %t1 = load i32, ptr @__loment_off
  %t2 = add i32 %t1, 16
  %t3 = icmp ule i32 %t2, 65536
  br i1 %t3, label %L1_aok, label %L2_aovf
L2_aovf:
  call void @__loment_abort()
  unreachable
L1_aok:
  store i32 %t2, ptr @__loment_off
  %t4 = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %t1
  store ptr %t4, ptr %p.addr
  %t5 = load ptr, ptr %p.addr
  %t6 = getelementptr i8, ptr %t5, i32 0
  store i8 7, ptr %t6
  %t7 = load ptr, ptr %p.addr
  %t8 = getelementptr i8, ptr %t7, i32 1
  store i8 35, ptr %t8
  store i32 0, ptr %s.addr
  store i32 0, ptr %i.addr
  br label %L3_wcond
L3_wcond:
  %t9 = load i32, ptr %i.addr
  %t10 = icmp ult i32 %t9, 2
  br i1 %t10, label %L4_wbody, label %L5_wend
L4_wbody:
  %t11 = load i32, ptr %s.addr
  %t12 = load ptr, ptr %p.addr
  %t13 = load i32, ptr %i.addr
  %t14 = getelementptr i8, ptr %t12, i32 %t13
  %t15 = load i8, ptr %t14
  %t16 = zext i8 %t15 to i32
  %t17 = add i32 %t11, %t16
  store i32 %t17, ptr %s.addr
  %t18 = load i32, ptr %i.addr
  %t19 = add i32 %t18, 1
  store i32 %t19, ptr %i.addr
  br label %L3_wcond
L5_wend:
  %t20 = load ptr, ptr %p.addr
  %t21 = load i32, ptr %s.addr
  ret i32 %t21
}
; wrap_add -> u32
define i32 @wrap_add(i32 %a, i32 %b) {
  %a.addr = alloca i32
  %b.addr = alloca i32
  store i32 %a, ptr %a.addr
  store i32 %b, ptr %b.addr
  %t1 = load i32, ptr %a.addr
  %t2 = load i32, ptr %b.addr
  %t3 = add i32 %t1, %t2
  ret i32 %t3
}
; safe_div -> u32
define i32 @safe_div(i32 %a, i32 %b) {
  %a.addr = alloca i32
  %b.addr = alloca i32
  store i32 %a, ptr %a.addr
  store i32 %b, ptr %b.addr
  %t1 = load i32, ptr %b.addr
  %t2 = icmp eq i32 %t1, 0
  br i1 %t2, label %L1_then, label %L2_else
L1_then:
  ret i32 0
L2_else:
  br label %L3_end
L3_end:
  %t3 = load i32, ptr %a.addr
  %t4 = load i32, ptr %b.addr
  %t6 = icmp eq i32 %t4, 0
  br i1 %t6, label %L5_dtrap, label %L4_dok
L5_dtrap:
  call void @__loment_abort()
  unreachable
L4_dok:
  %t5 = udiv i32 %t3, %t4
  br label %L6_dend
L6_dend:
  ret i32 %t5
}
; atomic_roundtrip -> u32
define i32 @atomic_roundtrip() {
  %p.addr = alloca ptr
  %a.addr = alloca i32
  %b.addr = alloca i32
  %t1 = load i32, ptr @__loment_off
  %t2 = add i32 %t1, 4
  %t3 = icmp ule i32 %t2, 65536
  br i1 %t3, label %L1_aok, label %L2_aovf
L2_aovf:
  call void @__loment_abort()
  unreachable
L1_aok:
  store i32 %t2, ptr @__loment_off
  %t4 = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %t1
  store ptr %t4, ptr %p.addr
  %t5 = load ptr, ptr %p.addr
  %t6 = getelementptr i8, ptr %t5, i32 0
  store i8 0, ptr %t6
  %t7 = load ptr, ptr %p.addr
  %t8 = atomicrmw add ptr %t7, i32 5 seq_cst
  store i32 %t8, ptr %a.addr
  %t9 = load ptr, ptr %p.addr
  %t10 = atomicrmw add ptr %t9, i32 3 seq_cst
  store i32 %t10, ptr %b.addr
  %t11 = load i32, ptr %a.addr
  %t12 = load i32, ptr %b.addr
  %t13 = add i32 %t11, %t12
  %t14 = load ptr, ptr %p.addr
  %t15 = getelementptr i8, ptr %t14, i32 0
  %t16 = load i8, ptr %t15
  %t17 = zext i8 %t16 to i32
  %t18 = add i32 %t13, %t17
  ret i32 %t18
}
