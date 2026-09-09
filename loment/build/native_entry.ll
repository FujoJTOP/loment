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

; _start -> ()
define void @_start() {
  %x.addr = alloca i32
  store i32 0, ptr %x.addr
  br label %L1_wcond
L1_wcond:
  br i1 1, label %L2_wbody, label %L3_wend
L2_wbody:
  %t1 = load i32, ptr %x.addr
  %t2 = add i32 %t1, 1
  store i32 %t2, ptr %x.addr
  br label %L1_wcond
L3_wend:
  ret void
}
; timer_isr -> interrupt (x86_intrcc)
define x86_intrcc void @timer_isr(ptr byval([8 x i8]) %__frame) {
  %t.addr = alloca i32
  store i32 1, ptr %t.addr
  %t1 = load i32, ptr @__loment_off
  %t2 = add i32 %t1, 1
  %t3 = icmp ule i32 %t2, 65536
  br i1 %t3, label %L1_aok, label %L2_aovf
L2_aovf:
  call void @__loment_abort()
  unreachable
L1_aok:
  store i32 %t2, ptr @__loment_off
  %t4 = getelementptr [65536 x i8], ptr @__loment_heap, i32 0, i32 %t1
  %t5 = load i32, ptr %t.addr
  %t6 = trunc i32 %t5 to i8
  %t7 = getelementptr i8, ptr %t4, i32 0
  store i8 %t6, ptr %t7
  ret void
}
