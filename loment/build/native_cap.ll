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

@__loment_audit = internal global [16 x i64] zeroinitializer

@__loment_caps = internal constant [1 x { i64, i64, i64, i64 }] [{ i64, i64, i64, i64 } { i64 1163935908, i64 0, i64 4, i64 1 }]

; write -> u32
define i32 @write(i32 %slot) {
  %slot.addr = alloca i32
  store i32 %slot, ptr %slot.addr
  %t1 = load i32, ptr %slot.addr
  %t2 = zext i32 %t1 to i64
  %t3 = icmp uge i64 %t2, 0
  %t4 = icmp ule i64 %t2, 4
  %t5 = and i1 %t3, %t4
  br i1 %t5, label %L1_gok, label %L2_gtrap
L2_gtrap:
  call void @__loment_abort()
  unreachable
L1_gok:
  %t6 = getelementptr [16 x i64], ptr @__loment_audit, i32 0, i32 0
  %t7 = load i64, ptr %t6
  %t8 = add i64 %t7, 1
  store i64 %t8, ptr %t6
  %t9 = load i32, ptr %slot.addr
  ret i32 %t9
}
; ok -> u32
define i32 @ok() {
  %t1 = call i32 @write(i32 3)
  ret i32 %t1
}
; literal_ok -> u32
define i32 @literal_ok() {
  %t1 = zext i32 2 to i64
  %t2 = icmp uge i64 %t1, 0
  %t3 = icmp ule i64 %t1, 4
  %t4 = and i1 %t2, %t3
  br i1 %t4, label %L1_gok, label %L2_gtrap
L2_gtrap:
  call void @__loment_abort()
  unreachable
L1_gok:
  %t5 = getelementptr [16 x i64], ptr @__loment_audit, i32 0, i32 0
  %t6 = load i64, ptr %t5
  %t7 = add i64 %t6, 1
  store i64 %t7, ptr %t5
  ret i32 2
}
