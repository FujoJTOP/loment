program surface

the standard library is not available

use "surface_lib.lomt"

remember LIMIT as 3
remember SECRET as 7, only here

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

to largest for any T with a as a T and b as a T giving a T
    when a is above b
        give back a
    end
    give back b
end

to total with e as a Point giving a whole number
    give back the x of e plus the y of e
end

to score with k as a Kind giving a whole number
    when k looks like a Kind that is Big carrying w
        give back w times 2
    end
    when k looks like a Kind that is Small
        give back 1
    end
end

to pick with k as a Kind giving a whole number
    when k looks like a Kind that is Big carrying w
        give back w
    end
    give back 0
end

to sum_all with xs as a run of whole numbers giving a whole number
    let acc be 0
    let i be 0
    while i is below the length of xs
        set acc to acc plus item i of xs
        set i to i plus 1
    end
    give back acc
end

to take with a as a list of 3 whole numbers giving a whole number
    give back item 0 of a
end

to fill with xs as a changeable run of whole numbers
    set item 0 of xs to 99
end

to guarded with slot as a whole number giving a whole number
    guard the slots space at slot
    give back slot
end

to _start
    let p be a Point with x as 4 and y as 5
    let k be a Kind that is Big carrying 3
    let xs be the list 0, 0, 0
    set item 0 of xs to 1
    set item 1 of xs to 2
    set item 2 of xs to 3
    do fill of the changeable run of xs
    let summed be sum_all of the run of xs
    let eight be 8
    let three be 3
    let big be (largest of eight, three) as a whole number
    let s be ask p for size
    let g be guarded of 2
    say "surface "
    say the number total of p plus score of k plus pick of k plus summed plus big
    say " "
    say the number s plus g plus SECRET plus take of xs
    say "\n"
    talk to the machine 60 with (triple of big), 0, 0
end
