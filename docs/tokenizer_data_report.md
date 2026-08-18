# Tokenizer Training Data Report

## Summary

- **Total unique programs**: 43,658
- **Character count**: min=27, max=3737, mean=336, median=229, p95=942
- **Character coverage**: 92 chars present out of expected set
- **Missing chars**: ["'^'", "'~'"]

## Per-Category Contribution

| Category | Count | % |
|----------|------:|--:|
| FUZZ | 8,400 | 19.2% |
| B-CMP | 4,436 | 10.2% |
| A-CND | 3,663 | 8.4% |
| A-MTH | 3,502 | 8.0% |
| A-SRT | 3,422 | 7.8% |
| A-ARR | 2,984 | 6.8% |
| A-STR | 2,070 | 4.7% |
| COMPOSE-C | 2,062 | 4.7% |
| COMPOSE-D | 1,575 | 3.6% |
| DOC-EXP | 1,488 | 3.4% |
| ERR-TRIPLE-immutable_reassignment | 1,056 | 2.4% |
| ERR-TRIPLE-wrong_argument_count | 1,036 | 2.4% |
| ERR-TRIPLE-undefined_variable | 967 | 2.2% |
| EC2-D-DAT | 799 | 1.8% |
| EC2-D-CRY | 725 | 1.7% |
| ERR-TRIPLE-type_mismatch | 718 | 1.6% |
| EC2-D-CLI | 673 | 1.5% |
| EC2-D-WEB | 666 | 1.5% |
| ERR-TRIPLE-missing_semicolon | 647 | 1.5% |
| EC2-D-FIO | 645 | 1.5% |
| EC2-D-TST | 618 | 1.4% |
| EC2-D-CFG | 588 | 1.3% |
| EC2-D-NET | 584 | 1.3% |
| DOC-ORIG | 329 | 0.8% |
| A-ERR | 4 | 0.0% |
| ERR-TRIPLE-wrong_operator | 1 | 0.0% |

## Character Frequency (top 40)

| Char | Repr | Count |
|------|------|------:|
| t | 't' | 975,316 |
| e | 'e' | 969,048 |
| ; | ';' | 766,479 |
| l | 'l' | 752,691 |
| i | 'i' | 719,847 |
| = | '=' | 634,536 |
| r | 'r' | 634,097 |
| s | 's' | 597,337 |
| ( | '(' | 479,928 |
| ) | ')' | 479,928 |
|   | ' ' | 441,557 |
| u | 'u' | 430,630 |
| f | 'f' | 421,037 |
| n | 'n' | 400,015 |
| a | 'a' | 388,662 |
| { | '{' | 351,568 |
| } | '}' | 351,566 |
| o | 'o' | 331,968 |
| m | 'm' | 323,076 |
| c | 'c' | 255,997 |
| . | '.' | 244,944 |
| < | '<' | 231,282 |
| 0 | '0' | 221,337 |
| p | 'p' | 217,297 |
| : | ':' | 216,593 |
| 1 | '1' | 207,802 |
| d | 'd' | 200,333 |
| 4 | '4' | 200,314 |
| 6 | '6' | 198,177 |
| " | '"' | 194,755 |
| h | 'h' | 156,873 |
| + | '+' | 131,757 |
| g | 'g' | 128,552 |
|  | '\n' | 103,600 |
| v | 'v' | 98,360 |
| 2 | '2' | 83,895 |
| x | 'x' | 74,826 |
| b | 'b' | 66,688 |
| S | 'S' | 60,368 |
| $ | '$' | 58,535 |

## Missing Phase 2 Characters

- `'^'`
- `'~'`

## Extra Characters (outside expected set)

- `'['` — 285 occurrences
- `']'` — 170 occurrences
- `'`'` — 1 occurrences
- `'§'` — 2 occurrences
- `'ÿ'` — 1 occurrences

## Vocabulary Coverage Estimate

- **Total characters**: 14,658,154
- **Unique characters**: 97
- With `character_coverage=1.0`, SentencePiece will cover all 97 unique characters in the training data.
- Recommended vocab size: 8,000–16,000 (standard for domain-specific BPE)
