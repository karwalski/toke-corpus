# Corpus Quality Report

**Generated**: 2026-04-09 12:04:06
**Total entries scored**: 197,856
**Fuzzed entries flagged for removal**: 9,099
**Non-fuzzed entries**: 188,757
**Non-fuzzed mean composite score**: 0.6752

## Composite Score Distribution

| Range | Count | % |
|-------|------:|--:|
| 0.0–0.1 | 4,578 | 2.3% # |
| 0.1–0.2 | 1,592 | 0.8% # |
| 0.2–0.3 | 6,580 | 3.3% # |
| 0.3–0.4 | 10,784 | 5.5% ## |
| 0.4–0.5 | 14,400 | 7.3% ### |
| 0.5–0.6 | 24,883 | 12.6% ###### |
| 0.6–0.7 | 48,318 | 24.4% ############ |
| 0.7–0.8 | 21,856 | 11.0% ##### |
| 0.8–0.9 | 55,233 | 27.9% ############# |
| 0.9–1.0 | 9,632 | 4.9% ## |

## Per-Category Average Scores

| Category | Count | Avg Score |
|----------|------:|----------:|
| A-ARR | 3,185 | 0.6850 |
| A-CND | 4,207 | 0.5107 |
| A-ERR | 4 | 0.5000 |
| A-MTH | 3,790 | 0.5361 |
| A-SRT | 3,539 | 0.7483 |
| A-STR | 2,145 | 0.6507 |
| B-CMP | 4,436 | 0.8427 |
| COMPOSE-C | 2,178 | 0.8475 |
| COMPOSE-D | 1,580 | 0.8714 |
| DOC-EXP | 6,216 | 0.6410 |
| EC2-D-CFG | 627 | 0.6517 |
| EC2-D-CLI | 761 | 0.6056 |
| EC2-D-CRY | 811 | 0.5887 |
| EC2-D-DAT | 912 | 0.6687 |
| EC2-D-FIO | 659 | 0.6670 |
| EC2-D-NET | 633 | 0.6370 |
| EC2-D-TST | 767 | 0.5454 |
| EC2-D-WEB | 697 | 0.6358 |
| FUZZ | 9,099 | 0.1880 |
| MUT-constant_variation | 14,197 | 0.6857 |
| MUT-expression_extraction | 1,336 | 0.6078 |
| MUT-let_to_mut | 33,538 | 0.7105 |
| MUT-type_widening | 8,321 | 0.6481 |
| MUT-variable_rename | 94,218 | 0.6651 |

## Bottom 10% — Candidates for Removal

Threshold composite score: **0.3500**
Entries at or below threshold: **19,785**

| Category | Count in bottom 10% |
|----------|--------------------:|
| FUZZ | 7,369 |
| MUT-variable_rename | 6,320 |
| MUT-let_to_mut | 1,643 |
| A-CND | 1,052 |
| A-MTH | 852 |
| MUT-type_widening | 809 |
| DOC-EXP | 493 |
| MUT-constant_variation | 392 |
| EC2-D-TST | 169 |
| A-ARR | 121 |
| EC2-D-CRY | 111 |
| A-STR | 98 |
| EC2-D-CLI | 94 |
| MUT-expression_extraction | 90 |
| EC2-D-WEB | 51 |
| EC2-D-CFG | 33 |
| A-SRT | 31 |
| EC2-D-NET | 26 |
| EC2-D-FIO | 23 |
| EC2-D-DAT | 7 |
| A-ERR | 1 |

## Fuzzed Programs (flagged for removal)

- **Count**: 9,099
- **Mean composite**: 0.1880
- **Min composite**: 0.0300
- **Max composite**: 0.4500

## Entries with Error Handling (for story 10.2)

- **Count**: 81,067 (41.0%)

| Category | Count |
|----------|------:|
| MUT-variable_rename | 39,557 |
| MUT-let_to_mut | 17,518 |
| MUT-constant_variation | 5,648 |
| MUT-type_widening | 3,679 |
| B-CMP | 2,282 |
| A-SRT | 1,749 |
| A-ARR | 1,746 |
| COMPOSE-C | 1,218 |
| A-MTH | 1,109 |
| A-STR | 1,079 |
| A-CND | 1,071 |
| COMPOSE-D | 720 |
| DOC-EXP | 674 |
| EC2-D-DAT | 547 |
| EC2-D-CLI | 432 |
| EC2-D-TST | 378 |
| EC2-D-CRY | 370 |
| EC2-D-FIO | 328 |
| EC2-D-WEB | 275 |
| EC2-D-NET | 249 |
| EC2-D-CFG | 240 |
| MUT-expression_extraction | 196 |
| A-ERR | 2 |

## Entries with Stdlib Usage (for story 10.3)

- **Count**: 118,338 (59.8%)

| Category | Count |
|----------|------:|
| MUT-variable_rename | 54,807 |
| MUT-let_to_mut | 24,266 |
| MUT-constant_variation | 7,719 |
| MUT-type_widening | 5,366 |
| DOC-EXP | 4,947 |
| B-CMP | 3,606 |
| A-SRT | 3,003 |
| A-ARR | 2,787 |
| A-STR | 1,778 |
| COMPOSE-C | 1,700 |
| A-MTH | 1,677 |
| COMPOSE-D | 1,580 |
| EC2-D-DAT | 860 |
| A-CND | 623 |
| EC2-D-FIO | 549 |
| EC2-D-WEB | 526 |
| EC2-D-CFG | 510 |
| EC2-D-CLI | 471 |
| EC2-D-TST | 439 |
| EC2-D-NET | 422 |
| EC2-D-CRY | 411 |
| MUT-expression_extraction | 291 |
