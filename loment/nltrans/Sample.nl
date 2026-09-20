program sample

note 自然语言写法的样例程序 (docs/197)。
note **它与同目录的 `Sample.lomt` 是同一个程序** —— 判据比的是"这一份翻出来的 Loment
note 与那一份**逐字节**相同" (docs/188 §7.1.1)。所以改这里就要同时改那一份。

remember LIMIT as 3

to add with a as a whole number and b as a whole number giving a whole number
    give back a plus b
end

to gcd with a as a whole number and b as a whole number giving a whole number
    let x be a
    let y be b
    while y is not 0
        let t be x modulo y
        set x to y
        set y to t
    end
    give back x
end

to sum_to with n as a whole number giving a whole number
    note 类型从字面量来还是从名字来，两条路都在这两句里
    let acc be 0
    for i from 1 to n
        set acc to add of acc, i
    end
    give back acc
end

to tally with n as a whole number giving a whole number
    let v be n
    let steps be 0
    while v is above 0
        set steps to steps plus 1
        set v to v over 2
    end
    give back steps
end

to is_even with n as a whole number giving a truth
    give back (n modulo 2) is 0
end

to label with n as a whole number giving a whole number
    when is_even of n
        give back 1
    otherwise
        give back 0
    end
end

to run giving a whole number
    note 1+2 = 3 | 48 与 18 的最大公约数 = 6 | 8 是偶数 -> 1 | 100 折半到 0 要 7 步
    note | 3 * 10 = 30   =>   3 + 6 + 1 + 7 + 30 = **47**
    let a be sum_to of LIMIT
    let b be gcd of 48, 18
    let c be label of 8
    let d be tally of 100
    let e be LIMIT times 10
    give back a plus b plus c plus d plus e
end

to _start
    let buf be a buffer of 8 bytes
    paint 42 at 0 in buf
    say "sample: "
    say the number run
    say " painted "
    say the number load8 of buf, 0
    say "\n"
    talk to the machine 60 with run, 0, 0
end
