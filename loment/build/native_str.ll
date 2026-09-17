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

@.str.hello_len.0 = private unnamed_addr constant [5 x i8] c"\68\65\6C\6C\6F"
@.str.same_lit.0 = private unnamed_addr constant [3 x i8] c"\61\62\63"
@.str.same_lit.1 = private unnamed_addr constant [3 x i8] c"\61\62\63"
@.str.same_var.0 = private unnamed_addr constant [3 x i8] c"\61\62\63"
@.str.same_var.1 = private unnamed_addr constant [3 x i8] c"\61\62\64"
@.str.eq_var.0 = private unnamed_addr constant [4 x i8] c"\66\75\6A\6F"
@.str.eq_var.1 = private unnamed_addr constant [4 x i8] c"\66\75\6A\6F"
@.str.diff_len.0 = private unnamed_addr constant [3 x i8] c"\61\62\63"
@.str.diff_len.1 = private unnamed_addr constant [4 x i8] c"\61\62\63\64"
@.str.first_byte.0 = private unnamed_addr constant [1 x i8] c"\5A"
@.str.third_byte.0 = private unnamed_addr constant [3 x i8] c"\61\62\63"

; hello_len -> u32
define i32 @hello_len() {
entry:
  %t1 = getelementptr inbounds [5 x i8], ptr @.str.hello_len.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 5, 1
  %t4 = extractvalue { ptr, i64 } %t3, 1
  %t5 = trunc i64 %t4 to i32
  ret i32 %t5
}
; same_lit -> bool
define i1 @same_lit() {
entry:
  %t1 = getelementptr inbounds [3 x i8], ptr @.str.same_lit.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 3, 1
  %t4 = getelementptr inbounds [3 x i8], ptr @.str.same_lit.1, i64 0, i64 0
  %t5 = insertvalue { ptr, i64 } undef, ptr %t4, 0
  %t6 = insertvalue { ptr, i64 } %t5, i64 3, 1
  %t7 = extractvalue { ptr, i64 } %t3, 0
  %t8 = extractvalue { ptr, i64 } %t3, 1
  %t9 = extractvalue { ptr, i64 } %t6, 0
  %t10 = extractvalue { ptr, i64 } %t6, 1
  %t11 = icmp eq i64 %t8, %t10
  br i1 %t11, label %L1_seq, label %L2_sneq
L1_seq:
  %t12 = call i32 @__loment_memcmp(ptr %t7, ptr %t9, i64 %t8)
  %t13 = icmp eq i32 %t12, 0
  br label %L3_send
L2_sneq:
  br label %L3_send
L3_send:
  %t14 = phi i1 [ %t13, %L1_seq ], [ false, %L2_sneq ]
  ret i1 %t14
}
; same_var -> bool
define i1 @same_var() {
entry:
  %a.addr = alloca { ptr, i64 }
  %b.addr = alloca { ptr, i64 }
  %t1 = getelementptr inbounds [3 x i8], ptr @.str.same_var.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 3, 1
  store { ptr, i64 } %t3, ptr %a.addr
  %t4 = getelementptr inbounds [3 x i8], ptr @.str.same_var.1, i64 0, i64 0
  %t5 = insertvalue { ptr, i64 } undef, ptr %t4, 0
  %t6 = insertvalue { ptr, i64 } %t5, i64 3, 1
  store { ptr, i64 } %t6, ptr %b.addr
  %t7 = load { ptr, i64 }, ptr %a.addr
  %t8 = load { ptr, i64 }, ptr %b.addr
  %t9 = extractvalue { ptr, i64 } %t7, 0
  %t10 = extractvalue { ptr, i64 } %t7, 1
  %t11 = extractvalue { ptr, i64 } %t8, 0
  %t12 = extractvalue { ptr, i64 } %t8, 1
  %t13 = icmp eq i64 %t10, %t12
  br i1 %t13, label %L1_seq, label %L2_sneq
L1_seq:
  %t14 = call i32 @__loment_memcmp(ptr %t9, ptr %t11, i64 %t10)
  %t15 = icmp eq i32 %t14, 0
  br label %L3_send
L2_sneq:
  br label %L3_send
L3_send:
  %t16 = phi i1 [ %t15, %L1_seq ], [ false, %L2_sneq ]
  ret i1 %t16
}
; eq_var -> bool
define i1 @eq_var() {
entry:
  %a.addr = alloca { ptr, i64 }
  %b.addr = alloca { ptr, i64 }
  %t1 = getelementptr inbounds [4 x i8], ptr @.str.eq_var.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 4, 1
  store { ptr, i64 } %t3, ptr %a.addr
  %t4 = getelementptr inbounds [4 x i8], ptr @.str.eq_var.1, i64 0, i64 0
  %t5 = insertvalue { ptr, i64 } undef, ptr %t4, 0
  %t6 = insertvalue { ptr, i64 } %t5, i64 4, 1
  store { ptr, i64 } %t6, ptr %b.addr
  %t7 = load { ptr, i64 }, ptr %a.addr
  %t8 = load { ptr, i64 }, ptr %b.addr
  %t9 = extractvalue { ptr, i64 } %t7, 0
  %t10 = extractvalue { ptr, i64 } %t7, 1
  %t11 = extractvalue { ptr, i64 } %t8, 0
  %t12 = extractvalue { ptr, i64 } %t8, 1
  %t13 = icmp eq i64 %t10, %t12
  br i1 %t13, label %L1_seq, label %L2_sneq
L1_seq:
  %t14 = call i32 @__loment_memcmp(ptr %t9, ptr %t11, i64 %t10)
  %t15 = icmp eq i32 %t14, 0
  br label %L3_send
L2_sneq:
  br label %L3_send
L3_send:
  %t16 = phi i1 [ %t15, %L1_seq ], [ false, %L2_sneq ]
  ret i1 %t16
}
; diff_len -> bool
define i1 @diff_len() {
entry:
  %a.addr = alloca { ptr, i64 }
  %b.addr = alloca { ptr, i64 }
  %t1 = getelementptr inbounds [3 x i8], ptr @.str.diff_len.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 3, 1
  store { ptr, i64 } %t3, ptr %a.addr
  %t4 = getelementptr inbounds [4 x i8], ptr @.str.diff_len.1, i64 0, i64 0
  %t5 = insertvalue { ptr, i64 } undef, ptr %t4, 0
  %t6 = insertvalue { ptr, i64 } %t5, i64 4, 1
  store { ptr, i64 } %t6, ptr %b.addr
  %t7 = load { ptr, i64 }, ptr %a.addr
  %t8 = load { ptr, i64 }, ptr %b.addr
  %t9 = extractvalue { ptr, i64 } %t7, 0
  %t10 = extractvalue { ptr, i64 } %t7, 1
  %t11 = extractvalue { ptr, i64 } %t8, 0
  %t12 = extractvalue { ptr, i64 } %t8, 1
  %t13 = icmp eq i64 %t10, %t12
  br i1 %t13, label %L1_seq, label %L2_sneq
L1_seq:
  %t14 = call i32 @__loment_memcmp(ptr %t9, ptr %t11, i64 %t10)
  %t15 = icmp eq i32 %t14, 0
  br label %L3_send
L2_sneq:
  br label %L3_send
L3_send:
  %t16 = phi i1 [ %t15, %L1_seq ], [ false, %L2_sneq ]
  ret i1 %t16
}
; first_byte -> u32
define i32 @first_byte() {
entry:
  %t1 = getelementptr inbounds [1 x i8], ptr @.str.first_byte.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 1, 1
  %t4 = extractvalue { ptr, i64 } %t3, 0
  %t5 = getelementptr inbounds i8, ptr %t4, i32 0
  %t6 = load i8, ptr %t5
  %t7 = zext i8 %t6 to i32
  ret i32 %t7
}
; third_byte -> u32
define i32 @third_byte() {
entry:
  %s.addr = alloca { ptr, i64 }
  %t1 = getelementptr inbounds [3 x i8], ptr @.str.third_byte.0, i64 0, i64 0
  %t2 = insertvalue { ptr, i64 } undef, ptr %t1, 0
  %t3 = insertvalue { ptr, i64 } %t2, i64 3, 1
  store { ptr, i64 } %t3, ptr %s.addr
  %t4 = load { ptr, i64 }, ptr %s.addr
  %t5 = extractvalue { ptr, i64 } %t4, 0
  %t6 = getelementptr inbounds i8, ptr %t5, i32 2
  %t7 = load i8, ptr %t6
  %t8 = zext i8 %t7 to i32
  ret i32 %t8
}
