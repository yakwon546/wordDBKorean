# wordDBKorean
# National institude of Korean Language
# raw DB: 2023 written corpus

| 열 | 의미 |
|----|------|
| `word` | 형태소 표기 |
| `pos` | 품사 태그(Kiwi) |
| `frequency` | 형태소 빈도 |
| `cd` | 맥락 다양성: 등장한 문서(책·기사·방송편) 수 |
| `log_freq` | log10(빈도) |
| `zipf` | Zipf 척도 = log10(빈도/전체토큰×10⁶)+3 |
| `syllable_len` | 음절 길이(완성형 블록 수) |
| `letter_len` | 자모 길이(NFD 분해) |
| `first_syllable` | 첫 음절 |
| `fs_type_freq` | 어두음절 타입 빈도(가) |
| `fs_token_freq` | 어두음절 토큰 빈도(나, 품사 통합) |
| `fs_token_freq_pos` | 어두음절 토큰 빈도(다, 단어 품사 기준) |
| `first_syll_freq` | 첫 음절의 코퍼스 빈도 |
| `final_syll_freq` | 끝 음절의 코퍼스 빈도 |
| `mean_syll_freq` | 음절들의 평균 빈도 |
| `sum_syll_freq` | 음절들의 합 빈도 |
| `n_pos_matched` | 같은 표기로 매칭된 품사 수 |
| `N_syllable` | 음절 이웃 수(Coltheart N) |
| `OLD20` | 직교 레벤슈타인 거리 20 |

- **내용형태소 품사 집합**: `NNG, NNP, NNB, NP, NR, VV, VA, VX, MAG, MAJ, MM, IC, XR`. **파생접미사(XSV·XSA·XSN) 제외**.
- **CD(맥락 다양성)**: 문서 단위: 문어=책
- **N·OLD20**: 한글 음절로만 구성된 단어 대상(혼합 표기·공백 포함 NNP는 제외).
