# Corpus Complexity Report

**Total entries analysed:** 197,856
**Generated:** analysis only (no files modified)

## Tier Distribution

| Tier | Count | % | Target % |
|------|------:|--:|----------|
| simple | 47,479 | 24.0% | 40-50% |
| medium | 115,545 | 58.4% | 25-30% |
| complex | 25,034 | 12.7% | 10-15% |
| application | 9,798 | 5.0% | 5-10% |

## Gap Analysis

Entries needed to reach the **midpoint** of each target range,
assuming the total corpus size stays at 197,856.

| Tier | Current | Target (mid) | Delta |
|------|--------:|-------------:|------:|
| simple | 47,479 | 89,035 | +41,556 |
| medium | 115,545 | 54,410 | -61,135 |
| complex | 25,034 | 24,732 | -302 |
| application | 9,798 | 14,839 | +5,041 |

## Average Metrics per Tier

| Tier | token count | num functions | num loops | num conditionals | num let bindings | num imports | num types | nesting depth |
|------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| simple | 9.3 | 1.0 | 0.6 | 0.7 | 2.6 | 0.8 | 0.8 | 2.2 |
| medium | 24.5 | 1.0 | 1.5 | 4.1 | 5.9 | 1.9 | 2.4 | 3.9 |
| complex | 59.4 | 2.0 | 1.4 | 3.8 | 7.2 | 2.2 | 2.8 | 3.7 |
| application | 190.5 | 3.7 | 2.1 | 7.1 | 9.9 | 3.4 | 3.4 | 4.0 |

## Tier Distribution by Category

| Category | simple | medium | complex | application | Total |
|----------|-----:|-----:|-----:|-----:|------:|
| A-ARR | 274 (9%) | 2,763 (87%) | 141 (4%) | 7 (0%) | 3,185 |
| A-CND | 1,396 (33%) | 2,282 (54%) | 430 (10%) | 99 (2%) | 4,207 |
| A-ERR | 0 (0%) | 3 (75%) | 1 (25%) | 0 (0%) | 4 |
| A-MTH | 1,227 (32%) | 2,168 (57%) | 347 (9%) | 48 (1%) | 3,790 |
| A-SRT | 43 (1%) | 2,147 (61%) | 1,061 (30%) | 288 (8%) | 3,539 |
| A-STR | 210 (10%) | 1,473 (69%) | 400 (19%) | 62 (3%) | 2,145 |
| B-CMP | 0 (0%) | 0 (0%) | 599 (14%) | 3,837 (86%) | 4,436 |
| COMPOSE-C | 0 (0%) | 0 (0%) | 2,005 (92%) | 173 (8%) | 2,178 |
| COMPOSE-D | 0 (0%) | 0 (0%) | 217 (14%) | 1,363 (86%) | 1,580 |
| DOC-EXP | 1,583 (25%) | 3,133 (50%) | 1,231 (20%) | 269 (4%) | 6,216 |
| EC2-D-CFG | 41 (7%) | 423 (67%) | 118 (19%) | 45 (7%) | 627 |
| EC2-D-CLI | 108 (14%) | 505 (66%) | 133 (17%) | 15 (2%) | 761 |
| EC2-D-CRY | 138 (17%) | 550 (68%) | 109 (13%) | 14 (2%) | 811 |
| EC2-D-DAT | 7 (1%) | 818 (90%) | 72 (8%) | 15 (2%) | 912 |
| EC2-D-FIO | 36 (5%) | 472 (72%) | 135 (20%) | 16 (2%) | 659 |
| EC2-D-NET | 48 (8%) | 326 (52%) | 182 (29%) | 77 (12%) | 633 |
| EC2-D-TST | 235 (31%) | 492 (64%) | 24 (3%) | 16 (2%) | 767 |
| EC2-D-WEB | 61 (9%) | 378 (54%) | 186 (27%) | 72 (10%) | 697 |
| FUZZ | 5,342 (59%) | 79 (1%) | 3,254 (36%) | 424 (5%) | 9,099 |
| MUT-constant_variation | 1,525 (11%) | 11,164 (79%) | 1,368 (10%) | 140 (1%) | 14,197 |
| MUT-expression_extraction | 408 (31%) | 622 (47%) | 281 (21%) | 25 (2%) | 1,336 |
| MUT-let_to_mut | 5,756 (17%) | 22,459 (67%) | 4,326 (13%) | 997 (3%) | 33,538 |
| MUT-type_widening | 3,276 (39%) | 4,519 (54%) | 444 (5%) | 82 (1%) | 8,321 |
| MUT-variable_rename | 25,765 (27%) | 58,769 (62%) | 7,970 (8%) | 1,714 (2%) | 94,218 |

