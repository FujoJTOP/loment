program surface

note 完整面的答卷 (docs/197)。**它与同目录的 `Surface.lomt` 是同一个程序** ——
note 判据比的是"这一份翻出来的 Loment 与那一份**逐字节**相同" (docs/188 §7.1.1)，
note 而两份各自真编真跑到**独立推出来的**那对数。

the standard library is not available

use "surface_lib.lomt"

remember LIMIT as 3
remember SECRET as 7, only here

note 声明：结构体 / 枚举 / trait / impl 各一条
a Point has x as a whole number and y as a whole number
a Kind is either Small or Big carrying a whole number
a Sizer can size giving a whole number

a Point can be a Sizer
    to size giving a whole number
        give back the x of self plus the y of self
    end
end

a disk space called slots covers 0 to 4, and it can be taken back
leave out "the network"

note 泛型：`for any T` 之后，类型那一格就能写 T
to largest for any T with a as a T and b as a T giving a T
    when a is above b
        give back a
    end
    give back b
end

to total with e as a Point giving a whole number
    give back the x of e plus the y of e
end

note 看形状：**两条臂** -> match
to score with k as a Kind giving a whole number
    when k looks like a Kind that is Big carrying w
        give back w times 2
    end
    when k looks like a Kind that is Small
        give back 1
    end
end

note 看形状：**一条臂、没有兜底** -> if let
to pick with k as a Kind giving a whole number
    when k looks like a Kind that is Big carrying w
        give back w
    end
    give back 0
end

note 切片是 `[i64]`；"取一个东西"一律 `the <什么> of <东西>`
to sum_all with xs as [i64] giving a whole number
    let acc be 0
    let i be 0
    while i is below the length of xs
        set acc to acc plus the item i of xs
        set i to i plus 1
    end
    give back acc
end

note 定长数组是 `[i64; 3]`
to take with a as [i64; 3] giving a whole number
    give back the item 0 of a
end

note 可改切片是 `mut [i64]`
to fill with xs as mut [i64]
    set the item 0 of xs to 99
end

to guarded with slot as a whole number giving a whole number
    guard the slots space at slot
    give back slot
end

to _start
    let p be a Point with x as 4 and y as 5
    let k be a Kind that is Big carrying 3
    let xs be [0, 0, 0]
    set the item 0 of xs to 1
    set the item 1 of xs to 2
    set the item 2 of xs to 3
    do fill of the changeable run of xs
    let summed be sum_all of the run of xs
    let eight be 8
    let three be 3
    let big be (largest of eight, three) as a whole number
    let s be the size of p
    let g be guarded of 2
    say "surface "
    say the number total of p plus score of k plus pick of k plus summed plus big
    say " "
    say the number s plus g plus SECRET plus take of xs
    say "\n"
    talk to the machine 60 with (triple of big), 0, 0
end
