# Corpus Deduplication Report

**Date:** 2026-04-09 12:06
**Runtime:** 92.8s

## Summary

| Metric | Value |
|--------|-------|
| Total files before | 197,856 |
| Total files after  | 97,819 |
| Files removed      | 100,037 |
| Reduction          | 50.6% |
| Unique structural hashes | 93,728 |

## Before/After Counts by Category

| Category | Before | After | Removed | Reduction |
|----------|--------|-------|---------|-----------|
| A-ARR | 3,185 | 2,984 | 201 | 6.3% |
| A-CND | 4,207 | 3,663 | 544 | 12.9% |
| A-ERR | 4 | 4 | 0 | 0.0% |
| A-MTH | 3,790 | 3,502 | 288 | 7.6% |
| A-SRT | 3,539 | 3,422 | 117 | 3.3% |
| A-STR | 2,145 | 2,070 | 75 | 3.5% |
| B-CMP | 4,436 | 4,436 | 0 | 0.0% |
| COMPOSE-C | 2,178 | 2,062 | 116 | 5.3% |
| COMPOSE-D | 1,580 | 1,575 | 5 | 0.3% |
| DOC-EXP | 6,216 | 1,488 | 4,728 | 76.1% |
| EC2-D-CFG | 627 | 588 | 39 | 6.2% |
| EC2-D-CLI | 761 | 673 | 88 | 11.6% |
| EC2-D-CRY | 811 | 725 | 86 | 10.6% |
| EC2-D-DAT | 912 | 799 | 113 | 12.4% |
| EC2-D-FIO | 659 | 645 | 14 | 2.1% |
| EC2-D-NET | 633 | 584 | 49 | 7.7% |
| EC2-D-TST | 767 | 618 | 149 | 19.4% |
| EC2-D-WEB | 697 | 666 | 31 | 4.4% |
| FUZZ | 9,099 | 8,400 | 699 | 7.7% |
| MUT-constant_variation | 14,197 | 5,376 | 8,821 | 62.1% |
| MUT-expression_extraction | 1,336 | 1,221 | 115 | 8.6% |
| MUT-let_to_mut | 33,538 | 32,090 | 1,448 | 4.3% |
| MUT-type_widening | 8,321 | 7,802 | 519 | 6.2% |
| MUT-variable_rename | 94,218 | 12,426 | 81,792 | 86.8% |

## Largest Duplicate Groups (Top 20)

| Rank | Hash | Group Size | Kept | Sample Source (first 80 chars) |
|------|------|-----------|------|-------------------------------|
| 1 | `a7154e997709a182` | 101 | 2 | `m=isPrintable;f=isPrintable(lhs:i64;rhs:i64):bool{let range=@(32;33;34;35;36;37;` |
| 2 | `d4e58e4535162851` | 101 | 2 | `m=isPrintable;f=isPrintable(lhs:i64;rhs:i64):bool{let printable=mut.false;let co` |
| 3 | `481e549010d73f32` | 99 | 2 | `m=largestcoin;f=largestCoin(n:i64):i64{if(n<1){<0};if(n=1){<1};if(n=2){<1};if(n=` |
| 4 | `fe3728934f535c46` | 69 | 2 | `m=isAlphaNum;f=isAlphaNum(c:$str):bool{let num1=mut.0 as u64;let num2=mut.0 as u` |
| 5 | `565fc4db4e9811ea` | 67 | 1 | `m=isalphanum;f=isAlphaNum(c:Str):bool{let result=mut.false;let lhs=@(97;98;99;10` |
| 6 | `3604aea74c495f5f` | 50 | 1 | `m=sample;
f=bad(): i64 { < 1 };` |
| 7 | `aa7fcbe9c232cdea` | 47 | 1 | `m=findMaxIndex;f=findMaxIndex(list:@i64):i64{let x=mut.0;let y=mut.list.get(0);l` |
| 8 | `3f0b3d3fccd39560` | 42 | 2 | `m=zodiac;f=zodiac(p:i64;q:i64):$str{let result=mut."";if(p=1){if(q<20){result="C` |
| 9 | `5df52f5610f3fd30` | 40 | 1 | `m=example;
i=crypto:std.crypto;
i=str:std.str;

f=hashstrop():$str{
  let hash=c` |
| 10 | `01b8766919ba0039` | 38 | 2 | `m=foldsumfunc;f=foldSum(arr:@u64;init:u64):u64{let acc=mut.init;lp(let i=0;i<arr` |
| 11 | `441f2a7848243095` | 38 | 2 | `m=zodiac;f=zodiac(num1:i64;num2:i64):$str{let sign=mut."";if(num1=3){if(num2>20)` |
| 12 | `d2a8e855339ea696` | 38 | 1 | `m=work;
i=str:std.str;
f=example():void{
  let b = str.bytes("abc");
};` |
| 13 | `c9a865dd978d1e33` | 38 | 1 | `m=errs;
i=file:std.file;
f=readsizefn(path:$str):i64{
  let res=file.read(path);` |
| 14 | `69bfebfeb5f152cf` | 37 | 1 | `m=mod1;
f=fn2():$str{
<"b0ea7"
};` |
| 15 | `0fd3d0b47edd413e` | 36 | 1 | `m=datapipeline;

i=file:std.file;
i=str:std.str;
i=log:std.log;

t=$err{code:i64` |
| 16 | `d0c107019723ecde` | 35 | 1 | `m=mod1;
f=fn2():f64{
<44.37
};` |
| 17 | `dedf725eede46784` | 34 | 2 | `m=isvalidday;f=isValidDay(x:i64;y:i64):bool{let daysInMonth=mut.0;if(x=1){daysIn` |
| 18 | `862c21e367ab9922` | 33 | 2 | `m=isequal;f=isEqual(num1:u64;num2:u64):bool{if(num1=num2){<true};<false};` |
| 19 | `70c811d7382773b6` | 33 | 2 | `m=islwr;f=isLowerCase(lhs:$str;rhs:$str):bool{let arr=@(97;98;99;100;101;102;103` |
| 20 | `860021b3f7cfff12` | 33 | 1 | `m=test;

i=file:std.file;
i=str:std.str;
i=log:std.log;

t=$err{code:i64};

f=co` |

## Distribution of Kept Variants per Structural Hash

| Kept per group | Number of groups |
|---------------|-----------------|
| 1 | 89,654 |
| 2 | 4,057 |
| 3 | 17 |