## Top 20 Most Complex Programs

| # | ID | Category | Tier | Funcs | Loops | Conds | Tokens | Nesting | Score |
|---|-----|----------|------|------:|------:|------:|-------:|--------:|------:|
| 1 | P2-D-D-APP-01460-5c40ec3d | COMPOSE-D | application | 12 | 7 | 99 | 1344 | 5 | 887.4 |
| 2 | P2-D-D-APP-04739-3cedd7d9 | COMPOSE-D | application | 9 | 5 | 97 | 1083 | 9 | 791.3 |
| 3 | P2-D-D-APP-04340-8e33cb3b | COMPOSE-D | application | 10 | 4 | 91 | 1260 | 5 | 772.0 |
| 4 | P2-B-D-CRY-0005v266-0bc0a873 | EC2-D-CRY | application | 1 | 1 | 70 | 801 | 4 | 745.1 |
| 5 | P2-D-D-APP-02352-f9b590c1 | COMPOSE-D | application | 10 | 5 | 89 | 1170 | 5 | 734.0 |
| 6 | P2-D-D-APP-01551-eea08a27 | COMPOSE-D | application | 15 | 8 | 49 | 1187 | 9 | 692.7 |
| 7 | P2-D-D-APP-03505-a49eecf3 | COMPOSE-D | application | 8 | 5 | 82 | 980 | 5 | 684.0 |
| 8 | P2-D-D-APP-03086-a118bd7f | COMPOSE-D | application | 7 | 5 | 82 | 940 | 5 | 670.0 |
| 9 | P2-B-B-CMP-5286-0066687e | B-CMP | application | 3 | 1 | 100 | 921 | 5 | 650.1 |
| 10 | P2-B-B-CMP-5255-f61b126e | B-CMP | application | 3 | 1 | 99 | 896 | 5 | 642.6 |
| 11 | P2-D-D-APP-03149-cea1ed4a | COMPOSE-D | application | 12 | 7 | 49 | 1048 | 8 | 640.8 |
| 12 | P2-D-D-APP-02516-f2117d06 | COMPOSE-D | application | 7 | 4 | 79 | 845 | 5 | 640.5 |
| 13 | P2-B-B-CMP-5282-9cdf25c6 | B-CMP | application | 5 | 4 | 79 | 921 | 5 | 628.1 |
| 14 | P2-A-A-CND-0068v33-7ab06c69 | A-CND | application | 1 | 0 | 102 | 834 | 2 | 609.4 |
| 15 | P2-B-B-CMP-5271-982006b7 | B-CMP | application | 3 | 2 | 85 | 813 | 5 | 585.3 |
| 16 | P2-B-D-WEB-0009v224-64ef92bd | EC2-D-WEB | application | 1 | 0 | 61 | 818 | 62 | 582.8 |
| 17 | P2-B-B-CMP-5259-eda677bd | B-CMP | application | 3 | 2 | 84 | 788 | 5 | 577.8 |
| 18 | P2-B-B-CMP-5279-236e1e8b | B-CMP | application | 3 | 6 | 77 | 875 | 7 | 577.5 |
| 19 | P2-B-B-CMP-5254-163c7c42 | B-CMP | application | 3 | 2 | 84 | 781 | 5 | 577.1 |
| 20 | P2-B-B-CMP-5283-ff560352 | B-CMP | application | 6 | 2 | 77 | 787 | 5 | 572.7 |

