# Corpus manifest summary

Total records: **188828**  
Phase 2 conformant: **166750** (88.3%)  
Compile pass: **179024** (94.8%)  
Runtime ran: **563** (0.30%)  

## Complexity tiers

| Tier | Count | % |
|------|-------|---|
| unclassified | 91009 | 48.2% |
| medium | 53650 | 28.4% |
| simple | 20325 | 10.8% |
| complex | 15936 | 8.4% |
| application | 7908 | 4.2% |

## Categories (top 30 by size)

| Category | Total | Conformant | Compiled | Ran |
|---|---|---|---|---|
| BIFI-missing_semicolon | 32994 | 30130 (91%) | 32013 (97%) | 162 |
| MUT-let_to_mut | 32090 | 24510 (76%) | 29469 (92%) | 0 |
| BIFI-undefined_variable | 17015 | 15461 (91%) | 16753 (98%) | 82 |
| BIFI-immutable_reassignment | 16692 | 15150 (91%) | 16354 (98%) | 74 |
| BIFI-type_mismatch | 12575 | 12250 (97%) | 12539 (100%) | 58 |
| MUT-variable_rename | 12426 | 10371 (83%) | 10042 (81%) | 0 |
| FUZZ | 8400 | 8400 (100%) | 8400 (100%) | 0 |
| MUT-type_widening | 7802 | 6211 (80%) | 7601 (97%) | 0 |
| MUT-constant_variation | 5376 | 4257 (79%) | 4912 (91%) | 0 |
| B-CMP | 4436 | 4436 (100%) | 4436 (100%) | 12 |
| A-CND | 3663 | 3281 (90%) | 3663 (100%) | 0 |
| A-MTH | 3502 | 3178 (91%) | 3502 (100%) | 1 |
| A-SRT | 3422 | 2322 (68%) | 3422 (100%) | 2 |
| COMPOSE-NEW | 3093 | 3092 (100%) | 3093 (100%) | 0 |
| A-ARR | 2984 | 2258 (76%) | 2984 (100%) | 0 |
| A-STR | 2070 | 1855 (90%) | 2070 (100%) | 0 |
| COMPOSE-C | 2062 | 2054 (100%) | 2062 (100%) | 0 |
| BIFI-wrong_argument_count | 1823 | 1805 (99%) | 1823 (100%) | 21 |
| COMPOSE-D | 1575 | 1573 (100%) | 1575 (100%) | 11 |
| DOC-EXP | 1488 | 1411 (95%) | 1146 (77%) | 42 |
| ERR-TRIPLE-immutable_reassignment | 1400 | 1275 (91%) | 878 (63%) | 2 |
| ERR-TRIPLE-undefined_variable | 1374 | 1232 (90%) | 948 (69%) | 1 |
| ERR-TRIPLE-wrong_argument_count | 1269 | 1252 (99%) | 694 (55%) | 3 |
| MUT-expression_extraction | 1221 | 1067 (87%) | 1179 (97%) | 0 |
| ERR-TRIPLE-type_mismatch | 1187 | 1143 (96%) | 884 (74%) | 2 |
| ERR-TRIPLE-missing_semicolon | 827 | 745 (90%) | 520 (63%) | 0 |
| EC2-D-DAT | 799 | 799 (100%) | 799 (100%) | 0 |
| EC2-D-CRY | 725 | 724 (100%) | 725 (100%) | 0 |
| EC2-D-CLI | 673 | 673 (100%) | 673 (100%) | 0 |
| EC2-D-WEB | 666 | 666 (100%) | 666 (100%) | 0 |

## Top sources

| Source | Count |
|---|---|
| `bifi-bootstrap` | 81185 |
| `mutation-let_to_mut` | 32615 |
| `mutation-variable_rename` | 12533 |
| `claude-haiku-4-5-20251001` | 8456 |
| `grammar-fuzzer` | 8400 |
| `mutation-type_widening` | 7820 |
| `error-injection-repair` | 6058 |
| `gpt-4.1-mini-2025-04-14` | 5621 |
| `mutation-constant_variation` | 5390 |
| `mechanical-compose` | 4436 |
| `grok-3-mini` | 4291 |
| `deepseek-chat` | 2467 |
| `mechanical-chain` | 1510 |
| `mechanical-accumulator` | 1456 |
| `mutation-expression_extraction` | 1221 |
